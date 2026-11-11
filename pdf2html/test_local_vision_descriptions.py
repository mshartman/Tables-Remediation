import io
import json
import unittest
from unittest.mock import patch
from bs4 import BeautifulSoup
from local_vision_descriptions import describe_local, request_description
from reader_local import app


@patch.dict('os.environ', {'CAMPUS_VISION_ENABLED': 'true'})
class LocalVisionTests(unittest.TestCase):
    def test_disabled_even_with_credentials(self):
        with patch.dict('os.environ', {'CAMPUS_VISION_ENABLED': 'false', 'LITELLM_API_KEY': 'test-only'}), patch('local_vision_descriptions.request_description') as call:
            html = '<img src="data:image/png;base64,a">'
            output, report = describe_local(html)
        call.assert_not_called()
        self.assertEqual(output, html)
        self.assertEqual(report['status'], 'campus_vision_disabled')
        self.assertEqual(report['calls'], 0)

    @patch.dict('os.environ', {'LITELLM_API_KEY': ''})
    def test_missing_key(self):
        with patch('local_vision_descriptions.request_description') as call:
            _, report = describe_local('<img>')
        call.assert_not_called()
        self.assertEqual(report['status'], 'local_vision_not_configured')

    @patch.dict('os.environ', {'LITELLM_API_KEY': 'test-only'})
    def test_cap_cache_and_preservation(self):
        html = '<img alt="authored"><img alt="">' + ''.join(
            f'<img src="data:image/png;base64,{i}">' for i in range(6))
        html += '<img src="data:image/png;base64,0">'
        html += '<details class="reader-source-scan"><img src="data:image/png;base64,scan"></details>'
        with patch('local_vision_descriptions.request_description', return_value=('Two connected boxes.', {})) as call:
            output, report = describe_local(html, 'local_llava')
        self.assertEqual(call.call_count, 4)
        self.assertEqual(report['generated'], 5)
        self.assertEqual(report['remaining'], 3)
        self.assertEqual(call.call_args.args[3], 'ai11-llava-llama3-8b')
        self.assertEqual(BeautifulSoup(output, 'html.parser').img['alt'], 'authored')

    @patch.dict('os.environ', {'LITELLM_API_KEY': 'test-only'})
    def test_failure_stops_without_fallback(self):
        with patch('local_vision_descriptions.request_description', side_effect=TimeoutError('secret')) as call, patch('reader_local.describe_openai') as paid:
            _, report = describe_local('<img src="data:image/png;base64,a"><img src="data:image/png;base64,b">')
        self.assertEqual(call.call_count, 1)
        paid.assert_not_called()
        self.assertEqual(report['failed'], 1)
        self.assertNotIn('secret', str(report))

    def test_response_and_request(self):
        reply = {'choices': [{'finish_reason': 'stop', 'message': {
            'content': '<think>Internal reasoning</think>Two connected boxes.',
            'reasoning_content': 'Private reasoning'}}]}
        with patch('local_vision_descriptions.urlopen') as call:
            call.return_value.__enter__.return_value.read.return_value = json.dumps(reply).encode()
            text, _ = request_description('data:image/png;base64,a', 'caption', 'test-only', 'ai11-qwen3.5-9b')
        self.assertEqual(text, 'Two connected boxes.')
        self.assertEqual(call.call_args.kwargs['timeout'], 180)
        self.assertIn('library.stonybrook.edu/LiteLLM/', call.call_args.args[0].full_url)

    def test_truncation_is_not_alt_text(self):
        with patch('local_vision_descriptions.urlopen') as call:
            call.return_value.__enter__.return_value.read.return_value = b'{"choices":[{"finish_reason":"length"}]}'
            with self.assertRaises(ValueError):
                request_description('data:image/png;base64,a', '', 'test-only', 'ai11-qwen3.5-9b')

    def test_default_ui_and_unknown_provider(self):
        client = app.test_client()
        self.assertIn(b'value="local_qwen" selected', client.get('/').data)
        with patch('reader_local.describe_openai') as paid:
            response = client.post('/preview', data={'document': (io.BytesIO(b'<p>Test</p>'), 'a.html'),
                'describe_images': 'on', 'image_provider': 'unknown'})
        self.assertEqual(response.status_code, 400)
        paid.assert_not_called()


if __name__ == '__main__':
    unittest.main()
