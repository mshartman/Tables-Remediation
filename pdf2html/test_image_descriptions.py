import unittest
from unittest.mock import patch
from bs4 import BeautifulSoup
from image_descriptions import describe_missing_images, annotate_image_gaps, needs_description


class ImageDescriptionTests(unittest.TestCase):
    def test_legacy_placeholder_and_authored_empty_alt(self):
        soup = BeautifulSoup('<article><img alt="Extracted image on page 2; a descriptive alternative has not been supplied."><img alt=""><img alt="An authored description"><img><img alt="A diagram" data-alt-status="ai-generated"></article>', 'html.parser')
        self.assertTrue(needs_description(soup.img))
        for _ in range(2):
            annotate_image_gaps(soup, soup.article)
        self.assertEqual(len(soup.select('[data-reader-image-notice]')), 1)
        self.assertIn('2 image(s)', soup.aside.get_text())
        self.assertIn('1 image description(s)', soup.aside.get_text())
        self.assertEqual(soup.find_all('img')[1]['alt'], '')
        self.assertEqual(soup.find_all('img')[2]['alt'], 'An authored description')

    @patch.dict('os.environ', {'OLLAMA_API_KEY': '', 'OLLAMA_VISION_MODEL': ''})
    def test_unconfigured_reports_remaining_without_network(self):
        with patch('image_descriptions.request_json') as client:
            _, report = describe_missing_images('<img>')
        self.assertEqual(report['remaining'], 1)
        self.assertEqual(report['status'], 'vision_not_configured')
        client.assert_not_called()

    @patch.dict('os.environ', {'OLLAMA_API_KEY': 'test', 'OLLAMA_VISION_MODEL': 'test'})
    def test_preserves_authored_and_decorative_alt(self):
        source = '<img alt="Existing description"><img alt=""><img data-alt-status="missing" alt="Placeholder" src="data:image/png;base64,YWJj">'
        with patch('image_descriptions.request_json', side_effect=[
            {'capabilities': ['vision']},
            {'message': {'content': 'A diagram with two connected stages.'}},
        ]) as client:
            html, report = describe_missing_images(source)
        images = BeautifulSoup(html, 'html.parser').find_all('img')
        self.assertEqual(images[0]['alt'], 'Existing description')
        self.assertEqual(images[1]['alt'], '')
        self.assertEqual(report['generated'], 1)
        self.assertEqual(client.call_count, 2)

    @patch.dict('os.environ', {'OLLAMA_API_KEY': 'test', 'OLLAMA_VISION_MODEL': 'test'})
    def test_text_model_never_receives_images(self):
        source = '<img src="data:image/png;base64,YWJj">'
        with patch('image_descriptions.request_json', return_value={'capabilities': ['completion']}) as client:
            html, report = describe_missing_images(source)
        self.assertEqual(source, html)
        self.assertEqual(report['status'], 'model_has_no_vision')
        self.assertEqual(client.call_count, 1)

    @patch.dict('os.environ', {'OLLAMA_API_KEY': 'test', 'OLLAMA_VISION_MODEL': 'test'})
    def test_failure_preserves_document(self):
        source = '<img src="data:image/png;base64,YWJj">'
        with patch('image_descriptions.request_json', side_effect=TimeoutError):
            html, report = describe_missing_images(source)
        self.assertEqual(source, html)
        self.assertEqual(report['status'], 'server_unavailable')


if __name__ == '__main__':
    unittest.main()
