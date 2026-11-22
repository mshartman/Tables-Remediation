"""Local faculty demo checks, using synthetic PDFs and blocking network calls."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
import faculty_demo as demo


class InlineExecutor:
    def submit(self, function, *args):
        function(*args)


class FacultyDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = demo.create_app(Path(self.tmp.name), InlineExecutor())
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.responses = []
        self.home = self.get('/')
        soup = BeautifulSoup(self.home.data, 'html.parser')
        self.token = soup.find('meta', attrs={'name': 'demo-csrf'})['content']
        self.pdf = self.get('/sample.pdf').data

    def tearDown(self):
        for response in self.responses:
            response.close()
        self.tmp.cleanup()

    def get(self, url, client=None):
        response = (client or self.client).get(url)
        self.responses.append(response)
        return response

    def post(self, content=None, **extra):
        fields = {'pdf_file': (io.BytesIO(self.pdf if content is None else content), 'sample.pdf'),
                  'consent': 'yes', 'title': 'Sample course reading'}
        fields.update(extra)
        return self.client.post('/api/demo/jobs', data=fields, headers={'X-Demo-CSRF': self.token})

    def test_page_brand_accessibility_and_demo_disclosure(self):
        soup = BeautifulSoup(self.home.data, 'html.parser')
        self.assertEqual(len(soup.select('h1')), 1)
        self.assertEqual(len(soup.select('main')), 1)
        self.assertEqual(soup.html['lang'], 'en')
        self.assertIn('No documents are sent to AWS or an AI service', soup.get_text())
        self.assertIn('Not a remediated PDF', soup.get_text())
        ids = [n['id'] for n in soup.select('[id]')]
        self.assertEqual(len(ids), len(set(ids)))
        for field in soup.select('input'):
            self.assertIsNotNone(soup.find('label', attrs={'for': field['id']}))
        for node in soup.select('[aria-describedby], [aria-labelledby]'):
            for attr in ('aria-describedby', 'aria-labelledby'):
                for target in node.get(attr, '').split():
                    self.assertIn(target, ids)

    def test_sample_is_valid_two_page_pdf(self):
        with demo.pymupdf.open(stream=self.pdf, filetype='pdf') as source:
            self.assertEqual(len(source), 2)
            self.assertIn('inclusive', source[0].get_text())

    def test_real_conversion_no_network_and_downloads(self):
        with patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
            response = self.post()
        self.assertEqual(response.status_code, 202)
        job = response.get_json()
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['pages'], 2)
        self.assertGreater(job['headings'], 0)
        view = self.get(job['view_url'])
        self.assertEqual(view.status_code, 200)
        self.assertIn(b'A more flexible reading experience', view.data)
        self.assertIn('sha256-', view.headers['Content-Security-Policy'])
        self.assertEqual(self.get(job['download_url']).status_code, 200)
        report = self.get(job['report_url'])
        self.assertEqual(report.status_code, 200)
        self.assertTrue(report.get_json()['manual_checks'])
        self.assertNotIn('owner', job)
        self.assertNotIn('folder', job)
        self.assertEqual(self.get('/api/demo/jobs').get_json()['jobs'][0]['id'], job['id'])

    def test_other_browser_cannot_read_jobs(self):
        job = self.post().get_json()
        stranger = self.app.test_client()
        self.get('/', stranger)
        self.assertEqual(self.get('/api/demo/jobs', stranger).get_json()['jobs'], [])
        for url in ('/api/demo/jobs/' + job['id'], job['view_url'], job['download_url'], job['report_url']):
            self.assertEqual(self.get(url, stranger).status_code, 404)

    def test_csrf_and_consent_required(self):
        response = self.client.post('/api/demo/jobs', data={'consent': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post(consent='').status_code, 400)
        self.assertEqual(len(self.app.extensions['faculty_demo']['jobs']), 0)

    def test_invalid_pdf_and_wrong_extension_rejected(self):
        self.assertEqual(self.post(b'not a pdf').status_code, 400)
        response = self.post(pdf_file=(io.BytesIO(self.pdf), 'notes.exe'))
        self.assertEqual(response.status_code, 400)
        response = self.post(b'%PDF- invalid content')
        self.assertEqual(response.status_code, 400)

    def test_password_protected_pdf_rejected(self):
        with demo.pymupdf.open() as doc:
            doc.new_page()
            locked = doc.tobytes(encryption=demo.pymupdf.PDF_ENCRYPT_AES_256, user_pw='demo-password')
        self.assertEqual(self.post(locked).status_code, 400)

    def test_page_and_file_limits(self):
        with demo.pymupdf.open() as doc:
            for _ in range(81):
                doc.new_page()
            content = doc.tobytes()
        self.assertEqual(self.post(content).status_code, 400)
        with patch.object(demo, 'MAX_FILE', 20):
            self.assertEqual(self.post(self.pdf).status_code, 413)

    def test_source_filename_cannot_control_output_path(self):
        response = self.post(pdf_file=(io.BytesIO(self.pdf), '../../private/sample.pdf'), title='<img src=x onerror=alert(1)>')
        job = response.get_json()
        self.assertEqual(job['filename'], 'sample.pdf')
        root = Path(self.tmp.name)
        self.assertEqual([p.name for p in root.iterdir()], [job['id']])
        soup = BeautifulSoup(self.get(job['view_url']).data, 'html.parser')
        self.assertEqual(soup.h1.get_text(), '<img src=x onerror=alert(1)>')
        self.assertFalse(soup.select('[onerror]'))

    def test_blank_scan_is_flagged_not_certified(self):
        with demo.pymupdf.open() as doc:
            doc.new_page()
            blank = doc.tobytes()
        job = self.post(blank).get_json()
        self.assertEqual(job['pages_without_text'], 1)
        self.assertEqual(job['status'], 'completed')
        report = self.get(job['report_url']).get_json()
        self.assertIn('does not perform OCR', report['manual_checks'][0])

    def test_security_headers_and_local_assets(self):
        self.assertEqual(self.home.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.home.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", self.home.headers['Content-Security-Policy'])
        soup = BeautifulSoup(self.home.data, 'html.parser')
        for node in soup.select('[src], link[href]'):
            url = node.get('src') or node.get('href')
            self.assertTrue(url.startswith('/assets/'))
        for asset in ('faculty.css', 'faculty.js', 'fonts/ZillaSlab-SemiBold.ttf', 'fonts/OpenSans.ttf'):
            self.assertEqual(self.get('/assets/' + asset).status_code, 200)

    def test_untrusted_host_is_rejected(self):
        response = self.client.get('/', base_url='http://untrusted.example')
        self.assertEqual(response.status_code, 400)

    def test_conversion_failure_does_not_expose_exception(self):
        with patch.object(demo, 'extract_article', side_effect=RuntimeError('secret internal path')):
            job = self.post().get_json()
        self.assertEqual(job['status'], 'failed')
        self.assertNotIn('secret internal path', job['error'])

    def test_queue_limit_rejects_duplicate_busy_uploads(self):
        with self.client.session_transaction() as session:
            owner = session['owner']
        state = self.app.extensions['faculty_demo']
        for n in range(2):
            state['jobs'][str(n)] = {'status': 'queued', 'owner': owner}
        self.assertEqual(self.post().status_code, 429)


if __name__ == '__main__':
    unittest.main()
