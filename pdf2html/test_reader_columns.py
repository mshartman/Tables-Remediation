"""Synthetic newspaper tests; no source PDF or network required."""
import unittest
from collections import Counter

from reader_columns import detect_gutters, recover_scan_columns, scan_order, scan_headings


def line(label, x, y, width=130, size=9):
    box = (x, y, x + width, y + size)
    return {'bbox': box, 'spans': [{'text': label, 'size': size, 'font': 'OCR-Regular',
                                   'origin': (x, y + size), 'bbox': box}]}


def block(lines):
    return {'bbox': (min(l['bbox'][0] for l in lines), min(l['bbox'][1] for l in lines),
                     max(l['bbox'][2] for l in lines), max(l['bbox'][3] for l in lines)), 'lines': lines}


class NewspaperColumnsTests(unittest.TestCase):
    def columns(self, count=5):
        return [block([line(f'Column {col} contains substantial source text {row}',
                            30 + col * 150, 150 + row * 15) for row in range(9)])
                for col in range(count)]

    def test_detects_five_columns_not_two(self):
        self.assertEqual(detect_gutters(self.columns(), 800), [170, 320, 470, 620])

    def test_column_count_is_not_specific_to_one_newspaper(self):
        for count in (3, 4, 6):
            with self.subTest(columns=count):
                self.assertEqual(len(detect_gutters(self.columns(count), count * 150 + 50)), count - 1)

    def test_does_not_split_sparse_or_two_column_pages(self):
        blocks = self.columns(2)
        result, report = recover_scan_columns(blocks, 800)
        self.assertIs(result, blocks)
        self.assertEqual(report['method'], 'original_blocks')
        self.assertEqual(detect_gutters([block([line('Short', 20, 20)])], 800), [])

    def test_split_mixed_blocks_without_losing_or_rewriting_text(self):
        columns = self.columns()
        mixed = block([l for b in columns for l in b['lines']])
        result, report = recover_scan_columns([mixed], 800)
        self.assertEqual(len(result), 5)
        self.assertEqual(report['split_blocks'], 4)
        before = Counter(s['text'] for l in mixed['lines'] for s in l['spans'])
        after = Counter(s['text'] for b in result for l in b['lines'] for s in l['spans'])
        self.assertEqual(before, after)
        self.assertEqual(len(mixed['lines']), 45)

    def test_column_order_with_local_spanning_headlines(self):
        columns = self.columns()
        title = block([line('A headline spanning the first two columns', 30, 100, width=280, size=22)])
        header = block([line('Publication details across the page', 30, 30, width=730)])
        result = scan_order([columns[4], title, columns[1], header, columns[3], columns[0], columns[2]],
                            [170, 320, 470, 620])
        self.assertEqual(result, [header, title] + columns)

    def test_fragments_on_same_baseline_are_joined_left_to_right(self):
        columns = self.columns()
        fragments = block([line('second', 100, 151, width=40), line('First', 30, 152, width=40)])
        result, _ = recover_scan_columns(columns + [fragments], 800)
        joined = result[-1]['lines']
        self.assertEqual(len(joined), 1)
        self.assertEqual(''.join(s['text'] for s in joined[0]['spans']), 'First second')
        self.assertEqual(fragments['lines'][0]['spans'][0]['text'], 'second')

    def test_headlines_use_size_and_prose_not_specific_font(self):
        title = block([line('A newspaper headline', 30, 120, size=24)])
        subtitle = block([line('And a second title line', 30, 146, size=24)])
        title['reader_column_span'] = subtitle['reader_column_span'] = (0, 1)
        prose = block([line('Ordinary body text with sufficient context. ' * 3, 30, 177)])
        byline = block([line('by Someone writing a column', 30, 250, size=24)])
        later = block([line('More ordinary body text with sufficient context. ' * 3, 30, 280)])
        noise = block([line('oe', 30, 320, size=24)])
        noise['heading_level'] = 2
        blocks = [title, subtitle, prose, byline, later, noise]
        scan_headings(blocks, 9, 1000)
        self.assertEqual(title['heading_level'], 2)
        self.assertEqual(len(title['lines']), 2)
        self.assertNotIn(subtitle, blocks)
        self.assertIsNone(byline['heading_level'])
        self.assertIsNone(noise['heading_level'])

    def test_real_pdf_preserves_source_evidence_and_column_sequence(self):
        from bs4 import BeautifulSoup
        from pdf_reader_extract import pymupdf, extract_article
        with pymupdf.open() as doc:
            page = doc.new_page(width=800, height=1000)
            pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 800, 1000))
            pix.clear_with(255)
            page.insert_image(page.rect, stream=pix.tobytes('png'))
            for column in range(5):
                for row in range(9):
                    page.insert_text((30 + column * 150, 150 + row * 12),
                                     f'Column {column} row {row} contains source text.', fontsize=7)
            original = page.get_text('dict')['blocks']
            report = {}
            soup = BeautifulSoup(extract_article(doc.tobytes(), structure_report=report), 'html.parser')
        words = soup.select_one('.page').get_text(' ', strip=True)
        self.assertLess(words.index('Column 0 row 8'), words.index('Column 1 row 0'))
        self.assertLess(words.index('Column 3 row 8'), words.index('Column 4 row 0'))
        self.assertEqual(len(report['pages'][0]['source_blocks']), len(original))
        before = [s['text'] for b in original for l in b.get('lines', []) for s in l['spans']]
        recorded = [s['text'] for b in report['pages'][0]['source_blocks']
                    for l in b.get('lines', []) for s in l['spans']]
        self.assertEqual(before, recorded)
        self.assertEqual(len(soup.select('.reader-source-scan img')), 1)


if __name__ == '__main__':
    unittest.main()
