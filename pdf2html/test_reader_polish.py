import unittest
import tempfile
from pathlib import Path
from bs4 import BeautifulSoup
from reader_polish import polish_document
from reader_links import add_document_links
from reader_audit import audit_reader


class PolishTests(unittest.TestCase):
    def test_hyphen_repair_requires_attested_word(self):
        s = BeautifulSoup('<article><p>Multilingual multilingual. multi- lingual. made- up. <a href="https://example.org">multi- lingual</a></p></article>', 'html.parser')
        polish_document(s, s.article)
        self.assertIn('multilingual. made- up.', s.p.text)
        self.assertEqual(s.a.text, 'multi- lingual')

    def test_short_article_title_survives_repeated_render(self):
        from reader_view import render_reader
        title = 'An example article title'
        catalog = 'Conference proceedings. ' + title
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'article.html'
            path.write_text(''.join(f'<section class="page"><h2>{title}</h2><p>Body</p></section>' for _ in range(4)), encoding='utf-8')
            for _ in range(2):
                render_reader(path, catalog)
                soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
                self.assertEqual(soup.h1.text, title)
                self.assertIn(catalog, soup.select_one('.reader-publication-info').text)

    def test_grouped_table_and_idempotence(self):
        s = BeautifulSoup('<article><p>Table 1. Actual caption</p><table><caption>Wrong title</caption>'
            '<thead><tr><td></td><th colspan="2">Bare</th><th colspan="2">Augmented</th></tr></thead>'
            '<tbody><tr><td></td><th id="dup">A</th><th id="b">B</th><th id="dup">A</th><th id="b">B</th></tr>'
            '<tr><td>AP</td><td headers="dup">0.1</td><td>0.2</td><td>0.3</td><td>0.4</td></tr></tbody></table></article>', 'html.parser')
        polish_document(s, s.article)
        self.assertEqual(s.caption.text, 'Table 1. Actual caption')
        self.assertEqual(len(s.thead.select('tr')), 2)
        ids = [n['id'] for n in s.select('[id]')]
        self.assertEqual(len(ids), len(set(ids)))
        for cell in s.tbody.select('td'):
            self.assertEqual(len(cell['headers']), 3)
            self.assertTrue(all(s.find(id=x).name == 'th' for x in cell['headers']))
        before = str(s)
        polish_document(s, s.article)
        self.assertEqual(before, str(s))

    def test_annotation_urls_preserve_surrounding_markup(self):
        s = BeautifulSoup('<article><p><em>Keep</em> <a href="https://pro">https://pro</a>. '
            '<a href="example.org/path">example.org/path</a> <a href="%20file.pdf">%20file.pdf</a> End.</p>'
            '<p><a href="http://orcid.org/0000-0002-2359-6340">ID</a>'
            '<a href="a@example.org">Email</a></p></article>', 'html.parser')
        polish_document(s, s.article, ['https://pro.example.org/path%20file.pdf'])
        self.assertEqual(s.p.a['href'], 'https://pro.example.org/path%20file.pdf')
        self.assertEqual(len(s.p.select('a')), 1)
        self.assertEqual(s.em.text, 'Keep')
        self.assertTrue(s.p.text.endswith(' End.'))
        self.assertIn('ORCID 0000-0002-2359-6340', s.text)
        self.assertTrue(s.select_one('a[href="mailto:a@example.org"]'))

    def test_no_annotation_no_url_guess(self):
        s = BeautifulSoup('<article><p><a href="https://pro">https://pro</a>. example.org</p></article>', 'html.parser')
        polish_document(s, s.article)
        self.assertEqual(s.a['href'], 'https://pro')

    def test_reference_links_preserve_inline_markup(self):
        s = BeautifulSoup('<article><p>See [1].</p><h2>References</h2><p>1. Author, A. <a href="https://example.org">Source</a></p></article>', 'html.parser')
        add_document_links(s, s.article)
        self.assertEqual(s.select_one('.reader-citation')['href'], '#reader-ref-1')
        self.assertEqual(s.select_one('[data-reader-reference] a')['href'], 'https://example.org')

    def test_figure_text_remains_available(self):
        s = BeautifulSoup('<article><section class="page"><div><img alt="CHART"><p>Axes</p></div><p>Fig. 1. Caption</p><p>Body</p></section></article>', 'html.parser')
        polish_document(s, s.article)
        self.assertEqual(s.figcaption.text, 'Fig. 1. Caption')
        self.assertIn('Axes', s.details.text)
        self.assertEqual(s.figure.find_next_sibling().text, 'Body')

    def test_invalid_headers_are_reported(self):
        report = audit_reader('<table><th id="x">A</th><th id="x">B</th><td headers="x missing">1</td></table>')
        self.assertIn('invalid_table_headers', [f['code'] for f in report['findings']])


if __name__ == '__main__':
    unittest.main()
