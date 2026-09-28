import os
import json
import shutil
import threading
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from bs4 import NavigableString
from bs4 import Tag
from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory, url_for
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
    return json.loads(status_path.read_text(encoding="utf-8"))


def _iter_job_statuses() -> list[tuple[str, dict]]:
    if not RUNS_DIR.exists():
        return []

    jobs = []
    for job_dir in sorted((p for p in RUNS_DIR.iterdir() if p.is_dir()), reverse=True):
        status = _read_job_status(job_dir)
        if status:
            jobs.append((job_dir.name, status))
    return jobs


def _find_existing_job(transaction_number: str) -> tuple[str, dict] | None:
    if not transaction_number:
        return None

    for job_id, status in _iter_job_statuses():
        if status.get("transaction_number") != transaction_number:
            continue

        job_dir = RUNS_DIR / job_id
        job_status = status.get("status")

        if job_status == "completed":
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _style_html_output(html_file_path: Path, document_name: str) -> None:
    if not html_file_path.exists() or html_file_path.suffix.lower() != ".html":
        return

    soup = BeautifulSoup(html_file_path.read_text(encoding="utf-8"), "html.parser")

    if not soup.html:
        return

    if not soup.head:
        head_tag = soup.new_tag("head")
        soup.html.insert(0, head_tag)

    if not soup.body:
        body_tag = soup.new_tag("body")
        soup.html.append(body_tag)

    title_text = document_name
    if soup.title:
        existing_title = (soup.title.string or "").strip() if soup.title.string else ""
        if existing_title and existing_title not in {"0", "Remediated Document", "Combined Document"}:
            title_text = existing_title
        soup.title.string = title_text
    else:
        title_tag = soup.new_tag("title")
        title_tag.string = title_text
        soup.head.append(title_tag)

    if not soup.html.get("lang"):
        soup.html["lang"] = "en"

    _label_form_controls(soup)
    _merge_adjacent_duplicate_links(soup)
    _clean_text_artifacts(soup)
    _demote_nested_h1s(soup)
    pages = _annotate_pages_and_hide_breaks(soup)
    _normalize_page_number_display(soup, pages)

    while True:
        existing_shell = soup.find(attrs={"data-accessible-html-shell": "true"})
        if not existing_shell:
            break

        existing_content = existing_shell.find(class_="accessible-shell__content")
        if existing_content:
            restored_nodes = [child.extract() for child in list(existing_content.contents)]
            existing_shell.decompose()
            soup.body.clear()
            for node in restored_nodes:
                soup.body.append(node)
        else:
            existing_shell.decompose()

    for stray_style in soup.body.find_all("style", attrs={"data-accessible-html-shell": "true"}):
        stray_style.decompose()

    for child in list(soup.body.contents):
        if not isinstance(child, NavigableString):
            continue

        text = str(child).strip()
        if not text:
            child.extract()
            continue

        # Clean up previously broken renders where the CSS stylesheet was injected
        # into the body as visible text instead of a <style> tag in <head>.
        if ".accessible-shell" in text and "body {" in text:
            child.extract()

    existing_style = soup.find("style", attrs={"data-accessible-html-shell": "true"})
    if not existing_style:
        style_tag = soup.new_tag("style")
        style_tag["data-accessible-html-shell"] = "true"
        style_tag.string = """
body {
  margin: 0;
  background: #f5f7fb;
  color: #18202a;
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.65;
}

.accessible-shell {
  max-width: 1280px;
  margin: 0 auto;
  padding: 32px 24px 64px;
}

.accessible-shell__header {
  margin-bottom: 28px;
  padding-bottom: 18px;
  border-bottom: 1px solid #d8e0eb;
}

.accessible-shell__eyebrow {
  margin: 0 0 8px;
  color: #5f6f82;
  font-size: 0.92rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.accessible-shell__title {
  margin: 0;
  color: #102033;
  font-size: clamp(1.8rem, 3vw, 2.7rem);
  line-height: 1.15;
}

.accessible-shell__content {
  background: #ffffff;
  border: 1px solid #d8e0eb;
  border-radius: 16px;
  padding: 28px 32px;
  box-shadow: 0 14px 32px rgba(16, 32, 51, 0.06);
  overflow-x: auto;
}

.accessible-shell__layout {
  display: grid;
  grid-template-columns: minmax(180px, 220px) minmax(0, 1fr);
  gap: 28px;
  align-items: start;
}

.accessible-shell__page-nav {
  background: #ffffff;
  border: 1px solid #d8e0eb;
  border-radius: 16px;
  padding: 24px 22px;
  box-shadow: 0 14px 32px rgba(16, 32, 51, 0.06);
  position: sticky;
  top: 24px;
}

.accessible-shell__page-nav h2 {
  margin: 0 0 12px;
  color: #102033;
  font-size: 1.05rem;
  line-height: 1.3;
}

.accessible-shell__page-nav ul,
.accessible-shell__page-nav ol {
  margin: 0;
  padding-left: 0;
  list-style: none;
}

.accessible-shell__page-nav li + li {
  margin-top: 0.35rem;
}

.accessible-shell__page-nav a {
  overflow-wrap: anywhere;
}

.accessible-shell__page-label {
  margin: 0 0 0.9rem;
  color: #5f6f82;
  font-size: 0.95rem;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.accessible-shell__content .page {
  padding-top: 0.35rem;
}

.accessible-shell__content .page + .page {
  margin-top: 2.5rem;
  padding-top: 2.5rem;
  border-top: 1px dashed #d8e0eb;
}

.accessible-shell__content img,
.accessible-shell__content table {
  max-width: 100%;
}

.accessible-shell__content table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  border: 1px solid #63758a;
  background: #ffffff;
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.4;
}

.accessible-shell__content caption {
  padding: 0.75rem 0.9rem;
  color: #102033;
  font-size: 1.05rem;
  font-weight: 700;
  text-align: left;
  caption-side: top;
}

.accessible-shell__content td,
.accessible-shell__content th {
  padding: 0.45rem 0.6rem;
  border: 1px solid #8c9bad;
  vertical-align: top;
}

.accessible-shell__content thead th {
  color: #102033;
  background: #e8eef5;
  font-weight: 700;
  text-align: left;
}

.accessible-shell__content tbody tr:nth-child(even):not(.table-section) {
  background: #f7f9fc;
}

.accessible-shell__content .table-section th {
  color: #102033;
  background: #eef3f8;
  font-style: italic;
  font-weight: 700;
  text-align: left;
}

.accessible-shell__content td:not(:first-child),
.accessible-shell__content th:not(:first-child) {
  white-space: nowrap;
}

@media (max-width: 900px) {
  .accessible-shell {
    padding: 24px 16px 48px;
  }

  .accessible-shell__layout {
    grid-template-columns: 1fr;
    gap: 18px;
  }

  .accessible-shell__content {
    padding: 22px 18px;
  }
}
        """
        soup.head.append(style_tag)

    shell = soup.new_tag("main")
    shell["class"] = "accessible-shell"
    shell["data-accessible-html-shell"] = "true"

    header = soup.new_tag("header")
    header["class"] = "accessible-shell__header"

    eyebrow = soup.new_tag("p")
    eyebrow["class"] = "accessible-shell__eyebrow"
    eyebrow.string = "Accessible HTML View"
    header.append(eyebrow)

    title_heading = soup.new_tag("h1")
    title_heading["class"] = "accessible-shell__title"
    title_heading.string = title_text
    header.append(title_heading)

    content = soup.new_tag("section")
    content["class"] = "accessible-shell__content"
    content["aria-label"] = "Document content"
    layout = soup.new_tag("div")
    layout["class"] = "accessible-shell__layout"

    movable_children = list(soup.body.contents)
    page_nav_source = None

    for child in movable_children:
        if getattr(child, "name", None) not in {"ul", "ol", "nav"}:
            continue

        if child.name == "nav":
            list_candidate = child.find(["ul", "ol"], recursive=False)
            if not list_candidate:
                continue
            candidate = list_candidate
        else:
            candidate = child

        link_nodes = candidate.find_all("a", href=True) if getattr(candidate, "find_all", None) else []
        if len(link_nodes) < 2:
            continue

        page_links = 0
        for link in link_nodes:
            link_text = link.get_text(" ", strip=True).lower()
            href = (link.get("href") or "").lower()
            if "page" in link_text or "#page" in href:
                page_links += 1

        if page_links >= min(2, len(link_nodes)):
            page_nav_source = candidate
            break

    page_nav = None
    if page_nav_source is not None:
        page_nav = soup.new_tag("nav")
        page_nav["class"] = "accessible-shell__page-nav"
        page_nav["aria-label"] = "Jump to page"

        page_nav_heading = soup.new_tag("h2")
        page_nav_heading.string = "Jump to page"
        page_nav.append(page_nav_heading)
        page_nav.append(page_nav_source.extract())
    elif len(pages) >= 2:
        page_nav = _build_generated_page_nav(soup, pages)

    if page_nav is not None:
        layout.append(page_nav)

    for child in list(soup.body.contents):
        content.append(child.extract())

    shell.append(header)
    if page_nav is not None:
        layout.append(content)
        shell.append(layout)
    else:
        shell.append(content)
    soup.body.append(shell)

    html_file_path.write_text(str(soup), encoding="utf-8")


def _build_file_manifest(base_dir: Path) -> list[dict]:
    files = []
    for file_path in sorted(p for p in base_dir.rglob("*") if p.is_file()):
        relative_path = file_path.relative_to(base_dir).as_posix()
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
    _style_html_output(html_path, document_name)
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
            _style_html_output(Path(remediation_result["remediated_html_path"]), document_name)

        remediation_report_path = output_dir / "remediation_report.html"
        generate_remediation_report(
            remediation_data=remediation_result,
            output_path=str(remediation_report_path),
            report_format="html",
        )

    usage_data_path = save_usage_data(
        output_path=str(output_dir / "usage_data.json"),
        profile=aws_profile or None,
    )

    return {
        "conversion_result": conversion_result,
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


def _create_job(job_dir: Path, original_filename: str, source_type: str) -> dict:
    status = {
        "status": "queued",
        "source_type": source_type,
        "original_filename": original_filename,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }
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
) -> None:
    job_dir = RUNS_DIR / job_id
    output_dir = job_dir / "output"

    def runner() -> None:
        _update_job(job_dir, status="processing")
        try:
            with PROCESS_LOCK:
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

            zip_path = _zip_directory(output_dir, job_dir / f"{pdf_path.stem}_results.zip")
            conversion_result = result["conversion_result"]
            audit_result = result["audit_result"]
            remediation_result = result["remediation_result"]

            if remediation_result and remediation_result.get("remediated_html_path"):
                preview_target = (
                    Path(remediation_result["remediated_html_path"]).relative_to(job_dir).as_posix()
                )
            else:
                preview_target = Path(conversion_result["html_path"]).relative_to(job_dir).as_posix()

            _update_job(
                job_dir,
                status="completed",
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
            )
        except Exception as exc:
            _update_job(
                job_dir,
                status="failed",
                original_filename=original_filename,
                error=str(exc),
                traceback=traceback.format_exc(),
            )

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


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


@app.after_request
def add_cors_headers(response):
    if ALLOWED_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
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

    job_id = uuid.uuid4().hex[:12]
    job_dir = RUNS_DIR / job_id
    input_dir, output_dir = _ensure_run_dirs(job_dir)
    pdf_path = input_dir / safe_name
    uploaded_file.save(pdf_path)

    perform_remediation = _bool_from_form("perform_remediation", default=True)
    cleanup_bda_output = _bool_from_form("cleanup_bda_output", default=True)
    bda_project_arn = request.form.get("bda_project_arn", "").strip()
    s3_bucket = request.form.get("s3_bucket", "").strip()
    aws_profile = request.form.get("aws_profile", "").strip()
    model_id = request.form.get("model_id", "us.amazon.nova-lite-v1:0").strip()

    try:
        with PROCESS_LOCK:
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
    except Exception as exc:
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

    zip_path = _zip_directory(output_dir, job_dir / f"{pdf_path.stem}_results.zip")
    files = _build_file_manifest(job_dir)

    conversion_result = result["conversion_result"]
    audit_result = result["audit_result"]
    remediation_result = result["remediation_result"]

    preview_target = None
    if remediation_result and remediation_result.get("remediated_html_path"):
        preview_target = Path(remediation_result["remediated_html_path"]).relative_to(job_dir).as_posix()
    else:
        preview_target = Path(conversion_result["html_path"]).relative_to(job_dir).as_posix()

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

    job_id = uuid.uuid4().hex[:12]
    job_dir = RUNS_DIR / job_id
    input_dir, _ = _ensure_run_dirs(job_dir)

    transaction_number = (
        (request.form.get("transaction_number") if uploaded_file else payload.get("transaction_number"))
        or ""
    ).strip()

    existing_job = _find_existing_job(transaction_number)
    if existing_job:
        existing_job_id, existing_status = existing_job
        return jsonify(_build_job_response(existing_job_id, existing_status) | {"details": existing_status}), 200

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

    status = _create_job(job_dir, original_filename=original_filename, source_type=source_type)
    if transaction_number:
        status = _update_job(job_dir, transaction_number=transaction_number)
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
    )
    return jsonify(_build_job_response(job_id, status)), 202


@app.get("/api/jobs/<job_id>")
def get_job_status(job_id: str):
    job_dir = RUNS_DIR / job_id
    if not job_dir.exists():
        return jsonify({"error": "Job not found."}), 404

    status = _read_job_status(job_dir)
    if not status:
        return jsonify({"error": "Job status unavailable."}), 404

    if status.get("status") == "completed":
        preview_filename = status.get("preview_filename")
        if not preview_filename or not (job_dir / Path(preview_filename)).exists():
            status = _update_job(
                job_dir,
                status="failed",
                error="Generated HTML is no longer available and must be regenerated.",
            )

    return jsonify(_build_job_response(job_id, status) | {"details": status})


@app.get("/api/jobs/<job_id>/result")
def get_job_result(job_id: str):
    job_dir = RUNS_DIR / job_id
    if not job_dir.exists():
        return jsonify({"error": "Job not found."}), 404

    status = _read_job_status(job_dir)
    if not status:
        return jsonify({"error": "Job status unavailable."}), 404
    if status.get("status") == "completed":
        preview_filename = status.get("preview_filename")
        if not preview_filename or not (job_dir / Path(preview_filename)).exists():
            status = _update_job(
                job_dir,
                status="failed",
                error="Generated HTML is no longer available and must be regenerated.",
            )
    if status.get("status") != "completed":
        return jsonify(_build_job_response(job_id, status)), 409

    files = _build_file_manifest(job_dir)
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
    job_dir = RUNS_DIR / job_id
    if not job_dir.exists():
        abort(404)
    return send_from_directory(job_dir, filename)


@app.get("/runs/<job_id>/download")
def download_job_zip(job_id: str):
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
