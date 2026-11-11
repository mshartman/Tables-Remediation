import unittest
from bs4 import BeautifulSoup
from pdf_reader_extract import pymupdf, is_duplicate_scan, extract_article


class ScanTests(unittest.TestCase):
    def test_detection_requires_coverage_and_text(self):
        rect = pymupdf.Rect(0, 0, 400, 600)
        image = {'image': b'x', 'bbox': (0, 0, 400, 600)}
        text = {'lines': [{'bbox': (20, 20, 380, 100), 'spans': [{'text': 'Words ' * 50}]}]}
        self.assertTrue(is_duplicate_scan(image, [image, text], rect))
        self.assertFalse(is_duplicate_scan(image, [image], rect))
        self.assertFalse(is_duplicate_scan(dict(image, bbox=(0, 0, 200, 200)), [text], rect))

    def test_scan_is_retained_in_closed_disclosure_after_text(self):
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=600)
        pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 400, 600))
        pix.clear_with(255)
        page.insert_image(page.rect, stream=pix.tobytes('png'))
        page.insert_textbox(pymupdf.Rect(20, 20, 380, 500), 'Readable source text. ' * 30)
        soup = BeautifulSoup(extract_article(doc.tobytes()), 'html.parser')
        doc.close()
        details = soup.select_one('details.reader-source-scan')
        self.assertIsNotNone(details)
        self.assertFalse(details.has_attr('open'))
        self.assertIsNotNone(details.img)
        self.assertIn('Readable source text', soup.select_one('.page > p').get_text())


if __name__ == '__main__':
    unittest.main()
