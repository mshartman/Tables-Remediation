"""100-page preflight and user messages; all conversion/cloud work is mocked."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import layout_compare
from pypdf import PdfWriter
import local_server as server
from content_accessibility_utility_on_aws.pdf2html import api
from content_accessibility_utility_on_aws.pdf2html.services.bedrock_client import ExtendedBDAClient
from content_accessibility_utility_on_aws.pdf2html.utils.pdf_validation import validate_pdf_pages
from content_accessibility_utility_on_aws.utils.job_errors import BDATimeoutError, PDFValidationError


def pdf(pages, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    if password:
        writer.encrypt(password)
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


class DocumentLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(server, 'RUNS_DIR', self.root).start()
        patch('socket.create_connection', side_effect=AssertionError('Network forbidden')).start()
        self.worker = patch.object(server, '_start_background_job').start()
        self.create = patch.object(server, '_create_job').start()
        self.cache = patch.object(server, '_find_existing_job', return_value=None).start()
        self.store = patch.object(server, 'get_store', return_value=None).start()
        self.client = server.app.test_client()

    def test_boundary_accepts_100_but_not_101(self):
        source = io.BytesIO(pdf(100))
        self.assertEqual(validate_pdf_pages(source), 100)
        self.assertEqual(source.tell(), 0)
        with self.assertRaises(PDFValidationError) as caught:
            validate_pdf_pages(io.BytesIO(pdf(101)))
        self.assertIn('101 pages', str(caught.exception))
        self.assertIn('split', str(caught.exception))

    def test_invalid_empty_and_encrypted_pdfs_fail_closed(self):
        for content in (b'%PDF-invalid', pdf(0), pdf(1, 'secret')):
            with self.assertRaises(PDFValidationError):
                validate_pdf_pages(io.BytesIO(content))

    def test_100_page_upload_is_accepted_without_running_conversion(self):
        self.create.return_value = {'status': 'queued'}
        response = self.client.post('/api/jobs', data={
            'pdf_file': (io.BytesIO(pdf(100)), 'boundary.pdf')})
        self.assertEqual(response.status_code, 202)
        self.create.assert_called_once()
        self.worker.assert_called_once()
        self.assertEqual(validate_pdf_pages(self.worker.call_args.kwargs['pdf_path']), 100)

    def test_upload_rejected_before_cache_job_or_worker(self):
        for endpoint in ('/api/jobs', '/api/table-jobs'):
            with redirect_stdout(io.StringIO()) as stream:
                response = self.client.post(endpoint, data={'pdf_file': (io.BytesIO(pdf(101)), 'PRIVATE.pdf')})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json['error_code'], 'document_page_limit')
            self.assertEqual(response.json['document_pages'], 101)
            self.assertEqual(response.json['max_document_pages'], 100)
            self.assertIn('100 pages or fewer', response.json['error'])
            self.assertNotIn('PRIVATE', response.json['error'])
            self.assertNotIn('job_queued', stream.getvalue())
        self.worker.assert_not_called()
        self.create.assert_not_called()
        self.cache.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_oversized_submission_does_not_touch_s3(self):
        self.store.return_value = Mock()
        response = self.client.post('/api/jobs', data={'pdf_file':(io.BytesIO(pdf(150)), 'large.pdf')})
        self.assertEqual(response.status_code, 422)
        self.cache.assert_not_called()
        self.worker.assert_not_called()

    def test_form_has_accessible_explanation_not_server_error(self):
        for endpoint in ('/process', '/table-lab/process'):
            response = self.client.post(endpoint, data={'pdf_file':(io.BytesIO(pdf(101)), 'large.pdf')})
            self.assertEqual(response.status_code, 422)
            self.assertIn(b'role="alert"', response.data)
            self.assertIn(b'101 pages', response.data)
            self.assertIn(b'100 pages or fewer', response.data)
        self.create.assert_not_called()

    def test_json_path_cannot_bypass_limit(self):
        source = self.root / 'large.pdf'
        source.write_bytes(pdf(101))
        response = self.client.post('/api/jobs', json={'pdf_path':str(source)})
        self.assertEqual(response.status_code, 422)
        self.worker.assert_not_called()
        self.create.assert_not_called()

    def test_shared_conversion_and_bda_client_reject_before_aws(self):
        source = self.root / 'large.pdf'
        source.write_bytes(pdf(101))
        with patch.object(api, 'resolve_bda_project') as resolve:
            with self.assertRaises(PDFValidationError):
                api.convert_pdf_to_html(str(source), str(self.root/'out'))
            resolve.assert_not_called()
        bda = ExtendedBDAClient.__new__(ExtendedBDAClient)
        bda.s3_client = Mock()
        with self.assertRaises(PDFValidationError):
            bda.process_and_retrieve(str(source), str(self.root/'out'), {})
        bda.s3_client.upload_file.assert_not_called()

    def test_shared_conversion_preserves_timeout_type_and_reason(self):
        source = self.root / 'small.pdf'
        source.write_bytes(pdf(1))
        with (patch.object(api, 'resolve_bda_project', return_value='project'),
              patch.object(api, 'ExtendedBDAClient') as client):
            client.return_value.process_and_retrieve.side_effect = BDATimeoutError(900)
            with self.assertRaises(BDATimeoutError) as caught:
                api.convert_pdf_to_html(str(source), str(self.root/'out'))
        self.assertIn('900-second', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
