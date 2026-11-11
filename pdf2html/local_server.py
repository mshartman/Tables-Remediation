import os
import re
import hashlib
import hmac
import json
import shutil
import threading
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from reader_view import render_reader, suggest_headings
from openai_descriptions import describe_openai
from layout_pipeline import build_layout_preview, refresh_layout_review, preview_path
from usage_analytics import JobTimer, correlation, enabled as analytics_enabled, provider_metrics, record_event
from result_storage import get_store, JOB_ID, artifact_name, expired, retention, StorageUnavailable, ResultExpired, RetentionError

from bs4 import BeautifulSoup
from bs4 import NavigableString
from bs4 import Tag
from flask import Flask, Response, abort, g, jsonify, render_template, request, send_file, send_from_directory, url_for
from werkzeug.http import dump_options_header
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from content_accessibility_utility_on_aws.api import (
    audit_html_accessibility,
    convert_pdf_to_html,
    generate_remediation_report,
    save_usage_data,
)
from content_accessibility_utility_on_aws.remediate.api import remediate_html_accessibility
from content_accessibility_utility_on_aws.remediate.remediation_strategies.local_table_extractor import (
    extract_pdf_tables_to_html,
)
from content_accessibility_utility_on_aws.utils.usage_tracker import SessionUsageTracker


BASE_DIR = Path(__file__).resolve().parent
RUNS_DIR = Path(os.environ.get("ACCESSIBLE_HTML_RUNS_DIR", BASE_DIR / "local_runs"))
MAX_UPLOAD_SIZE_MB = int(os.environ.get("LOCAL_SERVER_MAX_UPLOAD_MB", "100"))
ALLOWED_ORIGIN = os.environ.get("ACCESSIBLE_HTML_ALLOWED_ORIGIN", "")
PROCESS_LOCK = threading.Lock()

TABLE_ISSUE_TYPES = {
    "missing-table-headers",
    "missing-header-scope",
    "complex-table-no-ids",
    "table-missing-headers",
    "table-missing-scope",
    "table-missing-caption",
    "table-missing-thead",
    "table-missing-tbody",
    "table-irregular-headers",
    "table-missing-headers-id",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE_MB * 1024 * 1024
app.config["PREFERRED_URL_SCHEME"] = "https"
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


def _bool_from_form(name: str, default: bool = False) -> bool:
    return _bool_value(request.form.get(name), default)


def _bool_value(raw_value, default: bool = False) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).lower() in {"1", "true", "yes", "on"}


def _default_value(env_name: str, fallback: str = "") -> str:
    return os.environ.get(env_name, fallback)


def _is_table_issue(issue: dict) -> bool:
    issue_type = (issue.get("type") or "").lower()
    return issue_type in TABLE_ISSUE_TYPES or issue_type.startswith("table-") or issue_type.startswith("table_")


def _reset_usage_tracker() -> None:
    SessionUsageTracker._instance = None


def _ensure_run_dirs(job_dir: Path) -> tuple[Path, Path]:
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return input_dir, output_dir


def _job_status_path(job_dir: Path) -> Path:
    return job_dir / "job_status.json"


def _write_job_status(job_dir: Path, payload: dict) -> None:
    _job_status_path(job_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_job_status(job_dir: Path) -> dict | None:
    status_path = _job_status_path(job_dir)
    if not status_path.exists():
        return None
    try:
        value = json.loads(status_path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (ValueError, OSError):
        return None


def _iter_job_statuses() -> list[tuple[str, dict]]:
    if not RUNS_DIR.exists():
        return []

    jobs = []
    for job_dir in sorted((p for p in RUNS_DIR.iterdir() if p.is_dir()), reverse=True):
        if not JOB_ID.fullmatch(job_dir.name):
            continue
        status = _read_job_status(job_dir)
        if status:
            jobs.append((job_dir.name, status))
    return jobs


def _find_existing_job(transaction_number: str, workflow='full', source_sha256=None) -> tuple[str, dict] | None:
    if not transaction_number:
        return None
    store = get_store()
    if store:
        stored = store.find(transaction_number, workflow, source_sha256)
        if stored:
            return stored

    for job_id, status in _iter_job_statuses():
        if status.get("transaction_number") != transaction_number:
            continue
        if status.get('remediation_mode', 'full') != workflow:
            continue
        if store and not hmac.compare_digest(status.get('source_sha256', ''), source_sha256 or ''):
            continue
        if expired(status):
            raise ResultExpired()

        job_dir = RUNS_DIR / job_id
        job_status = status.get("status")

        if job_status == "completed":
            if status.get('storage') == 's3':
                # Never fall back to disk if a committed remote result disappeared.
                raise StorageUnavailable('Stored results need recovery. Please retry later.')
            preview_filename = status.get("preview_filename")
            if not preview_filename:
                continue

            preview_path = job_dir / Path(preview_filename)
            if not preview_path.exists():
                continue

            return job_id, status

        if job_status in {"queued", "processing"}:
            return job_id, status

    return None


def _result_record(job_id):
    records = g.setdefault('stored_results', {})
    if job_id not in records:
        store = get_store()
        records[job_id] = store.get_record(job_id) if store else None
    return records[job_id]


def _available_status(job_id):
    record = _result_record(job_id)
    if record:
        return record['status']
    status = _read_job_status(RUNS_DIR / job_id)
    if status and expired(status):
        raise ResultExpired()
    if status and status.get('storage') == 's3':
        raise StorageUnavailable('Stored results need recovery. Please retry later.')
    return status


def _public_status(status):
    return {key: value for key, value in status.items()
            if key not in {'transaction_number', 'zip_path', 'traceback', 'source_sha256'}}


def _request_retention(payload=None):
    if not get_store():
        return {}
    return retention(request.form.get('delivery_at') or (payload or {}).get('delivery_at'))


def _complete_job(job_dir, **changes):
    status = _read_job_status(job_dir) or {}
    status.update(changes, status='completed', updated_at=_utc_now())
    store = get_store()
    if store:
        status = store.publish(job_dir, status)
    _write_job_status(job_dir, status)
    return status


def _cleanup_completed_scratch(job_dir):
    """Only newly completed, committed S3 jobs; never touch the source PDF path."""
    status = _read_job_status(job_dir) or {}
    if status.get('status') != 'completed' or status.get('storage') != 's3':
        return
    try:
        if (not JOB_ID.fullmatch(job_dir.name) or job_dir.is_symlink() or
                job_dir.resolve().parent != RUNS_DIR.resolve()):
            return
        for child in list(job_dir.iterdir()):
            if child.name not in {'input', 'output', status.get('zip_filename')}:
                continue
            if child.is_symlink() or not child.resolve().is_relative_to(job_dir.resolve()):
                continue
            if child.is_dir():
                shutil.rmtree(child)
            elif child.is_file():
                child.unlink()
    except OSError:
        app.logger.warning('Could not clear working files for a completed S3 result.')


def _record_manifest(job_id, record):
    return [{'name': name, 'size_kb': round(info['size'] / 1024, 1),
             'url': url_for('serve_job_file', job_id=job_id, filename=name)}
            for name, info in sorted(record['files'].items())]


def _stored_response(job_id, filename, attachment=False):
    record = _result_record(job_id)
    if not record:
        return None
    byte_range = request.headers.get('Range')
    if byte_range and not re.fullmatch(r'bytes=(?:[0-9]+-[0-9]*|-[0-9]+)', byte_range):
        abort(416)
    try:
        obj = get_store().object(record, filename, head=request.method == 'HEAD', byte_range=byte_range)
    except ValueError:
        abort(416)
    if obj is None:
        abort(404)
    body = obj.get('Body')
    def chunks():
        try:
            yield from body.iter_chunks(chunk_size=64 * 1024)
        finally:
            body.close()
    response = Response(chunks() if body else b'', status=206 if obj.get('ContentRange') else 200,
                        content_type=record['files'][filename]['content_type'])
    if body:
        response.call_on_close(body.close)
    for source, target in (('ContentLength', 'Content-Length'), ('ContentRange', 'Content-Range'), ('ETag', 'ETag')):
        if source in obj:
            response.headers[target] = str(obj[source])
    response.headers['Accept-Ranges'] = 'bytes'
    if attachment:
        response.headers['Content-Disposition'] = dump_options_header('attachment', {'filename': Path(filename).name})
    return response


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _analytics_usage(output_dir: Path, local_only: bool = False) -> dict:
    """Snapshot provider totals while PROCESS_LOCK still owns the singleton."""
    try:
        if not analytics_enabled():
            return {}
        if local_only:
            return provider_metrics()
        tracker = SessionUsageTracker._instance
        aws = {'bda_usage': tracker.bda_usage, 'bedrock_usage': tracker.bedrock_usage} if tracker else None
        report_path = output_dir / 'openai-image-report.json'
        openai = None
        try:
            if report_path.is_file() and report_path.stat().st_size <= 1024**2:
                openai = json.loads(report_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        return provider_metrics(aws, openai)
    except Exception:
        return {}


def _detect_image_dir(conversion_result: dict, html_path: Path) -> str | None:
    image_files = conversion_result.get("image_files") or []
    if image_files:
        return str(Path(image_files[0]).parent)

    extracted_html_dir = html_path.parent
    if extracted_html_dir.is_dir():
        return str(extracted_html_dir)

    return None


def _friendly_document_name(original_filename: str) -> str:
    base_name = Path(original_filename or "Accessible Document").stem
    cleaned = " ".join(base_name.replace("_", " ").replace("-", " ").split()).strip()
    return cleaned or "Accessible Document"


def _humanize_control_name(raw_value: str) -> str:
    text = " ".join((raw_value or "").replace("_", " ").replace("-", " ").split()).strip()
    return text[:1].upper() + text[1:] if text else ""


def _label_form_controls(soup: BeautifulSoup) -> None:
    for control in soup.find_all(["input", "select", "textarea"]):
        control_id = control.get("id")
        if control_id and soup.find("label", attrs={"for": control_id}):
            continue

        if control.get("aria-label") or control.get("aria-labelledby") or control.get("title"):
            continue

        candidate_parts = [
            control.get("name", ""),
            control.get("id", ""),
            control.get("placeholder", ""),
            control.get("value", "") if control.name == "input" and control.get("type") in {"button", "submit"} else "",
        ]
        candidate_text = next((_humanize_control_name(part) for part in candidate_parts if _humanize_control_name(part)), "")
        if not candidate_text:
            continue

        control["aria-label"] = candidate_text


def _merge_adjacent_duplicate_links(soup: BeautifulSoup) -> None:
    for parent in soup.find_all(True):
        children = list(parent.children)
        index = 0
        while index < len(children) - 1:
            current = children[index]
            following = children[index + 1]

            if getattr(current, "name", None) != "a":
                index += 1
                continue

            whitespace_nodes = []
            next_anchor = None
            look_ahead = index + 1

            while look_ahead < len(children):
                candidate = children[look_ahead]
                if getattr(candidate, "name", None) == "a":
                    next_anchor = candidate
                    break
                if isinstance(candidate, str) and not candidate.strip():
                    whitespace_nodes.append(candidate)
                    look_ahead += 1
                    continue
                next_anchor = None
                break

            if not next_anchor:
                index += 1
                continue

            current_href = (current.get("href") or "").strip()
            next_href = (next_anchor.get("href") or "").strip()
            if not current_href or current_href != next_href:
                index += 1
                continue

            current_text = current.get_text(" ", strip=True)
            next_text = next_anchor.get_text(" ", strip=True)
            if next_text and next_text not in current_text:
                if current.contents and isinstance(current.contents[-1], str):
                    current.append(" ")
                current.append(next_text)

            next_anchor.decompose()
            children = list(parent.children)
            index += 1


def _demote_nested_h1s(soup: BeautifulSoup) -> None:
    for heading in soup.find_all("h1"):
        heading.name = "h2"


def _annotate_pages_and_hide_breaks(soup: BeautifulSoup) -> list[Tag]:
    pages = []
    for index, page in enumerate(soup.find_all(class_="page")):
        page_id = (page.get("id") or "").strip() or f"page-{index + 1}"
        page["id"] = page_id
        page_number = index + 1
        page["aria-label"] = f"Page {page_number}"
        page["tabindex"] = "-1"
        pages.append(page)

    for page_break in soup.find_all(class_="page-break"):
        page_break["aria-hidden"] = "true"
        page_break["role"] = "presentation"

    for nav in soup.find_all("nav"):
        if not nav.get_text(" ", strip=True) and not nav.find("a", href=True):
            nav.decompose()

    return pages


def _normalize_page_number_display(soup: BeautifulSoup, pages: list[Tag]) -> None:
    if len(pages) < 2:
        return

    for index, page in enumerate(pages):
        page_number = str(index + 1)

        for existing_label in page.find_all(class_="accessible-shell__page-label"):
            existing_label.decompose()

        for child in list(page.children):
            if isinstance(child, NavigableString):
                if not child.strip():
                    child.extract()
                    continue
                if child.strip() == page_number:
                    child.extract()
                    continue
                break

            if not isinstance(child, Tag):
                continue

            child_text = child.get_text(" ", strip=True)
            child_classes = set(child.get("class", []))
            if not child_text:
                continue
            if child_text == page_number and child.name in {"p", "div", "span", "small", "strong", "em"}:
                child.decompose()
                continue
            if child.name == "hr" or "page-break" in child_classes:
                continue
            break

        page_label = soup.new_tag("p")
        page_label["class"] = "accessible-shell__page-label"
        page_label["aria-hidden"] = "true"
        page_label.string = f"Page {page_number}"
        page.insert(0, page_label)


def _clean_text_artifacts(soup: BeautifulSoup) -> None:
    replacements = {
        "Â©": "©",
        "Â®": "®",
        "Â™": "™",
        "Â ": " ",
        "â": "'",
        "â": "'",
        "â": '"',
        "â": '"',
        "â": "-",
        "â": "-",
        "â¢": "•",
        "â´": "4",
        "âµ": "5",
        "â¶": "6",
        "Â¹": "1",
        "Â²": "2",
        "Â³": "3",
        "Al implementation": "AI implementation",
        "RESPONSIBLE Al PRACTICE": "RESPONSIBLE AI PRACTICE",
    }

    for text_node in soup.find_all(string=True):
        if not isinstance(text_node, NavigableString):
            continue

        updated_text = str(text_node)
        for old_value, new_value in replacements.items():
            updated_text = updated_text.replace(old_value, new_value)

        if updated_text != str(text_node):
            text_node.replace_with(updated_text)


def _build_generated_page_nav(soup: BeautifulSoup, pages: list[Tag]) -> Tag | None:
    if len(pages) < 2:
        return None

    page_nav = soup.new_tag("nav")
    page_nav["class"] = "accessible-shell__page-nav"
    page_nav["aria-label"] = "Pages"

    page_nav_heading = soup.new_tag("h2")
    page_nav_heading.string = "Pages"
    page_nav.append(page_nav_heading)

    page_list = soup.new_tag("ul")
    for index, page in enumerate(pages):
        page_id = page.get("id", f"page-{index + 1}")
        item = soup.new_tag("li")
        link = soup.new_tag("a", href=f"#{page_id}")
        link.string = f"Page {index + 1}"
        item.append(link)
        page_list.append(item)

    page_nav.append(page_list)
    return page_nav


def _style_html_output(html_file_path: Path, document_name: str, pdf_path=None) -> None:
    if html_file_path.exists() and html_file_path.suffix.lower() == '.html':
        render_reader(html_file_path, document_name, pdf_path=pdf_path)
    return

def _build_file_manifest(base_dir: Path) -> list[dict]:
    files = []
    for file_path in sorted(p for p in base_dir.rglob("*") if p.is_file()):
        relative_path = file_path.relative_to(base_dir).as_posix()
        if not artifact_name(relative_path) or file_path.is_symlink():
            continue
        files.append(
            {
                "name": relative_path,
                "size_kb": round(file_path.stat().st_size / 1024, 1),
                "url": url_for("serve_job_file", job_id=base_dir.name, filename=relative_path),
            }
        )
    return files


def _zip_directory(source_dir: Path, zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
            archive.write(file_path, file_path.relative_to(source_dir))
    return zip_path


def _build_job_response(job_id: str, status: dict) -> dict:
    response = {
        "job_id": job_id,
        "status": status.get("status", "unknown"),
        "created_at": status.get("created_at"),
        "updated_at": status.get("updated_at"),
        "original_filename": status.get("original_filename"),
        "status_url": url_for("get_job_status", job_id=job_id, _external=True),
        "result_url": url_for("get_job_result", job_id=job_id, _external=True),
    }
    for key in ('expires_at', 'retention_basis', 'storage'):
        if key in status:
            response[key] = status[key]

    if status.get("status") == "completed":
        preview_filename = status.get("preview_filename")
        if preview_filename:
            response["viewer_url"] = url_for(
                "serve_job_file",
                job_id=job_id,
                filename=preview_filename,
                _external=True,
            )
        response["download_url"] = url_for("download_job_zip", job_id=job_id, _external=True)

    if status.get("status") == "failed":
        response["error"] = status.get("error")

    return response


def _run_pipeline(
    pdf_path: Path,
    output_dir: Path,
    original_filename: str,
    bda_project_arn: str,
    s3_bucket: str,
    aws_profile: str,
    model_id: str,
    perform_remediation: bool,
    cleanup_bda_output: bool,
    table_only: bool = False,
) -> dict:
    _reset_usage_tracker()

    conversion_options = {
        "single_file": True,
        "cleanup_bda_output": cleanup_bda_output,
        "preserve_layout_structure": os.getenv('READER_LAYOUT_MODE', 'legacy') != 'legacy',
    }

    conversion_result = convert_pdf_to_html(
        pdf_path=str(pdf_path),
        output_dir=str(output_dir / "html"),
        options=conversion_options,
        bda_project_arn=bda_project_arn or None,
        s3_bucket=s3_bucket or None,
        profile=aws_profile or None,
    )

    html_path = Path(conversion_result["html_path"])
    document_name = _friendly_document_name(original_filename)
    heading_status = suggest_headings(html_path)
    (output_dir / 'ollama_status.json').write_text(json.dumps({'status': heading_status}), encoding='utf-8')
    _style_html_output(html_path, document_name, pdf_path)
    image_dir = _detect_image_dir(conversion_result, html_path)

    audit_report_path = output_dir / "accessibility_audit.json"
    audit_result = audit_html_accessibility(
        html_path=str(html_path),
        image_dir=image_dir,
        options={"severity_threshold": "minor"},
        output_path=str(audit_report_path),
    )

    remediation_result = None
    remediation_report_path = None

    if perform_remediation:
        remediation_issues = [
            issue
            for issue in audit_result.get("issues", [])
            if issue.get("remediation_status") == "needs_remediation"
        ]
        if os.getenv('IMAGE_ALT_PROVIDER', 'bedrock') == 'openai':
            remediation_issues = [issue for issue in remediation_issues
                                  if 'alt-text' not in issue.get('type', '')]
        if table_only:
            remediation_issues = [
                issue for issue in remediation_issues if _is_table_issue(issue)
            ]

        filtered_audit_report = {
            "issues": remediation_issues,
            "summary": audit_result.get("summary", {}),
        }

        remediation_output_path = output_dir / "remediated.html"
        remediation_result = remediate_html_accessibility(
            html_path=str(html_path),
            audit_report=filtered_audit_report,
            options={
                "auto_fix": True,
                "single_page": True,
                "model_id": model_id,
                "severity_threshold": "minor",
            },
            output_path=str(remediation_output_path),
            image_dir=image_dir,
        )
        if remediation_result and remediation_result.get("remediated_html_path"):
            _style_html_output(Path(remediation_result["remediated_html_path"]), document_name, pdf_path)

        remediation_report_path = output_dir / "remediation_report.html"
        generate_remediation_report(
            remediation_data=remediation_result,
            output_path=str(remediation_report_path),
            report_format="html",
        )

    legacy_target = Path(remediation_result['remediated_html_path']) if remediation_result and remediation_result.get('remediated_html_path') else html_path
    layout_target = None if table_only else build_layout_preview(
        pdf_path, output_dir, legacy_target, conversion_result, document_name)
    target = layout_target or legacy_target
    if perform_remediation and not table_only and os.getenv('IMAGE_ALT_PROVIDER', 'bedrock') == 'openai':
        described, image_report = describe_openai(
            target.read_text(encoding='utf-8'), image_root=output_dir,
            asset_base=[target.parent, html_path.parent])
        target.write_text(described, encoding='utf-8')
        (output_dir / 'openai-image-report.json').write_text(json.dumps(image_report, indent=2), encoding='utf-8')
        if not layout_target:
            _style_html_output(target, document_name, pdf_path)
    if layout_target:
        try:
            refresh_layout_review(layout_target)
        except Exception as exc:
            # A presentation-only failure must not discard the legacy conversion.
            layout_target = None
            (output_dir / 'layout-status.json').write_text(json.dumps({
                'status': 'fallback', 'preview': 'legacy', 'error_type': type(exc).__name__,
                'message': 'Final layout review failed; the original reader was retained.'
            }, indent=2), encoding='utf-8')

    usage_data_path = save_usage_data(
        output_path=str(output_dir / "usage_data.json"),
        profile=aws_profile or None,
    )

    return {
        "conversion_result": conversion_result,
        "layout_preview_path": layout_target,
        "audit_result": audit_result,
        "remediation_result": remediation_result,
        "audit_report_path": audit_report_path,
        "remediation_report_path": remediation_report_path,
        "usage_data_path": usage_data_path,
        "table_only": table_only,
        "issues_selected_for_remediation": len(
            filtered_audit_report["issues"] if perform_remediation else []
        ),
    }


def _run_local_table_pipeline(
    pdf_path: Path,
    output_dir: Path,
    original_filename: str,
    perform_remediation: bool,
) -> dict:
    """Run local-only PDF table extraction and rule-based table remediation."""
    document_name = _friendly_document_name(original_filename)
    extraction_path = output_dir / "local_table_extraction.html"
    conversion_result = extract_pdf_tables_to_html(
        pdf_path=pdf_path,
        output_path=extraction_path,
        document_title=document_name,
    )
    html_path = Path(conversion_result["html_path"])
    _style_html_output(html_path, document_name)

    audit_report_path = output_dir / "accessibility_audit.json"
    audit_result = audit_html_accessibility(
        html_path=str(html_path),
        options={"severity_threshold": "minor"},
        output_path=str(audit_report_path),
    )

    remediation_result = None
    remediation_report_path = None
    remediation_issues = [
        issue
        for issue in audit_result.get("issues", [])
        if issue.get("remediation_status") == "needs_remediation" and _is_table_issue(issue)
    ]

    if perform_remediation:
        remediation_output_path = output_dir / "remediated.html"
        remediation_result = remediate_html_accessibility(
            html_path=str(html_path),
            audit_report={
                "issues": remediation_issues,
                "summary": audit_result.get("summary", {}),
            },
            options={
                "auto_fix": True,
                "single_page": True,
                "disable_ai": True,
                "severity_threshold": "minor",
            },
            output_path=str(remediation_output_path),
        )
        if remediation_result and remediation_result.get("remediated_html_path"):
            _style_html_output(Path(remediation_result["remediated_html_path"]), document_name)

        remediation_report_path = output_dir / "remediation_report.html"
        generate_remediation_report(
            remediation_data=remediation_result,
            output_path=str(remediation_report_path),
            report_format="html",
        )

    usage_data_path = output_dir / "usage_data.json"
    usage_data_path.write_text(
        json.dumps(
            {
                "mode": "local_table_lab",
                "aws_services_used": False,
                "tables_extracted": conversion_result["tables_extracted"],
                "pages_with_tables": conversion_result["pages_with_tables"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "conversion_result": conversion_result,
        "audit_result": audit_result,
        "remediation_result": remediation_result,
        "audit_report_path": audit_report_path,
        "remediation_report_path": remediation_report_path,
        "usage_data_path": usage_data_path,
        "table_only": True,
        "issues_selected_for_remediation": len(remediation_issues),
    }


def _create_job(job_dir: Path, original_filename: str, source_type: str, retention_fields=None) -> dict:
    status = {
        "status": "queued",
        "source_type": source_type,
        "original_filename": original_filename,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    status.update(retention_fields or {})
    if retention_fields:
        status['storage'] = 's3_pending'
    _write_job_status(job_dir, status)
    return status


def _update_job(job_dir: Path, **changes) -> dict:
    status = _read_job_status(job_dir) or {}
    status.update(changes)
    status["updated_at"] = _utc_now()
    _write_job_status(job_dir, status)
    return status


def _start_background_job(
    job_id: str,
    pdf_path: Path,
    original_filename: str,
    bda_project_arn: str,
    s3_bucket: str,
    aws_profile: str,
    model_id: str,
    perform_remediation: bool,
    cleanup_bda_output: bool,
    table_only: bool = False,
    local_table_lab: bool = False,
    analytics_context: dict = None,
) -> None:
    job_dir = RUNS_DIR / job_id
    output_dir = job_dir / "output"
    workflow = 'local_tables' if local_table_lab else 'tables' if table_only else 'full'
    timer = JobTimer('api', workflow, analytics_context)

    def runner() -> None:
        try:
            with PROCESS_LOCK:
                timer.start()
                _update_job(job_dir, status="processing")
                if expired(_read_job_status(job_dir) or {}):
                    raise ResultExpired()
                try:
                    if local_table_lab:
                        result = _run_local_table_pipeline(
                            pdf_path=pdf_path,
                            output_dir=output_dir,
                            original_filename=original_filename,
                            perform_remediation=perform_remediation,
                        )
                    else:
                        result = _run_pipeline(
                            pdf_path=pdf_path,
                            output_dir=output_dir,
                            original_filename=original_filename,
                            bda_project_arn=bda_project_arn,
                            s3_bucket=s3_bucket,
                            aws_profile=aws_profile,
                            model_id=model_id,
                            perform_remediation=perform_remediation,
                            cleanup_bda_output=cleanup_bda_output,
                            table_only=table_only,
                        )
                finally:
                    timer.fields.update(_analytics_usage(output_dir, local_table_lab))

            zip_path = _zip_directory(output_dir, job_dir / f"{pdf_path.stem}_results.zip")
            conversion_result = result["conversion_result"]
            audit_result = result["audit_result"]
            remediation_result = result["remediation_result"]

            preview_target = preview_path(result).relative_to(job_dir).as_posix()

            _complete_job(
                job_dir,
                original_filename=original_filename,
                issues_found=len(audit_result.get("issues", [])),
                issues_needing_remediation=len(
                    [
                        issue
                        for issue in audit_result.get("issues", [])
                        if issue.get("remediation_status") == "needs_remediation"
                    ]
                ),
                issues_remediated=(remediation_result or {}).get("issues_remediated", 0),
                remediation_mode="local_tables" if local_table_lab else ("tables" if table_only else "full"),
                issues_selected_for_remediation=result["issues_selected_for_remediation"],
                preview_filename=preview_target,
                zip_path=str(zip_path),
                zip_filename=zip_path.name,
            )
            timer.complete(result)
            _cleanup_completed_scratch(job_dir)
        except Exception as exc:
            timer.fail(exc)
            _update_job(
                job_dir,
                status="failed",
                original_filename=original_filename,
                error=str(exc),
                traceback=traceback.format_exc(),
            )

    try:
        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
    except Exception as exc:
        timer.fail(exc)
        _update_job(job_dir, status='failed', error='The processing worker could not start.')
        raise


@app.get("/")
def index():
    return render_template(
        "index.html",
        defaults={
            "bda_project_arn": _default_value("BDA_PROJECT_ARN"),
            "s3_bucket": _default_value("BDA_S3_BUCKET"),
            "aws_profile": _default_value("AWS_PROFILE"),
            "model_id": _default_value("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0"),
        },
        max_upload_size_mb=MAX_UPLOAD_SIZE_MB,
        table_only=False,
    )


@app.get("/table-lab")
def table_lab():
    return render_template(
        "index.html",
        defaults={
            "bda_project_arn": "",
            "s3_bucket": "",
            "aws_profile": "",
            "model_id": "",
        },
        max_upload_size_mb=MAX_UPLOAD_SIZE_MB,
        table_only=True,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.errorhandler(StorageUnavailable)
def storage_unavailable(error):
    return jsonify({'error': 'Stored results are temporarily unavailable. Please retry opening the result later; avoid resubmitting the PDF.'}), 503, {'Retry-After': '30'}


@app.errorhandler(ResultExpired)
def result_expired(error):
    return jsonify({'status': 'expired', 'error': 'This generated webpage has reached its retention deadline and is no longer available.'}), 410


@app.errorhandler(RetentionError)
def retention_error(error):
    return jsonify({'error': str(error)}), 400


@app.before_request
def analytics_submission():
    job_id = (request.view_args or {}).get('job_id')
    if job_id is not None and not JOB_ID.fullmatch(job_id):
        abort(404)
    if request.method == 'POST' and request.endpoint in {'create_job', 'process_pdf'}:
        record_event('submission_requested', channel='api' if request.endpoint == 'create_job' else 'form')


@app.after_request
def analytics_response(response):
    try:
        if not analytics_enabled():
            return response
        if request.method == 'POST' and request.endpoint in {'create_job', 'process_pdf'} and response.status_code >= 400:
            record_event('submission_error', channel='api' if request.endpoint == 'create_job' else 'form', http_status=response.status_code)
        elif request.method == 'GET' and request.endpoint == 'serve_job_file':
            args = request.view_args or {}
            if not JOB_ID.fullmatch(args.get('job_id', '')):
                return response
            filename = args.get('filename', '')
            if filename.lower().endswith('.html'):
                status = _available_status(args['job_id']) or {}
                kind = ('selected_preview' if filename == status.get('preview_filename') else
                        'legacy_preview' if not status and filename in {
                            'output/remediated.html', 'output/layout/layout.html', 'output/local_table_extraction.html'
                        } else 'other_html')
                context = correlation(request.headers.get('X-Accessible-Reader-Id'),
                                      status.get('transaction_number'), args['job_id'])
                record_event('html_response', html_kind=kind, http_status=response.status_code, **context)
        elif request.method == 'GET' and request.endpoint == 'download_job_zip':
            record_event('zip_response', http_status=response.status_code)
    except Exception:
        pass  # A missing/corrupt analytics source must not affect HTTP delivery.
    return response


@app.after_request
def add_cors_headers(response):
    if request.path.startswith(('/runs/', '/api/jobs', '/api/table-jobs')):
        # Expiration is enforced server-side, not by a stale browser/proxy cache.
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
    if ALLOWED_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Accessible-Reader-Id"
    return response


@app.route("/api/jobs", methods=["OPTIONS"])
@app.route("/api/table-jobs", methods=["OPTIONS"])
@app.route("/api/jobs/<job_id>", methods=["OPTIONS"])
@app.route("/api/jobs/<job_id>/result", methods=["OPTIONS"])
def cors_preflight(job_id=None):
    return ("", 204)


@app.post("/process")
@app.post("/table-lab/process")
def process_pdf():
    table_only = request.path.startswith("/table-lab/")
    uploaded_file = request.files.get("pdf_file")
    if not uploaded_file or not uploaded_file.filename:
        return render_template(
            "index.html",
            defaults={
                "bda_project_arn": request.form.get("bda_project_arn", ""),
                "s3_bucket": request.form.get("s3_bucket", ""),
                "aws_profile": request.form.get("aws_profile", ""),
                "model_id": request.form.get("model_id", "us.amazon.nova-lite-v1:0"),
            },
            max_upload_size_mb=MAX_UPLOAD_SIZE_MB,
            table_only=table_only,
            error="Choose a PDF file before starting the workflow.",
        ), 400

    safe_name = secure_filename(uploaded_file.filename)
    if not safe_name.lower().endswith(".pdf"):
        return render_template(
            "index.html",
            defaults={
                "bda_project_arn": request.form.get("bda_project_arn", ""),
                "s3_bucket": request.form.get("s3_bucket", ""),
                "aws_profile": request.form.get("aws_profile", ""),
                "model_id": request.form.get("model_id", "us.amazon.nova-lite-v1:0"),
            },
            max_upload_size_mb=MAX_UPLOAD_SIZE_MB,
            table_only=table_only,
            error="Only PDF uploads are supported.",
        ), 400

    retention_fields = _request_retention()
    job_id = uuid.uuid4().hex
    job_dir = RUNS_DIR / job_id
    input_dir, output_dir = _ensure_run_dirs(job_dir)
    pdf_path = input_dir / safe_name
    uploaded_file.save(pdf_path)
    if get_store():
        _create_job(job_dir, uploaded_file.filename, 'upload', retention_fields)

    perform_remediation = _bool_from_form("perform_remediation", default=True)
    cleanup_bda_output = _bool_from_form("cleanup_bda_output", default=True)
    bda_project_arn = request.form.get("bda_project_arn", "").strip()
    s3_bucket = request.form.get("s3_bucket", "").strip()
    aws_profile = request.form.get("aws_profile", "").strip()
    model_id = request.form.get("model_id", "us.amazon.nova-lite-v1:0").strip()
    timer = JobTimer('form', 'local_tables' if table_only else 'full',
                     correlation(request.form.get('reader_client_id'), job_id=job_id))

    try:
        with PROCESS_LOCK:
            timer.start()
            try:
                if table_only:
                    result = _run_local_table_pipeline(
                        pdf_path=pdf_path,
                        output_dir=output_dir,
                        original_filename=uploaded_file.filename,
                        perform_remediation=perform_remediation,
                    )
                else:
                    result = _run_pipeline(
                        pdf_path=pdf_path,
                        output_dir=output_dir,
                        original_filename=uploaded_file.filename,
                        bda_project_arn=bda_project_arn,
                        s3_bucket=s3_bucket,
                        aws_profile=aws_profile,
                        model_id=model_id,
                        perform_remediation=perform_remediation,
                        cleanup_bda_output=cleanup_bda_output,
                        table_only=False,
                    )
            finally:
                timer.fields.update(_analytics_usage(output_dir, table_only))
    except Exception as exc:
        timer.fail(exc)
        return render_template(
            "index.html",
            defaults={
                "bda_project_arn": bda_project_arn,
                "s3_bucket": s3_bucket,
                "aws_profile": aws_profile,
                "model_id": model_id,
            },
            max_upload_size_mb=MAX_UPLOAD_SIZE_MB,
            table_only=table_only,
            error=str(exc),
            traceback_text=traceback.format_exc(),
        ), 500

    try:
        zip_path = _zip_directory(output_dir, job_dir / f"{pdf_path.stem}_results.zip")
        if get_store():
            _complete_job(job_dir, original_filename=uploaded_file.filename,
                          preview_filename=preview_path(result).relative_to(job_dir).as_posix(),
                          zip_filename=zip_path.name, remediation_mode='local_tables' if table_only else 'full',
                          issues_found=len(result['audit_result'].get('issues', [])),
                          issues_remediated=(result.get('remediation_result') or {}).get('issues_remediated', 0))
    except Exception as exc:
        timer.fail(exc)
        raise
    timer.complete(result)
    _cleanup_completed_scratch(job_dir)
    stored_record = _result_record(job_id)
    files = _record_manifest(job_id, stored_record) if stored_record else _build_file_manifest(job_dir)

    conversion_result = result["conversion_result"]
    audit_result = result["audit_result"]
    remediation_result = result["remediation_result"]

    preview_target = preview_path(result).relative_to(job_dir).as_posix()

    return render_template(
        "results.html",
        job_id=job_id,
        original_filename=uploaded_file.filename,
        preview_url=url_for("serve_job_file", job_id=job_id, filename=preview_target),
        zip_url=url_for("download_job_zip", job_id=job_id),
        files=files,
        issues_found=len(audit_result.get("issues", [])),
        issues_needing_remediation=len(
            [
                issue
                for issue in audit_result.get("issues", [])
                if issue.get("remediation_status") == "needs_remediation"
            ]
        ),
        remediated_count=(remediation_result or {}).get("issues_remediated", 0),
        table_only=table_only,
        issues_selected_for_remediation=result["issues_selected_for_remediation"],
    )


@app.post("/api/jobs")
@app.post("/api/table-jobs")
def create_job():
    uploaded_file = request.files.get("pdf_file")
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'The JSON request must be an object.'}), 400

    transaction_number = (
        (request.form.get("transaction_number") if uploaded_file else payload.get("transaction_number"))
        or ""
    )
    if not isinstance(transaction_number, str) or len(transaction_number) > 80:
        return jsonify({'error': 'Invalid transaction number.'}), 400
    transaction_number = transaction_number.strip()
    client_id = request.form.get('reader_client_id') if uploaded_file else payload.get('reader_client_id')
    retention_fields = _request_retention(payload)
    table_only = request.path == '/api/table-jobs' or (
        _bool_from_form('table_only') if uploaded_file else _bool_value(payload.get('table_only')))
    workflow = 'local_tables' if request.path == '/api/table-jobs' else 'tables' if table_only else 'full'
    source_sha256 = None
    if get_store():
        if not uploaded_file or not uploaded_file.filename:
            return jsonify({'error': 'S3-backed jobs require a PDF upload, including cache lookups.'}), 400
        digest = hashlib.sha256()
        for block in iter(lambda: uploaded_file.stream.read(1024 * 1024), b''):
            digest.update(block)
        uploaded_file.stream.seek(0)
        source_sha256 = digest.hexdigest()

    existing_job = _find_existing_job(transaction_number, workflow, source_sha256)
    if existing_job:
        existing_job_id, existing_status = existing_job
        record_event('job_cache_hit', channel='api', workflow=existing_status.get('remediation_mode', 'full'),
                     cache_state=existing_status.get('status'),
                     **correlation(client_id, transaction_number, existing_job_id))
        return jsonify(_build_job_response(existing_job_id, existing_status) | {"details": _public_status(existing_status)}), 200

    job_id = uuid.uuid4().hex
    job_dir = RUNS_DIR / job_id
    input_dir, _ = _ensure_run_dirs(job_dir)

    if uploaded_file and uploaded_file.filename:
        safe_name = secure_filename(uploaded_file.filename)
        if not safe_name.lower().endswith(".pdf"):
            return jsonify({"error": "Only PDF uploads are supported."}), 400
        pdf_path = input_dir / safe_name
        uploaded_file.save(pdf_path)
        original_filename = uploaded_file.filename
        source_type = "upload"
    else:
        pdf_source_path = payload.get("pdf_path", "").strip()
        if not pdf_source_path:
            return jsonify({"error": "Provide either multipart field 'pdf_file' or JSON field 'pdf_path'."}), 400
        pdf_path = Path(pdf_source_path)
        if not pdf_path.exists() or not pdf_path.is_file():
            return jsonify({"error": f"PDF path not found: {pdf_source_path}"}), 400
        if pdf_path.suffix.lower() != ".pdf":
            return jsonify({"error": "Only PDF files are supported."}), 400
        safe_name = secure_filename(pdf_path.name)
        copied_pdf_path = input_dir / safe_name
        shutil.copy2(pdf_path, copied_pdf_path)
        pdf_path = copied_pdf_path
        original_filename = pdf_source_path
        source_type = "path"

    bda_project_arn = (
        (request.form.get("bda_project_arn") if uploaded_file else payload.get("bda_project_arn"))
        or _default_value("BDA_PROJECT_ARN")
    )
    s3_bucket = (
        (request.form.get("s3_bucket") if uploaded_file else payload.get("s3_bucket"))
        or _default_value("BDA_S3_BUCKET")
    )
    aws_profile = (
        (request.form.get("aws_profile") if uploaded_file else payload.get("aws_profile"))
        or _default_value("AWS_PROFILE")
    )
    model_id = (
        (request.form.get("model_id") if uploaded_file else payload.get("model_id"))
        or _default_value("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")
    )
    perform_remediation = (
        _bool_from_form("perform_remediation", default=True)
        if uploaded_file
        else payload.get("perform_remediation", True)
    )
    cleanup_bda_output = (
        _bool_from_form("cleanup_bda_output", default=True)
        if uploaded_file
        else payload.get("cleanup_bda_output", True)
    )
    table_only = request.path == "/api/table-jobs" or (
        _bool_from_form("table_only", default=False)
        if uploaded_file
        else _bool_value(payload.get("table_only"), default=False)
    )
    local_table_lab = request.path == "/api/table-jobs"

    status = _create_job(job_dir, original_filename=original_filename, source_type=source_type,
                         retention_fields=retention_fields)
    if transaction_number:
        status = _update_job(job_dir, transaction_number=transaction_number)
    if source_sha256:
        status = _update_job(job_dir, source_sha256=source_sha256)
    if table_only:
        status = _update_job(
            job_dir,
            remediation_mode="local_tables" if local_table_lab else "tables",
        )
    _start_background_job(
        job_id=job_id,
        pdf_path=pdf_path,
        original_filename=original_filename,
        bda_project_arn=bda_project_arn,
        s3_bucket=s3_bucket,
        aws_profile=aws_profile,
        model_id=model_id,
        perform_remediation=perform_remediation,
        cleanup_bda_output=cleanup_bda_output,
        table_only=table_only,
        local_table_lab=local_table_lab,
        analytics_context=correlation(client_id, transaction_number, job_id),
    )
    return jsonify(_build_job_response(job_id, status)), 202


@app.get("/api/jobs/<job_id>")
def get_job_status(job_id: str):
    job_dir = RUNS_DIR / job_id
    status = _available_status(job_id)
    if not status:
        return jsonify({"error": "Job status unavailable."}), 404

    if status.get("status") == "completed" and not _result_record(job_id):
        preview_filename = status.get("preview_filename")
        if not preview_filename or not (job_dir / Path(preview_filename)).exists():
            status = _update_job(
                job_dir,
                status="failed",
                error="Generated HTML is no longer available and must be regenerated.",
            )

    return jsonify(_build_job_response(job_id, status) | {"details": _public_status(status)})


@app.get("/api/jobs/<job_id>/result")
def get_job_result(job_id: str):
    job_dir = RUNS_DIR / job_id
    status = _available_status(job_id)
    if not status:
        return jsonify({"error": "Job status unavailable."}), 404
    record = _result_record(job_id)
    if status.get("status") == "completed" and not record:
        preview_filename = status.get("preview_filename")
        if not preview_filename or not (job_dir / Path(preview_filename)).exists():
            status = _update_job(
                job_dir,
                status="failed",
                error="Generated HTML is no longer available and must be regenerated.",
            )
    if status.get("status") != "completed":
        return jsonify(_build_job_response(job_id, status)), 409

    files = _record_manifest(job_id, record) if record else _build_file_manifest(job_dir)
    return jsonify(
        _build_job_response(job_id, status)
        | {
            "files": files,
            "summary": {
                "issues_found": status.get("issues_found", 0),
                "issues_needing_remediation": status.get("issues_needing_remediation", 0),
                "issues_remediated": status.get("issues_remediated", 0),
            },
        }
    )


@app.get("/runs/<job_id>/<path:filename>")
def serve_job_file(job_id: str, filename: str):
    if not artifact_name(filename):
        abort(404)
    status = _available_status(job_id)  # Enforce expiry even if an old local copy remains.
    if status and status.get('storage') == 's3_pending':
        return jsonify({'error': 'This result has not been committed to durable storage.'}), 409
    stored = _stored_response(job_id, filename)
    if stored is not None:
        return stored
    job_dir = RUNS_DIR / job_id
    if not job_dir.exists():
        abort(404)
    return send_from_directory(job_dir, filename)


@app.get("/runs/<job_id>/download")
def download_job_zip(job_id: str):
    status = _available_status(job_id)
    if status and status.get('storage') == 's3_pending':
        return jsonify({'error': 'This result has not been committed to durable storage.'}), 409
    record = _result_record(job_id)
    if record:
        return _stored_response(job_id, record['status']['zip_filename'], attachment=True)
    job_dir = RUNS_DIR / job_id
    if not job_dir.exists():
        abort(404)

    zip_matches = sorted(job_dir.glob("*_results.zip"))
    if not zip_matches:
        abort(404)

    return send_file(zip_matches[0], as_attachment=True)


if __name__ == "__main__":
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    app.run(host=host, port=port, debug=False)
