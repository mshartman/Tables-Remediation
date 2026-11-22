"""Loopback-only faculty upload demonstration. No AWS or AI-provider calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import secrets
import threading
from uuid import uuid4

from bs4 import BeautifulSoup
from flask import Flask, abort, jsonify, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

from pdf_reader_extract import extract_article, pymupdf
from reader_audit import audit_reader
from reader_view import READER_SCRIPT_HASH, render_reader

ROOT = Path(__file__).resolve().parent
MAX_FILE = 20 * 1024 * 1024
MAX_PAGES = 80


def create_app(output_root=None, executor=None):
    app = Flask(__name__, static_folder='faculty_demo_assets', static_url_path='/assets')
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=MAX_FILE + 1024 * 1024,
                      SESSION_COOKIE_NAME='faculty_demo_session', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    state = {'jobs': {}, 'lock': threading.RLock(),
             'executor': executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='faculty-demo'),
             'root': Path(output_root) if output_root else ROOT / 'local_runs' / 'faculty_demo'}
    app.extensions['faculty_demo'] = state

    def public(job):
        with state['lock']:
            job = dict(job)
        result = {key: value for key, value in job.items() if key not in {'owner', 'folder'}}
        if job['status'] == 'completed':
            result.update(view_url=url_for('reading_view', job_id=job['id']),
                          download_url=url_for('download_view', job_id=job['id']),
                          report_url=url_for('download_report', job_id=job['id']))
        return result

    def owned(job_id):
        with state['lock']:
            job = state['jobs'].get(job_id)
            if not job or job['owner'] != session.get('owner'):
                abort(404)
            return dict(job)

    def update(job_id, **values):
        with state['lock']:
            state['jobs'][job_id].update(values)

    def process(job_id, data):
        try:
            update(job_id, status='processing', phase='extracting')
            with state['lock']:
                job = dict(state['jobs'][job_id])
            tables, structure = [], {}
            html = extract_article(data, title=job['title'], table_report=tables, structure_report=structure)
            folder = state['root'] / job_id
            folder.mkdir(parents=True, exist_ok=False)
            target = folder / 'reading-view.html'
            target.write_text(html, encoding='utf-8')
            update(job_id, phase='structuring')
            render_reader(target, job['title'])
            update(job_id, phase='checking')
            text = target.read_text(encoding='utf-8')
            audit = audit_reader(text, tables)
            audit['demo_notice'] = 'Local heuristic conversion and static checks only. No AI or AWS was used. Human review is required.'
            if job['pages_without_text']:
                audit['manual_checks'].insert(0, 'Some source pages have no selectable text. This demo does not perform OCR; provide a transcription or OCR before relying on them.')
            (folder / 'review-notes.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
            soup = BeautifulSoup(text, 'html.parser')
            update(job_id, status='completed', phase='ready', folder=str(folder),
                   headings=len(soup.select('main h2, main h3, main h4, main h5, main h6')),
                   tables=len(soup.select('table')), review_items=sum(item.get('count', 1) for item in audit['findings']),
                   images_needing_review=sum(item['count'] for item in audit['findings'] if item['code'] == 'missing_image_alternative'))
        except Exception:
            update(job_id, status='failed', phase='failed', error='We could not create a reading view for this PDF. Try a smaller, text-based PDF. This local demo does not repair damaged files or perform OCR.')

    @app.after_request
    def headers(response):
        is_view = request.endpoint in {'reading_view', 'download_view'}
        script = "'sha256-" + READER_SCRIPT_HASH + "'" if is_view else "'self'"
        style = "'unsafe-inline'" if is_view else "'self'"
        response.headers['Content-Security-Policy'] = ("default-src 'none'; script-src " + script +
            '; style-src ' + style + "; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error='Choose a PDF smaller than 20 MB.'), 413

    @app.get('/')
    def home():
        session.setdefault('owner', secrets.token_hex(24))
        session.setdefault('csrf', secrets.token_hex(24))
        return render_template('faculty_demo.html', csrf=session['csrf'])

    @app.get('/api/demo/jobs')
    def jobs():
        with state['lock']:
            items = [public(job) for job in state['jobs'].values() if job['owner'] == session.get('owner')]
        return jsonify(jobs=list(reversed(items)))

    @app.post('/api/demo/jobs')
    def create_job():
        token = request.headers.get('X-Demo-CSRF', '')
        expected = session.get('csrf', '')
        if not session.get('owner') or not expected or not secrets.compare_digest(token, expected):
            return jsonify(error='Refresh this page before submitting a document.'), 403
        if request.form.get('consent') != 'yes':
            return jsonify(error='Confirm your permission to process the document before continuing.'), 400
        upload = request.files.get('pdf_file')
        if not upload or not upload.filename or not upload.filename.lower().endswith('.pdf'):
            return jsonify(error='Choose a PDF file to continue.'), 400
        data = upload.stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            return jsonify(error='Choose a PDF smaller than 20 MB.'), 413
        if not data or b'%PDF-' not in data[:1024]:
            return jsonify(error='This file does not appear to be a readable PDF.'), 400
        try:
            with pymupdf.open(stream=data, filetype='pdf') as doc:
                if doc.needs_pass:
                    return jsonify(error='This PDF requires a password. Upload an unlocked copy.'), 400
                pages = len(doc)
                if not 1 <= pages <= MAX_PAGES:
                    return jsonify(error='Choose a PDF with between 1 and 80 pages for this demo.'), 400
                pages_without_text = sum(not page.get_text().strip() for page in doc)
        except Exception:
            return jsonify(error='We could not read this PDF. Try a different, unencrypted copy.'), 400
        filename = upload.filename.replace('\\', '/').rsplit('/', 1)[-1][:160]
        title = request.form.get('title', '').strip()[:160] or Path(filename).stem
        job_id = uuid4().hex
        with state['lock']:
            pending = [j for j in state['jobs'].values() if j['status'] in {'queued', 'processing'}]
            if len(pending) >= 4 or sum(j['owner'] == session['owner'] for j in pending) >= 2:
                return jsonify(error='The local demo is busy. Wait for a document to finish, then try again.'), 429
            state['jobs'][job_id] = {'id': job_id, 'owner': session['owner'], 'filename': filename,
                                    'title': title, 'pages': pages, 'bytes': len(data),
                                    'pages_without_text': pages_without_text, 'status': 'queued', 'phase': 'queued',
                                    'created': datetime.now(timezone.utc).isoformat()}
        state['executor'].submit(process, job_id, data)
        return jsonify(public(state['jobs'][job_id])), 202

    @app.get('/api/demo/jobs/<job_id>')
    def job_status(job_id):
        return jsonify(public(owned(job_id)))

    def completed_file(job_id, filename, download=False):
        job = owned(job_id)
        if job['status'] != 'completed':
            abort(404)
        path = state['root'] / job_id / filename
        name = (secure_filename(job['title']) or 'document') + ('.html' if filename.endswith('.html') else '-review-notes.json')
        return send_file(path, as_attachment=download, download_name=name)

    @app.get('/documents/<job_id>')
    def reading_view(job_id):
        return completed_file(job_id, 'reading-view.html')

    @app.get('/documents/<job_id>/download')
    def download_view(job_id):
        return completed_file(job_id, 'reading-view.html', True)

    @app.get('/documents/<job_id>/review')
    def download_report(job_id):
        return completed_file(job_id, 'review-notes.json', True)

    @app.get('/sample.pdf')
    def sample():
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((58, 68), 'Designing inclusive course materials', fontsize=21, fontname='hebo')
            page.insert_text((58, 103), 'A sample teaching handout | University Libraries', fontsize=10)
            page.insert_text((58, 155), '1. A more flexible reading experience', fontsize=15, fontname='hebo')
            page.insert_textbox((58, 180, 530, 330),
                'Students engage with course readings in many different ways. Some use screen readers, '
                'some enlarge the text, and others read on a small screen. A structured HTML reading '
                'view allows the content to adapt without requiring everyone to use the same layout.\n\n'
                'Clear headings, meaningful links and well-structured tables make a document easier '
                'to navigate. These changes benefit readers who use assistive technology and readers '
                'who simply need a more flexible format.', fontsize=12)
            page.insert_text((58, 375), 'What this example demonstrates', fontsize=15, fontname='hebo')
            page.insert_textbox((58, 400, 530, 520),
                'The source includes selectable text and section headings. The local converter creates '
                'an HTML reading view and then checks a limited set of structural issues. It does not '
                'certify accessibility. Compare the output with this original before sharing it.', fontsize=12)
            page = doc.new_page()
            page.insert_text((58, 70), '2. Review before you share', fontsize=18, fontname='hebo')
            page.insert_textbox((58, 105, 530, 240),
                'Check that the reading order is logical and no information is missing. Test headings '
                'and links with a keyboard and screen reader. Verify table values and associations. '
                'Supply meaningful descriptions for any instructional figures.\n\n'
                'Automatic conversion is a starting point, not a substitute for a human accessibility review.', fontsize=12)
            return send_file(io.BytesIO(doc.tobytes()), mimetype='application/pdf',
                             as_attachment=True, download_name='inclusive-course-materials.pdf')

    return app


app = create_app()

if __name__ == '__main__':
    print('Faculty demo: http://127.0.0.1:8002 (local processing only; no AWS or AI calls)')
    app.run(host='127.0.0.1', port=8002, debug=False, threaded=True, use_reloader=False)
