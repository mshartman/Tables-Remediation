import io
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from reader_local import app


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def soup(self, response):
        return BeautifulSoup(response.data, 'html.parser')

    def check_error(self, response, status):
        self.assertEqual(response.status_code, status)
        soup = self.soup(response)
        self.assertTrue(soup.title.string.startswith('Upload error'))
        self.assertEqual(soup.select_one('#document')['aria-invalid'], 'true')
        for identifier in soup.select_one('#document')['aria-describedby'].split():
            self.assertIsNotNone(soup.find(id=identifier))
        self.assertIsNotNone(soup.select_one('a[href="#document"]'))
        self.assertIn("default-src 'none'", response.headers['Content-Security-Policy'])
        return soup

    def test_initial_instructions(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        soup = self.soup(response)
        self.assertIn('40 MiB', soup.select_one('#document-help').get_text())
        self.assertFalse(soup.select_one('#document').has_attr('aria-invalid'))
        self.assertFalse(soup.select_one('[name=describe_images]').has_attr('checked'))

    def test_missing_file_retains_escaped_title_and_choice(self):
        title = '"><script>alert(1)</script>'
        soup = self.check_error(self.client.post('/preview', data={
            'title': title, 'describe_images': 'on'}), 400)
        self.assertEqual(soup.select_one('#title')['value'], title)
        self.assertIsNone(soup.script)
        self.assertTrue(soup.select_one('[name=describe_images]').has_attr('checked'))

    def test_unsupported_file(self):
        self.check_error(self.client.post('/preview', data={
            'document': (io.BytesIO(b'example'), 'example.txt')}), 400)

    def test_conversion_failure_keeps_title(self):
        with patch('reader_local.extract_article', side_effect=ValueError('invalid PDF')):
            soup = self.check_error(self.client.post('/preview', data={
                'title': 'My article', 'document': (io.BytesIO(b'bad'), 'bad.pdf')}), 400)
        self.assertEqual(soup.select_one('#title')['value'], 'My article')

    def test_oversized_upload_does_not_reparse_form(self):
        with patch.dict(app.config, MAX_CONTENT_LENGTH=512):
            soup = self.check_error(self.client.post('/preview', data={
                'document': (io.BytesIO(b'x' * 1024), 'big.pdf')}), 413)
        self.assertIn('smaller file', soup.select_one('#document-error').get_text())
        self.assertFalse(soup.select_one('[name=describe_images]').has_attr('checked'))


if __name__ == '__main__':
    unittest.main()
