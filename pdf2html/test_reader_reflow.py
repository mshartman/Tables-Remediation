"""Reflow safeguards; real layout is checked separately in a browser."""
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from reader_view import CSS, render_reader


class ReflowTests(unittest.TestCase):
    def test_reflow_rules_do_not_hide_overflow(self):
        self.assertIn('overflow-wrap:anywhere', CSS)
        self.assertIn('white-space:pre-wrap', CSS)
        self.assertIn('grid-template-columns:minmax(0,1fr)', CSS)
        self.assertNotIn('overflow-x:hidden', CSS)
        self.assertNotIn('overflow:hidden', CSS)

    def test_wrapping_preserves_text_and_scientific_markup(self):
        url = 'https://example.org/' + 'longidentifier' * 30
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'article.html'
            path.write_text('<p>' + url + '</p><pre>  a    b\n  c    d</pre>'
                            '<p>LiCoO<sub>2</sub> at 80 <sup>o</sup>C</p>'
                            '<table><tr><th>Value</th><td>1.234</td></tr></table>',
                            encoding='utf-8')
            render_reader(path, 'Reflow test')
            soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
            self.assertIn(url, soup.article.get_text())
            self.assertEqual(soup.pre.get_text(), '  a    b\n  c    d')
            self.assertEqual(soup.sub.get_text(), '2')
            self.assertEqual(soup.td.get_text(), '1.234')
            self.assertEqual(soup.table.parent.get('class'), ['reader-table'])


if __name__ == '__main__':
    unittest.main()
