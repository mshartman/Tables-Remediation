import unittest
from bs4 import BeautifulSoup
from reader_frontmatter import group_frontmatter
from pdf_reader_extract import line_html


def block(text, box, size=10):
    return {'bbox': box, 'lines': [{'bbox': box, 'spans': [
        {'text': text, 'size': size, 'origin': (box[0], box[3]), 'font': 'Example'}]}]}


class FrontmatterTests(unittest.TestCase):
    def test_author_fragments_join_without_loss(self):
        blocks = [block('Journal', (0, 0, 100, 15)),
                  block('Article title', (120, 30, 400, 60)),
                  block('Jane Doe', (120, 70, 190, 80)),
                  block(' and John Doe', (200, 70, 300, 80)),
                  block('Abstract prose. ' * 30, (120, 100, 400, 200))]
        result = group_frontmatter(blocks, 'Article title', 500, line_html)
        html = next(b['frontmatter_html'] for b in result if 'frontmatter_html' in b)
        soup = BeautifulSoup(html, 'html.parser')
        self.assertEqual(soup.select_one('.reader-authors').get_text(), 'Jane Doe and John Doe')
        self.assertIn('Journal', soup.get_text())
        self.assertIn(blocks[-1], result)
        self.assertEqual(group_frontmatter(blocks, 'Unknown title', 500, line_html), blocks)
