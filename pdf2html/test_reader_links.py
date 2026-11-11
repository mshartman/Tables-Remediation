import unittest
from bs4 import BeautifulSoup
from reader_links import add_document_links


class LinkTests(unittest.TestCase):
    def render(self, source):
        soup = BeautifulSoup('<article>' + source + '</article>', 'html.parser')
        add_document_links(soup, soup.article)
        return soup

    def test_refs_and_citations(self):
        soup = self.render('<p>See [1] and [1–2], but [9] is unknown.</p><h2>References</h2><p>1. Smith, A. Work. 2. Jones, B. Other.</p>')
        self.assertEqual(len(soup.select('[data-reader-reference]')), 2)
        self.assertEqual(len(soup.select('.reader-citation')), 2)
        self.assertEqual(soup.select_one('#reader-ref-1')['tabindex'], '-1')
        self.assertIn('[9]', soup.get_text())
        before = str(soup)
        add_document_links(soup, soup.article)
        self.assertEqual(str(soup), before)

    def test_urls_and_existing_markup(self):
        soup = self.render('<p>See https://doi.org/10.1234/test. (https://example.org/a(b)). www.example.org</p><a href="https://example.com">https://example.com</a><p>H<sub>2</sub>O</p>')
        self.assertEqual([a['href'] for a in soup.select('a')], ['https://doi.org/10.1234/test', 'https://example.org/a(b)', 'https://www.example.org', 'https://example.com'])
        self.assertEqual(soup.sub.string, '2')
        self.assertFalse(soup.select('a a'))

    def test_no_guessing_or_id_collision(self):
        soup = self.render('<p id="reader-ref-1">[1]</p><h2>References</h2><p>1. Smith, A. Work.</p><p>4. Unknown, B. Work.</p>')
        self.assertEqual(soup.a['href'], '#reader-ref-1-entry')
        self.assertEqual(len(soup.select('[data-reader-reference]')), 1)
        self.assertFalse(self.render('<p>[1]</p><p>1. Smith, A. Not a bibliography.</p>').select('a'))


if __name__ == '__main__':
    unittest.main()
