"""Offline checks of the shared table stage at the reader and conversion boundaries."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
import pymupdf

from content_accessibility_utility_on_aws.pdf2html.api import convert_pdf_to_html
from content_accessibility_utility_on_aws.pdf2html.services.page_builder import build_html_data
from content_accessibility_utility_on_aws.table_resolution.bda_layout import apply_to_layout
from content_accessibility_utility_on_aws.table_resolution.document import resolve_document
from layout_compare import geometry_structure
from pdf_reader_extract import extract_article
from reader_view import render_reader


def fixture():
    with pymupdf.open() as doc:
        page = doc.new_page()
        for x in (40, 160, 320):
            page.draw_line((x, 100), (x, 190))
        for y in (100, 130, 160, 190):
            page.draw_line((40, y), (320, y))
        page.insert_text((40, 86), 'Table 1. Measurements')
        page.insert_text((40, 225), 'Following paragraph.')
        for i, row in enumerate([('Gene', 'Value'), ('Gene-A', 'First'), ('Gene-B', 'Second')]):
            for x, text in zip((47, 167), row):
                page.insert_text((x, 120 + 30*i), text, fontname='helv' if i else 'hebo')
        return doc.tobytes()


class TableResolutionTests(unittest.TestCase):
    def assert_headers(self, soup):
        self.assertEqual(len(soup.select('table')), 1)
        cell = next(td for td in soup.select('td') if td.get_text(strip=True) == 'Second')
        headers = [soup.find(id=k) for k in cell['headers']]
        self.assertEqual({h.get_text(strip=True) for h in headers}, {'Gene-B', 'Value'})
        self.assertEqual({h.get('scope') for h in headers}, {'row','col'})

    def test_reader_and_geometry(self):
        data = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'reader.html'
            out.write_text(extract_article(data), encoding='utf-8')
            render_reader(out, 'Example')
            soup = BeautifulSoup(out.read_text(encoding='utf-8'), 'html.parser')
            self.assert_headers(soup)
            self.assertEqual(soup.get_text().count('Following paragraph.'), 1)
        with pymupdf.open(stream=data, filetype='pdf') as doc:
            elements = geometry_structure(doc)['elements']
        table = next(e for e in elements if e['type']=='TABLE')
        self.assert_headers(BeautifulSoup(table['representation']['html'], 'html.parser'))

    def test_saved_bda_layout(self):
        with pymupdf.open(stream=fixture(), filetype='pdf') as doc:
            data = geometry_structure(doc)
            original = next(e for e in data['elements'] if e['type'] == 'TABLE')
            original['type'] = 'TEXT'
            text = BeautifulSoup(original['representation']['html'], 'html.parser').get_text(' ')
            original['representation']['html'] = '<p>' + text + '</p>'
            apply_to_layout(doc, data)
        self.assertEqual(original['type'], 'TABLE')
        self.assert_headers(BeautifulSoup(original['representation']['html'], 'html.parser'))

    def test_cloud_conversion_formats(self):
        # Use the actual BDA HTML builder, substituting only the remote invocation.
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp)/'source.pdf'
            pdf.write_bytes(fixture())
            for single, filename in [(True, 'remediated.html'), (False, 'page-0.html')]:
                for linked in (False, True):
                    body = ('<p>Gene Value</p><p>Gene-A First</p><p>Gene-B Second</p>'
                            if not linked else '<p><a href="#notes">Gene-A First</a></p>')
                    body += '<p id="notes">Following paragraph.</p>'
                    def conversion(_pdf, directory, _options):
                        data = {'pages':[{'representation':{'html':body}}]}
                        return {**build_html_data(data, directory, single), 'result_data':data}
                    out = Path(tmp)/f'job-{single}-{linked}'
                    module = 'content_accessibility_utility_on_aws.pdf2html.api.'
                    with patch(module+'ExtendedBDAClient') as client, patch(module+'resolve_bda_project'):
                        client.return_value.process_and_retrieve.side_effect = conversion
                        convert_pdf_to_html(str(pdf),str(out),options={'single_file':single})
                    directory = out/'extracted_html'
                    html = (directory/filename).read_text(encoding='utf-8')
                    soup = BeautifulSoup(html,'html.parser')
                    self.assert_headers(soup)
                    self.assertEqual(soup.get_text().count('Following paragraph.'),1)
                    if linked:
                        self.assertTrue(soup.select_one('details table'))
                        self.assertTrue(soup.select_one('a[href="#notes"]'))
                    else:
                        self.assertFalse(soup.select('details'))
                        self.assertEqual(soup.get_text().count('Second'),1)
                    resolve_document(pdf,directory,single_file=single)
                    self.assertEqual((directory/filename).read_text(encoding='utf-8'),html)
