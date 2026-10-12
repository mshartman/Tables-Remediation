"""Small, fully local web application for testing PDF table extraction.

This intentionally avoids the main PDF-to-HTML service and every AWS dependency.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from bs4 import BeautifulSoup
from flask import Flask, abort, redirect, render_template_string, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

try:
    # Used by the slim student-worker package, where this module is at root.
    from local_table_extractor import extract_pdf_tables_to_html
except ModuleNotFoundError:
    # Allows the same server to run from this full development repository.
    from content_accessibility_utility_on_aws.remediate.remediation_strategies.local_table_extractor import (
        extract_pdf_tables_to_html,
    )


BASE_DIR = Path(__file__).resolve().parent
RUNS_DIR = BASE_DIR / "table_lab_runs"
RUNS_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local PDF Table Lab</title><style>
:root { --paper:#fffaf3; --ink:#1d2433; --muted:#5a6474; --accent:#b5542e; --line:#dccfbd; }
* { box-sizing:border-box; } body { margin:0; color:var(--ink); background:#f5efe6; font-family:Georgia, "Times New Roman", serif; }
main { max-width:900px; margin:0 auto; padding:48px 20px 64px; } .eyebrow { color:var(--accent); font-size:.78rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }
h1 { margin:.4rem 0 1rem; font-size:clamp(2rem,5vw,3.4rem); line-height:1; } p { line-height:1.6; } .panel { padding:26px; border:1px solid var(--line); border-radius:18px; background:var(--paper); box-shadow:0 16px 36px rgba(56,43,24,.08); }
label { display:grid; gap:.5rem; font-weight:700; } input { padding:.8rem; border:1px solid var(--line); border-radius:10px; background:#fff; font:inherit; } button,.button { display:inline-block; margin-top:1rem; padding:.75rem 1.1rem; border:0; border-radius:999px; background:var(--accent); color:white; font:inherit; font-weight:700; text-decoration:none; cursor:pointer; }
.error { margin-bottom:1rem; padding:1rem; border-radius:10px; background:#f9dddd; color:#7f1d1d; } .note { color:var(--muted); }
</style></head><body><main><div class="eyebrow">Fully Local</div><h1>PDF Table Lab</h1>
<p>Upload a PDF to test local table extraction and accessible HTML rendering. No AWS, AI model, cloud storage, or external service is used.</p>
<section class="panel">{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form action="{{ url_for('process_pdf') }}" method="post" enctype="multipart/form-data"><label for="pdf_file">PDF file<input id="pdf_file" name="pdf_file" type="file" accept="application/pdf,.pdf" required></label><button type="submit">Create table HTML</button></form>
<p class="note">The result opens as an ordinary HTML page in a new browser tab. It is designed for testing and improving table extraction rules.</p></section></main></body></html>"""


RESULT_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Table Lab complete</title><style>body{margin:0;background:#f5efe6;color:#1d2433;font-family:Georgia,"Times New Roman",serif}main{max-width:760px;margin:0 auto;padding:48px 20px}a{color:#8e3f20;font-weight:700}.card{padding:24px;border:1px solid #dccfbd;border-radius:18px;background:#fffaf3}</style></head><body><main><h1>Table HTML created</h1><section class="card"><p>{{ tables }} table(s) were extracted from {{ pages }} PDF page(s).</p><p><a target="_blank" rel="noopener" href="{{ html_url }}">Open the HTML result in a new tab</a></p><p><a href="{{ audit_url }}">View the local extraction summary</a></p><p><a href="{{ home_url }}">Test another PDF</a></p></section></main></body></html>"""


OUTPUT_CSS = """
body { margin: 0; background: #f5f7fb; color: #18202a; font-family: Georgia, "Times New Roman", serif; line-height: 1.55; }
main { max-width: 1280px; margin: 0 auto; padding: 32px 24px 64px; }
nav { margin: 1.5rem 0; padding: 1rem 1.25rem; border: 1px solid #d8e0eb; border-radius: 12px; background: #fff; }
nav ul { display: flex; flex-wrap: wrap; gap: .25rem 1rem; margin: .5rem 0 0; padding: 0; list-style: none; }
.page { margin: 2rem 0; overflow-x: auto; }
table { width: 100%; min-width: 760px; border-collapse: collapse; border: 1px solid #63758a; background: #fff; }
caption { padding: .75rem .9rem; color: #102033; font-size: 1.05rem; font-weight: 700; text-align: left; caption-side: top; }
td, th { padding: .45rem .6rem; border: 1px solid #8c9bad; vertical-align: top; text-align: left; }
thead th { color: #102033; background: #e8eef5; font-weight: 700; }
tbody tr:nth-child(even):not(.table-section) { background: #f7f9fc; }
.table-section th { color: #102033; background: #eef3f8; font-style: italic; font-weight: 700; }
td:not(:first-child), th:not(:first-child) { white-space: nowrap; }
@media (max-width: 700px) { main { padding: 24px 16px 48px; } }
"""


def _style_output(path: Path) -> None:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    if not soup.head:
        soup.html.insert(0, soup.new_tag("head"))
    style = soup.new_tag("style")
    style.string = OUTPUT_CSS
    soup.head.append(style)
    path.write_text(str(soup), encoding="utf-8")


@app.get("/")
def home():
    return render_template_string(PAGE_TEMPLATE, error=request.args.get("error"))


@app.post("/process")
def process_pdf():
    uploaded = request.files.get("pdf_file")
    if not uploaded or not uploaded.filename or not uploaded.filename.lower().endswith(".pdf"):
        return redirect(url_for("home", error="Please choose a PDF file."))

    job_id = uuid.uuid4().hex[:12]
    job_dir = RUNS_DIR / job_id
    job_dir.mkdir()
    source_path = job_dir / secure_filename(uploaded.filename)
    uploaded.save(source_path)
    output_path = job_dir / "table-output.html"

    try:
        result = extract_pdf_tables_to_html(source_path, output_path, source_path.stem)
        _style_output(output_path)
    except Exception as error:
        return redirect(url_for("home", error=f"Could not process the PDF: {error}"))

    audit_path = job_dir / "extraction-summary.json"
    audit_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return render_template_string(
        RESULT_TEMPLATE,
        tables=result["tables_extracted"], pages=result["pages_with_tables"],
        html_url=url_for("job_file", job_id=job_id, filename=output_path.name),
        audit_url=url_for("job_file", job_id=job_id, filename=audit_path.name),
        home_url=url_for("home"),
    )


@app.get("/runs/<job_id>/<path:filename>")
def job_file(job_id: str, filename: str):
    directory = RUNS_DIR / job_id
    if not directory.is_dir():
        abort(404)
    return send_from_directory(directory, filename)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
