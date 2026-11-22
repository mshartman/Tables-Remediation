"""Verify the public sample really is untagged and has no header row."""
from pathlib import Path
import unittest

from bs4 import BeautifulSoup
from pypdf import PdfReader

FIXTURE = Path(__file__).parent / 'examples' / 'headerless-table'
ROWS = [['2022', '12'], ['2023', '15'], ['2024', '19']]


class HeaderlessFixtureTests(unittest.TestCase):
    def test_pdf_has_no_tags_or_bold_header_font(self):
        reader = PdfReader(FIXTURE / 'untagged-headerless.pdf')
        self.assertEqual(len(reader.pages), 1)
        self.assertNotIn('/StructTreeRoot', reader.trailer['/Root'])
        self.assertNotIn(b'/MCID', reader.pages[0].get_contents().get_data())
        fonts = reader.pages[0]['/Resources']['/Font'].get_object()
        self.assertEqual({font.get_object()['/BaseFont'] for font in fonts.values()}, {'/Helvetica'})
        self.assertEqual(reader.pages[0].extract_text().split(), [v for row in ROWS for v in row])

    def test_expected_html_preserves_every_row_as_data(self):
        soup = BeautifulSoup((FIXTURE / 'expected.html').read_text(encoding='utf-8'), 'html.parser')
        self.assertEqual(len(soup.select('table')), 1)
        self.assertEqual(soup.select('thead,th'), [])
        self.assertEqual([[td.get_text() for td in row.select('td')]
                          for row in soup.select('tbody tr')], ROWS)
        self.assertIsNone(soup.select_one('[data-reader-headers-repaired]'))


if __name__ == '__main__':
    unittest.main()
