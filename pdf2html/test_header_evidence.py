"""Regression tests for the header decision through the final public reader."""
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
import pymupdf
from reader_view import render_reader
from pdf_reader_extract import extract_article
from test_headerless_fixture import FIXTURE, ROWS
from content_accessibility_utility_on_aws.remediate.remediation_strategies.table_detection import preprocess_tables
from content_accessibility_utility_on_aws.remediate.remediation_strategies.table_remediation import remediate_table_missing_headers, remediate_table_missing_thead
from content_accessibility_utility_on_aws.remediate.remediation_strategies.table_remediation_direct import ensure_table_structure


def continuation_pdf(number=1, repeat=False, bold_data=False):
    with pymupdf.open() as doc:
        for i in range(2):
            page = doc.new_page()
            caption = 'Table 1. Measurements' if i == 0 else f'Table {number} (continued). Measurements'
            page.insert_text((40, 86), caption)
            records = [('Gene-A','First'),('Gene-B','Second')] if i == 0 else [('Gene-C','Third'),('Gene-D','Fourth')]
            if i == 0 or repeat:
                records.insert(0, ('Gene','Value'))
            for x in [40,160,320]:
                page.draw_line((x,100),(x,100+30*len(records)))
            for j in range(len(records)+1):
                page.draw_line((40,100+30*j),(320,100+30*j))
            for j, row in enumerate(records):
                for x, value in zip((47,167),row):
                    page.insert_text((x,120+30*j),value,fontname='hebo' if j == 0 and (not i or bold_data) else 'helv')
        return doc.tobytes()


def rendered(data):
    with tempfile.TemporaryDirectory() as folder:
        p=Path(folder)/'article.html'
        p.write_text(extract_article(data),encoding='utf-8')
        render_reader(p,'Local header regression')
        return BeautifulSoup(p.read_text(encoding='utf-8'),'html.parser')


class EvidenceTests(unittest.TestCase):
    def test_real_public_headerless_fixture(self):
        soup=rendered((FIXTURE/'untagged-headerless.pdf').read_bytes())
        table=soup.table
        self.assertFalse(table.select('thead,th'))
        self.assertFalse(table.has_attr('data-reader-headers-repaired'))
        self.assertEqual([[c.text for c in r.select('td')] for r in table.select('tr')], ROWS)

    def test_verified_continuations(self):
        for repeat,bold_data in [(False,False),(False,True),(True,False)]:
            with self.subTest(repeat=repeat,bold_data=bold_data):
                table=rendered(continuation_pdf(repeat=repeat,bold_data=bold_data)).select('table')[1]
                self.assertEqual([c.text for c in table.select('thead th')], ['Gene','Value'])
                rows=table.select('tbody tr')
                self.assertEqual([[c.text for c in r.find_all(['th','td'])] for r in rows],
                                 [['Gene-C','Third'],['Gene-D','Fourth']])
                cell=rows[0].td
                self.assertEqual([table.find(id=k).text for k in cell['headers']],['Value','Gene-C'])

    def test_different_table_number_does_not_inherit(self):
        table=rendered(continuation_pdf(number=2,bold_data=True)).select('table')[1]
        self.assertFalse(table.thead)
        self.assertEqual(table.tr.td.text,'Gene-C')

    def test_raster_backed_working_page_preserves_uncertain_ocr_as_text(self):
        # Simulate the accepted OCR output: a raster background and invisible glyphs.
        with pymupdf.open(FIXTURE/'untagged-headerless.pdf') as original, pymupdf.open() as doc:
            source = original[0]
            page = doc.new_page(width=source.rect.width, height=source.rect.height)
            page.insert_image(page.rect, stream=source.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes('png'))
            for block in source.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        page.insert_text(span['origin'], span['text'], fontsize=span['size'], render_mode=3)
            findings = []
            soup = BeautifulSoup(extract_article(doc.tobytes(), table_report=findings), 'html.parser')
        self.assertFalse(soup.select('thead,th'))
        self.assertTrue(any('image overlaps' in f.get('reason', '').lower() for f in findings))
        for year, value in ROWS:
            self.assertIn(year + ' ' + value, soup.get_text(' ', strip=True))

    def test_audit_does_not_guess_headers(self):
        for markup in ['<tr><td><b>2022</b></td><td><b>12</b></td></tr>',
                       '<tr><th scope="row">Q1</th><td>Example</td></tr>']:
            html='<table id="fixture">'+markup+'<tr><td>2023</td><td>15</td></tr></table>'
            soup=BeautifulSoup(ensure_table_structure(preprocess_tables(html)), 'html.parser')
            remediate_table_missing_headers(soup, {'selector':'#fixture'})
            remediate_table_missing_thead(soup, {'selector':'#fixture'})
            self.assertFalse(soup.select('thead,th[scope=col]'))
            self.assertEqual(len(soup.select('tbody tr')),2)
