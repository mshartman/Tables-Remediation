"""Local article previews with optional, explicitly selected vision providers."""
from html import escape
from pathlib import Path
from uuid import uuid4
import json

from bs4 import BeautifulSoup
from flask import Flask, abort, redirect, render_template_string, request, send_file, url_for
from pypdf import PdfReader

from reader_view import CSS, READER_SCRIPT_HASH, render_reader
from pdf_reader_extract import extract_article
from image_descriptions import describe_missing_images
from reader_audit import audit_reader
from openai_descriptions import describe_openai
from local_vision_descriptions import describe_local

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / 'local_runs' / 'reader'
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024

HOME = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{% if error %}Upload error - {% endif %}Article Preview Lab</title>
<style>{{ css|safe }} label,input,button{display:block;margin:12px 0}input,button{font:inherit;padding:10px;max-width:100%}button{background:#143d5a;color:white;border:0;border-radius:6px;cursor:pointer}</style></head>
<body><main class="reader"><header class="reader-header"><p>Local workspace</p><h1>Article Preview Lab</h1>
<p>Explore a cleaner reading experience</p></header><section class="reader-content">
{% if error %}<section aria-labelledby="error-heading"><h2 id="error-heading">We could not create your reading view</h2>
<p id="document-error">{{ error }}</p><p><a href="#document">Choose a file and try again</a>. Select the file again before submitting.</p></section>{% endif %}
<form method="post" action="/preview" enctype="multipart/form-data">
<label for="document">Choose an HTML article or PDF</label>
<p id="document-help">Required. Accepted formats: PDF, HTML and HTM. Choose a file smaller than 40 MiB; the complete upload, including form data, must fit within 40 MiB. PDFs must be readable without a password. HTML images must be embedded in the file.</p>
<input id="document" name="document" type="file" accept=".html,.htm,.pdf" required aria-describedby="document-help{% if error %} document-error{% endif %}"{% if error %} aria-invalid="true"{% endif %}>
<label for="title">Article title (optional)</label><input id="title" name="title" type="text" maxlength="300" value="{{ entered_title }}">
<label><input type="checkbox" name="describe_images"{% if describe_images %} checked{% endif %}> Generate missing image descriptions (sends images and available captions to the selected provider; API charges may apply)</label>
<label for="image-provider">Image description provider</label>
<select id="image-provider" name="image_provider"><option value="local_qwen"{% if image_provider == 'local_qwen' %} selected{% endif %}>Campus AI11: Qwen 3.5 9B (default)</option><option value="local_llava"{% if image_provider == 'local_llava' %} selected{% endif %}>Campus AI11: LLaVA Llama 3 8B</option><option value="openai"{% if image_provider == 'openai' %} selected{% endif %}>OpenAI GPT-5 nano (paid, manual fallback)</option><option value="ollama"{% if image_provider == 'ollama' %} selected{% endif %}>Legacy configured Ollama vision model</option></select>
<p>Campus vision is paused by default pending output-quality review. An administrator must explicitly enable CAMPUS_VISION_ENABLED before campus requests can run. Selecting a campus provider alone does not enable it. Campus and OpenAI testing is limited to four calls per upload. A failed or disabled campus request never switches to OpenAI automatically.</p>
<p id="processing-help">Conversion can take several minutes. After submitting, wait for the reading page to open. Avoid submitting again while it is processing.</p>
<button aria-describedby="processing-help">Create reading view</button></form>
<p>HTML preserves existing document structure. Images must be embedded in the HTML; separate image folders are not uploaded.</p>
<p>PDF preview reconstructs text blocks, basic column order, and scientific notation, and includes embedded images. Complex tables and scanned pages still need further work.</p>
<p>AWS is never called. Image descriptions are optional and require server-side credentials. OpenAI uses low-detail images for economy, which may miss small labels. Generated descriptions require human review.</p>
<a href="/demo">Open the example reading page</a></section></main></body></html>'''


def clean_html(text):
    soup = BeautifulSoup(text, 'html.parser')
    for tag in soup.select('script, iframe, object, embed, form, base, link, meta[http-equiv]'):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.startswith('on') or attr in {'style', 'srcset', 'srcdoc', 'background'}:
                del tag[attr]
        if tag.has_attr('href') and not str(tag['href']).strip().lower().startswith(('https://', 'http://', '#', 'mailto:')):
            del tag['href']
        if tag.name == 'img' and not str(tag.get('src', '')).startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
            note = soup.new_tag('p'); note.string = '[Image not included in this upload: ' + str(tag.get('alt', 'no description')) + ']'
            tag.replace_with(note)
    return str(soup)


@app.after_request
def secure_preview(response):
    response.headers['Content-Security-Policy'] = ("default-src 'none'; script-src 'sha256-" + READER_SCRIPT_HASH + "'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.get('/')
def home():
    return upload_page()


def upload_page(error=None, status=200, preserve=False):
    return render_template_string(
        HOME, css=CSS, error=error,
        entered_title=request.form.get('title', '')[:300] if preserve else '',
        describe_images=bool(request.form.get('describe_images')) if preserve else False,
        image_provider=request.form.get('image_provider', 'local_qwen') if preserve else 'local_qwen',
    ), status


@app.errorhandler(413)
def upload_too_large(error):
    # Do not access request.form: parsing this oversized request would raise again.
    return upload_page('The upload exceeds the 40 MiB limit. Choose a smaller file or split the PDF into smaller documents. Your title and image-description choice could not be recovered; enter them again.', 413)


@app.post('/preview')
def preview():
    upload = request.files.get('document')
    if not upload or Path(upload.filename or '').suffix.lower() not in {'.pdf', '.html', '.htm'}:
        return upload_page('Choose a PDF, HTML or HTM file. Other file formats are not supported.', 400, preserve=True)
    title = request.form.get('title', '').strip()[:300] or Path(upload.filename).stem
    try:
        table_report = []
        structure_report = {'schema_version': 1, 'status': 'not_applicable_html_input'}
        if upload.filename.lower().endswith('.pdf'):
            source = extract_article(upload.read(), table_report=table_report, title=title,
                                     structure_report=structure_report)
        else:
            source = clean_html(upload.read().decode('utf-8-sig', errors='replace'))
        alt_report = {'status': 'not_requested'}
        if request.form.get('describe_images'):
            provider = request.form.get('image_provider', 'local_qwen')
            if provider in {'local_qwen', 'local_llava'}:
                source, alt_report = describe_local(source, provider)
            elif provider == 'ollama':
                source, alt_report = describe_missing_images(source)
            elif provider == 'openai':
                source, alt_report = describe_openai(source)
            else:
                return upload_page('Choose a supported image description provider.', 400, preserve=True)
            parsed = BeautifulSoup(source, 'html.parser')
            notice = parsed.new_tag('p')
            notice.string = ('AI-generated image descriptions: ' + str(alt_report.get('generated', 0))
                            + '. Status: ' + alt_report['status'].replace('_', ' ') + '. Descriptions have not been human-verified.')
            (parsed.body or parsed).insert(0, notice)
            source = str(parsed)
        job = uuid4().hex
        folder = RUNS / job; folder.mkdir(parents=True)
        target = folder / 'article.html'
        target.write_text(source, encoding='utf-8')
        (folder / 'document-structure.json').write_text(json.dumps(structure_report, indent=2), encoding='utf-8')
        (folder / 'table-extraction-report.json').write_text(json.dumps(table_report, indent=2), encoding='utf-8')
        (folder / 'image-description-report.json').write_text(json.dumps(alt_report, indent=2), encoding='utf-8')
        render_reader(target, title)
        audit = audit_reader(target.read_text(encoding='utf-8'), table_report)
        (folder / 'accessibility-report.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
        return redirect(url_for('article', job=job))
    except Exception:
        app.logger.warning('Document preview failed')
        return upload_page('This document could not be processed. Try a readable, unencrypted PDF or a UTF-8 HTML file. If the problem continues with a valid file, contact the person running this local preview tool.', 400, preserve=True)


@app.get('/article/<job>')
def article(job):
    if len(job) != 32 or any(c not in '0123456789abcdef' for c in job):
        abort(404)
    target = RUNS / job / 'article.html'
    if not target.is_file():
        abort(404)
    return send_file(target)


@app.get('/demo')
def demo():
    return send_file(ROOT / 'reader-demo.html')


@app.get('/article/<job>/accessibility-report')
def accessibility_report(job):
    if len(job) != 32 or any(c not in '0123456789abcdef' for c in job):
        abort(404)
    folder = RUNS / job
    target = folder / 'article.html'
    if not target.is_file():
        abort(404)
    table_path = folder / 'table-extraction-report.json'
    tables = json.loads(table_path.read_text(encoding='utf-8')) if table_path.exists() else None
    report = audit_reader(target.read_text(encoding='utf-8'), tables)
    if tables is None:
        report['manual_checks'].append('This older output has no table extraction report; regenerate to check table detection.')
    return report


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8001, debug=False)
