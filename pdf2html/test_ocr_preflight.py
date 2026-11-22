"""Local OCR policy and pipeline tests; no paid service or model calls."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import layout_compare
import pymupdf
import ocr_preflight as ocr
from content_accessibility_utility_on_aws.utils.job_errors import PDFValidationError


PROSE = 'This article describes the research methods and presents the results for all participants. '
CONFIDENCE = {'mean_confidence': 96, 'low_confidence_fraction': .01, 'recognized_words': 50}


class OCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.pdf'
        self.report_path = self.root / 'output' / 'ocr-quality.json'
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'PDF_OCR_MODE': 'auto', 'PDF_OCR_LANGUAGE': 'eng',
                               'PDF_OCR_PAGE_TIMEOUT_SECONDS': '45', 'PDF_OCR_BUDGET_SECONDS': '600'}).start()
        patch('socket.create_connection', side_effect=AssertionError('No network')).start()
        self.engine = patch.object(ocr.shutil, 'which', return_value='tesseract').start()
        self.run = patch.object(ocr, 'run_page_ocr', side_effect=self.recognize).start()

    def make_pdf(self, scan=True, pages=1, old_text=PROSE * 3):
        with pymupdf.open() as doc:
            for _ in range(pages):
                page = doc.new_page(width=420, height=594)
                if scan:
                    with pymupdf.open() as visual:
                        p = visual.new_page(width=420, height=594)
                        p.insert_textbox((30, 50, 390, 550), PROSE * 5, fontsize=12)
                        png = p.get_pixmap().tobytes('png')
                    page.insert_image(page.rect, stream=png)
                page.insert_textbox((30, 50, 390, 550), old_text, fontsize=12, render_mode=3 if scan else 0)
            doc[0].insert_link({'kind': pymupdf.LINK_URI, 'from': pymupdf.Rect(30, 30, 100, 45),
                                'uri': 'https://example.org/article'})
            if pages > 1:
                doc[1].insert_link({'kind': pymupdf.LINK_GOTO, 'from': pymupdf.Rect(30, 30, 100, 45),
                                    'page': 0, 'to': pymupdf.Point(30, 50)})
            doc.set_toc([[1, 'Article', 1]])
            doc.save(self.source)

    def recognize(self, page, folder, executable, language, timeout):
        target = Path(folder) / 'recognized.pdf'
        with pymupdf.open() as doc:
            p = doc.new_page(width=page.rect.width, height=page.rect.height)
            p.insert_image(p.rect, stream=page.get_pixmap().tobytes('png'))
            p.insert_textbox((30, 50, 390, 550), PROSE * 5, fontsize=12, render_mode=3)
            doc.save(target)
        return target, CONFIDENCE

    def test_clean_native_text_is_not_rasterized(self):
        self.make_pdf(scan=False)
        with ocr.prepare_pdf(self.source, self.report_path) as (path, report):
            self.assertEqual(path, self.source)
            self.assertEqual(report['status'], 'not_needed')
            self.assertEqual(report['pages'][0]['reason'], 'native_text_passed_checks')
        self.run.assert_not_called()

    def test_scan_replaced_original_untouched_and_scratch_removed(self):
        self.make_pdf(pages=2)
        original = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with ocr.prepare_pdf(self.source, self.report_path) as (path, report):
            self.assertNotEqual(path, self.source)
            self.assertEqual(report['pages_replaced'], 2)
            with pymupdf.open(path) as doc:
                self.assertEqual(len(doc), 2)
                self.assertEqual(doc[0].rect, pymupdf.Rect(0, 0, 420, 594))
                self.assertIn('participants', doc[0].get_text())
                self.assertEqual(doc[0].get_links()[0]['uri'], 'https://example.org/article')
                self.assertEqual(doc[1].get_links()[0]['page'], 0)
                self.assertEqual(doc.get_toc(), [[1, 'Article', 1]])
            working = path
        self.assertFalse(working.exists())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original)
        private_report = self.report_path.read_text()
        self.assertNotIn(PROSE.strip(), private_report)
        self.assertNotIn(str(self.source), private_report)

    def test_no_text_scan_can_gain_text(self):
        self.make_pdf(old_text='')
        with ocr.prepare_pdf(self.source, self.report_path) as (_, report):
            self.assertEqual(report['pages_replaced'], 1)
            self.assertEqual(report['pages'][0]['reason'], 'scan_without_text')

    def test_missing_engine_and_timeout_are_visible_not_success(self):
        self.make_pdf()
        for outcome in ('engine_unavailable', 'page_timeout'):
            self.engine.return_value = None if outcome == 'engine_unavailable' else 'tesseract'
            self.run.side_effect = subprocess.TimeoutExpired('private payload', 45)
            with ocr.prepare_pdf(self.source, self.report_path) as (path, report):
                self.assertEqual(path, self.source)
                self.assertEqual(report['status'], 'partial')
                self.assertEqual(report['pages_needing_review'], 1)
                self.assertEqual(report['pages'][0]['outcome'], outcome)
            self.assertNotIn('private payload', self.report_path.read_text())

    def test_low_confidence_candidate_retains_source(self):
        self.make_pdf()
        def weak(*args):
            path, confidence = self.recognize(*args)
            return path, dict(confidence, mean_confidence=30)
        self.run.side_effect = weak
        with ocr.prepare_pdf(self.source, self.report_path) as (path, report):
            self.assertEqual(path, self.source)
            self.assertEqual(report['pages'][0]['outcome'], 'candidate_rejected')

    def test_short_confident_fragment_is_not_an_improvement(self):
        self.assertFalse(ocr.acceptable_candidate(ocr.text_quality(PROSE * 5),
                                                 ocr.text_quality('Some text'), CONFIDENCE))

    def test_tsv_quotes_are_literal_and_confidence_is_validated(self):
        tsv = self.root / 'words.tsv'
        tsv.write_text('level\tconf\ttext\n5\t98\t"Research\n5\t96\tresults\n', encoding='utf-8')
        confidence = ocr.confidence_summary(tsv)
        self.assertEqual(confidence['recognized_words'], 2)
        tsv.write_text('level\tconf\ttext\n5\tnan\tResearch\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            ocr.confidence_summary(tsv)

    def test_budget_does_not_start_another_page(self):
        self.make_pdf(pages=2)
        with patch.object(ocr.time, 'monotonic', side_effect=[0, 0, 601]):
            with ocr.prepare_pdf(self.source, self.report_path) as (_, report):
                self.assertEqual(report['pages_replaced'], 1)
                self.assertEqual(report['pages'][1]['outcome'], 'budget_exhausted')
        self.run.assert_called_once()

    def test_disable_and_100_page_limit_before_ocr(self):
        self.make_pdf()
        with patch.dict(os.environ, {'PDF_OCR_MODE': 'off'}):
            with ocr.prepare_pdf(self.source, self.report_path) as (path, report):
                self.assertEqual(path, self.source)
                self.assertEqual(report['status'], 'disabled')
        self.make_pdf(scan=False, pages=101)
        with self.assertRaises(PDFValidationError):
            with ocr.prepare_pdf(self.source, self.report_path):
                self.fail('Should not start')
        self.run.assert_not_called()

    def test_language_and_math_are_not_ascii_quality_failures(self):
        for text in ('中文研究方法和结果', 'Méthodes et résultats de la recherche', 'E = mc²; α = β + γ'):
            metrics = ocr.text_quality(text)
            self.assertFalse(metrics['broken_mapping'])
            self.assertFalse(metrics['fragmented'])
        self.assertTrue(ocr.text_quality('Bad \ufffd character')['broken_mapping'])
        self.assertTrue(ocr.text_quality('(cid:123)')['broken_mapping'])

    def test_summary_updates_old_warning_without_claiming_accuracy(self):
        target = self.root / 'reader.html'
        target.write_text('<html><body><main><aside data-reader-ocr-warning>has not re-run OCR</aside></main></body></html>')
        report = {'pages_selected': 2, 'pages_replaced': 1, 'pages_needing_review': 1}
        ocr.add_ocr_notice(target, report)
        ocr.add_ocr_notice(target, report)
        text = target.read_text()
        self.assertEqual(text.count('data-reader-ocr-summary='), 1)
        self.assertIn('1 selected page(s) could not', text)
        self.assertNotIn('has not re-run OCR', text)

    def test_pipeline_uses_working_pdf_and_preserves_report(self):
        import local_server as server
        self.make_pdf()
        html = self.root / 'reader.html'
        html.write_text('<html><body><main>Article text</main></body></html>')
        def process(path, *args):
            self.assertNotEqual(path, self.source)
            self.assertTrue(path.exists())
            return {'conversion_result': {'html_path': str(html)}}
        with patch.object(server, '_run_prepared_pipeline', side_effect=process):
            result = server._run_pipeline(self.source, self.report_path.parent, 'source.pdf', '', '', '', '', True, True)
        self.assertTrue(result['ocr_report_path'].exists())
        self.assertIn('new OCR text layer', html.read_text())


if __name__ == '__main__':
    unittest.main()
