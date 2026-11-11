"""Offline fixtures for the optional layout comparison; no service/model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import layout_compare as lab
from bs4 import BeautifulSoup


class LayoutComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'source.pdf'

    def tearDown(self):
        self.tmp.cleanup()

    def make_pdf(self, rotated=False, blank=False):
        with lab.pymupdf.open() as doc:
            page = doc.new_page()
            if not blank:
                page.insert_text((70,100), 'A sample article heading', fontsize=18)
                page.insert_text((70,140), 'Readable content with reference 42.', fontsize=11)
                if rotated:
                    page.insert_text((250,250), 'Rotated information', rotate=90)
            doc.save(self.pdf)

    def generate(self, **kwargs):
        with patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
            path = lab.compare(self.pdf, self.root / 'out', **kwargs)
        return BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser'), json.loads(
            (path.parent/'layout-review.json').read_text(encoding='utf-8'))

    def test_local_demo_preserves_text_navigation_and_source(self):
        self.make_pdf()
        before = self.pdf.read_bytes()
        soup, report = self.generate(geometry_only=True, title='A sample article')
        self.assertEqual(before, self.pdf.read_bytes())
        self.assertEqual(len(soup.select('h1')), 1)
        self.assertIn('Readable content', soup.get_text())
        semantic = soup.select_one('.txt')
        self.assertIn('--text-y:', semantic['style'])
        self.assertIn('--text-width:', semantic['style'])
        self.assertNotIn('width:1px;height:1px', semantic['style'])
        self.assertEqual(soup.select_one('.pb')['id'], 'layout-page-1')
        self.assertEqual(soup.select_one('.pb')['tabindex'], '-1')
        self.assertIn('local_geometry', report['structure_source'])
        self.assertEqual(report['network_calls'], 0)
        self.assertIn("default-src 'none'", soup.select_one('meta[http-equiv]')['content'])
        ids = [n['id'] for n in soup.select('[id]')]
        self.assertEqual(len(ids), len(set(ids)))
        for a in soup.select('a[href^="#"]'):
            self.assertIsNotNone(soup.find(id=a['href'][1:]))

    def test_scan_without_ocr_has_explicit_fallback(self):
        self.make_pdf(blank=True)
        soup, report = self.generate(geometry_only=True)
        self.assertEqual(report['fallback_pages'][0]['reason'], 'no_pdf_text')
        self.assertIn('OCR or a transcribed alternative is required', soup.get_text())
        self.assertIsNotNone(soup.select_one('.fallback-image img'))

    def test_rotated_text_is_retained_and_flagged(self):
        self.make_pdf(rotated=True)
        soup, report = self.generate(geometry_only=True)
        self.assertEqual(report['fallback_pages'][0]['reason'], 'rotated_text')
        self.assertIn('Rotated information', soup.select_one('.fallback-text').get_text())

    def test_saved_structure_path_does_not_call_bda(self):
        self.make_pdf()
        with lab.pymupdf.open(self.pdf) as doc:
            data = lab.geometry_structure(doc)
        source = self.root/'result.json'
        source.write_text(json.dumps(data), encoding='utf-8')
        before = source.read_bytes()
        soup, report = self.generate(bda_json=source)
        self.assertEqual(report['structure_source'], 'saved_bda')
        self.assertEqual(source.read_bytes(), before)
        self.assertNotIn('local geometry demo', soup.get_text().lower())

    def test_scanned_page_uses_saved_ocr_text(self):
        self.make_pdf(blank=True)
        data = {'elements':[{'id':'ocr', 'type':'TEXT', 'reading_order':0,
            'representation':{'html':'<p>Recognized OCR text</p>'},
            'locations':[{'page_index':0,'bounding_box':{'left':.1,'top':.1,'width':.8,'height':.8}}]}]}
        source = self.root/'result.json'
        source.write_text(json.dumps(data), encoding='utf-8')
        soup, report = self.generate(bda_json=source)
        self.assertIn('Recognized OCR text', soup.select_one('.fallback-text').get_text())

    def test_invalid_structure_is_rejected(self):
        for data in ({}, {'elements':[]}, {'elements':[{'id':'bad','locations':[{'page_index':9}]}]}):
            with self.assertRaises(ValueError):
                lab.validate_structure(data, 1)

    def test_prose_table_stays_a_table(self):
        e = {'type':'TABLE', 'rect':(10,10,200,200), 'html':'<table><tr><td>'+'long text '*30+'</td></tr></table>', 'sub':''}
        self.assertEqual(lab.iris_layout.classify([e], 600, 800, 0)[0]['role'], 'table')

    def test_mode_must_be_explicit(self):
        self.make_pdf()
        with self.assertRaises(ValueError):
            lab.compare(self.pdf, self.root/'out')


if __name__ == '__main__':
    unittest.main()
