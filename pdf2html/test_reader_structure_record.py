import json
import unittest
from pdf_reader_extract import pymupdf, extract_article


class StructureRecordTests(unittest.TestCase):
    def test_evidence_does_not_change_html(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), 'Example source text')
        page.insert_link({'kind': pymupdf.LINK_URI, 'from': pymupdf.Rect(70, 60, 200, 75),
                          'uri': 'https://example.org'})
        data = doc.tobytes()
        doc.close()
        report = {}
        self.assertEqual(extract_article(data), extract_article(data, structure_report=report))
        json.dumps(report)
        page = report['pages'][0]
        self.assertEqual(page['source_blocks'][0]['lines'][0]['spans'][0]['text'], 'Example source text')
        self.assertEqual(page['links'][0]['uri'], 'https://example.org')
        self.assertEqual(page['links'][0]['overlapping_source_ids'], ['p1-source-1'])
        self.assertEqual(page['render_blocks'][0]['overlapping_source_ids'], ['p1-source-1'])

    def test_page_ids_are_unique_and_empty_pages_supported(self):
        doc = pymupdf.open()
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((72,72), 'Repeated text')
        doc.new_page()
        report = {}
        extract_article(doc.tobytes(), structure_report=report)
        doc.close()
        ids = [b['id'] for p in report['pages'] for b in p['source_blocks']]
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(report['pages'][2]['source_blocks'], [])


if __name__ == '__main__':
    unittest.main()
