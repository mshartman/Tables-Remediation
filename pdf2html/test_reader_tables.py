import unittest
from bs4 import BeautifulSoup
from pdf_reader_extract import pymupdf, extract_article


class TableTests(unittest.TestCase):
    def make_pdf(self, bold=True):
        doc = pymupdf.open()
        page = doc.new_page()
        for x in (50, 180, 310):
            page.draw_line((x, 100), (x, 190))
        for y in (100, 130, 160, 190):
            page.draw_line((50, y), (310, y))
        page.insert_text((50, 85), 'Table 1. Measurements')
        for y, values in [(120, ['Group', 'Value']), (150, ['Control', '-1.25']), (180, ['Treatment', '2.50'])]:
            for x, value in zip((60, 190), values):
                page.insert_text((x, y), value, fontname='hebo' if bold and y == 120 else 'helv')
        result = doc.tobytes()
        doc.close()
        return result

    def test_grid_and_caption_preserve_values(self):
        report = []
        soup = BeautifulSoup(extract_article(self.make_pdf(), table_report=report), 'html.parser')
        self.assertEqual(len(soup.select('table')), 1)
        self.assertEqual([th.get_text() for th in soup.select('th[scope=col]')], ['Group', 'Value'])
        self.assertEqual(soup.caption.get_text(), 'Table 1. Measurements')
        self.assertEqual(soup.get_text().count('-1.25'), 1)
        self.assertEqual(report[0]['status'], 'structured_needs_review')

    def test_uncertain_headers_keep_source_text(self):
        report = []
        soup = BeautifulSoup(extract_article(self.make_pdf(False), table_report=report), 'html.parser')
        self.assertIsNone(soup.table)
        self.assertIn('-1.25', soup.get_text())
        self.assertTrue(any(item['status'] == 'needs_review' for item in report))


if __name__ == '__main__':
    unittest.main()
