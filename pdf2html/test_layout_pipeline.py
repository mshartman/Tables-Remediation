"""Exercise the server integration with all cloud operations mocked."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('AWS_EC2_METADATA_DISABLED', 'true')
import layout_compare as lab
import layout_pipeline as pipeline
import local_server as server


class LayoutPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = self.root/'original.pdf'
        with lab.pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72,100), 'Sample document', fontsize=18)
            page.insert_text((72,140), 'Some readable source text.')
            doc.save(self.pdf)
        self.legacy = self.root/'legacy.html'
        self.legacy.write_text('<html><head><title>Sample</title></head><body><p>Legacy result</p></body></html>', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_does_not_import_or_run_renderer(self):
        with patch.dict(os.environ, {'READER_LAYOUT_MODE':'legacy'}), patch('layout_compare.compare') as call:
            self.assertIsNone(pipeline.build_layout_preview(self.pdf,self.root,self.legacy,{},'Sample'))
        call.assert_not_called()
        self.assertFalse((self.root/'layout-status.json').exists())

    def test_missing_bda_keeps_legacy(self):
        with patch.dict(os.environ, {'READER_LAYOUT_MODE':'bda'}):
            self.assertIsNone(pipeline.build_layout_preview(self.pdf,self.root,self.legacy,{},'Sample'))
        report = json.loads((self.root/'layout-status.json').read_text())
        self.assertEqual(report['status'], 'fallback')

    def test_renderer_failure_keeps_legacy_without_error_payload(self):
        with patch.dict(os.environ, {'READER_LAYOUT_MODE':'geometry'}), patch('layout_compare.compare',side_effect=RuntimeError('private payload')):
            self.assertIsNone(pipeline.build_layout_preview(self.pdf,self.root,self.legacy,{},'Sample'))
        text = (self.root/'layout-status.json').read_text()
        self.assertNotIn('private payload', text)
        self.assertTrue(self.legacy.exists())

    def test_preview_selection_is_shared(self):
        result = {'conversion_result':{'html_path':'converted.html'}}
        self.assertEqual(pipeline.preview_path(result), Path('converted.html'))
        result['remediation_result'] = {'remediated_html_path':'remediated.html'}
        self.assertEqual(pipeline.preview_path(result), Path('remediated.html'))
        result['layout_preview_path'] = 'layout/layout.html'
        self.assertEqual(pipeline.preview_path(result), Path('layout/layout.html'))

    def test_full_pipeline_selects_layout_and_does_not_rewrap_it(self):
        output = self.root/'output'; output.mkdir()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'READER_LAYOUT_MODE':'geometry', 'IMAGE_ALT_PROVIDER':'openai'}))
            stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('Network forbidden')))
            conversion = stack.enter_context(patch.object(server, 'convert_pdf_to_html', return_value={'html_path':str(self.legacy)}))
            stack.enter_context(patch.object(server, 'suggest_headings', return_value='disabled'))
            style = stack.enter_context(patch.object(server, '_style_html_output'))
            stack.enter_context(patch.object(server, '_detect_image_dir', return_value=None))
            stack.enter_context(patch.object(server, 'audit_html_accessibility', return_value={'issues':[]}))
            stack.enter_context(patch.object(server, 'remediate_html_accessibility', return_value={'remediated_html_path':str(self.legacy)}))
            stack.enter_context(patch.object(server, 'generate_remediation_report'))
            stack.enter_context(patch.object(server, 'save_usage_data', return_value=str(output/'usage.json')))
            stack.enter_context(patch.object(server, '_reset_usage_tracker'))
            describe = stack.enter_context(patch.object(server, 'describe_openai', side_effect=lambda text,**kw:(text,{'calls':0})))
            result = server._run_pipeline(self.pdf, output, 'Sample.pdf', '', '', '', '', True, True)
        self.assertTrue(result['layout_preview_path'].exists())
        self.assertEqual(pipeline.preview_path(result), result['layout_preview_path'])
        self.assertTrue(conversion.call_args.kwargs['options']['preserve_layout_structure'])
        self.assertEqual(style.call_count,2)
        self.assertIn('viewToggle', describe.call_args.args[0])
        self.assertTrue((output/'layout'/'layout-review.json').exists())
        self.assertIn('Legacy result', self.legacy.read_text())


if __name__ == '__main__':
    unittest.main()
