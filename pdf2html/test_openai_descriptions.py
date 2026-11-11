import unittest
from unittest.mock import patch
from bs4 import BeautifulSoup
from openai_descriptions import describe_openai, request_description
import json
import tempfile
from pathlib import Path
from PIL import Image
from openai_descriptions import local_image_data


class OpenAITests(unittest.TestCase):
    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-only'})
    def test_layout_scan_is_excluded_and_caption_is_passed(self):
        source = ('<figure class="fallback-image"><img data-alt-status="missing" src="data:image/png;base64,scan"></figure>'
                  '<figure><img data-alt-status="missing" data-figure-caption="Figure 2: stages" src="data:image/png;base64,chart"></figure>')
        with patch('openai_descriptions.request_description', return_value=('Two connected stages.', {})) as call:
            html, report = describe_openai(source)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[1], 'Figure 2: stages')
        self.assertEqual(report['skipped'], 1)

    def test_local_image_is_job_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'job'; root.mkdir()
            Image.new('RGB', (2, 2)).save(root / 'image.png')
            Image.new('RGB', (2, 2)).save(Path(folder) / 'outside.png')
            self.assertTrue(local_image_data('image.png', root, root).startswith('data:image/png;base64,'))
            self.assertEqual(local_image_data('../outside.png', root, root), '')
            self.assertEqual(local_image_data('https://example.org/image.png', root, root), '')

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-only', 'OPENAI_ALT_MAX_IMAGES': '1'})
    def test_placeholder_and_configured_cap(self):
        with patch('openai_descriptions.request_description', return_value=('A chart showing two groups.', {})) as call:
            html, report = describe_openai('<img alt="CHART" src="data:image/png;base64,a"><img src="data:image/png;base64,b">')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(report['generated'], 1)
        self.assertEqual(report['remaining'], 1)

    @patch.dict('os.environ', {'OPENAI_API_KEY': ''})
    def test_missing_key_never_calls(self):
        with patch('openai_descriptions.request_description') as call:
            _, report = describe_openai('<img>')
        call.assert_not_called()
        self.assertEqual(report['status'], 'openai_not_configured')

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-only'})
    def test_cap_preservation_and_scan_exclusion(self):
        source = '<img alt="Authored"><img alt="">' + ''.join(
            '<img src="data:image/png;base64,' + str(i) + '">' for i in range(6))
        source += '<details class="reader-source-scan"><img src="data:image/png;base64,scan"></details>'
        with patch('openai_descriptions.request_description', return_value=('A diagram with two connected stages.', {'input_tokens': 20})) as call:
            html, report = describe_openai(source)
        self.assertEqual(call.call_count, 4)
        self.assertEqual(report['remaining'], 3)
        self.assertEqual(BeautifulSoup(html, 'html.parser').img['alt'], 'Authored')

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-only'})
    def test_failure_has_no_retry_or_secret(self):
        with patch('openai_descriptions.request_description', side_effect=TimeoutError('secret')) as call:
            _, report = describe_openai('<img src="data:image/png;base64,x">')
        self.assertEqual(call.call_count, 1)
        self.assertNotIn('secret', str(report))
        self.assertEqual(report['failed'], 1)

    def test_request_is_bounded_and_not_stored(self):
        result = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Two linked circles.'}]}], 'usage': {}}
        with patch('openai_descriptions.urlopen') as call:
            call.return_value.__enter__.return_value.read.return_value = json.dumps(result).encode()
            text, _ = request_description('data:image/png;base64,x', '', 'test-only')
        payload = json.loads(call.call_args.args[0].data)
        self.assertFalse(payload['store'])
        self.assertEqual(payload['max_output_tokens'], 600)
        self.assertEqual(text, 'Two linked circles.')
