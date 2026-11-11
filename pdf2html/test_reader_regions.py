import unittest
from bs4 import BeautifulSoup
from reader_regions import group_regions, section_navigation, normalize_section_headings


class RegionTests(unittest.TestCase):
    def test_running_headers_and_reversed_section_sequence(self):
        soup = BeautifulSoup('<article>' + ''.join(
            f'<section class="page"><h2>Journal title</h2><p>Body</p><h2>{label} {n}</h2></section>'
            for n, label in enumerate(['Introduction', 'Methods', 'Results'], 1)) + '</article>', 'html.parser')
        normalize_section_headings(soup.article)
        self.assertEqual([h.text for h in soup.select('h2')], ['1 Introduction', '2 Methods', '3 Results'])
        self.assertEqual(len(soup.select('[data-reader-running-header]')), 3)
        before = str(soup)
        normalize_section_headings(soup.article)
        self.assertEqual(str(soup), before)

    def test_isolated_trailing_number_is_preserved(self):
        soup = BeautifulSoup('<article><h2>Experiment 2</h2></article>', 'html.parser')
        normalize_section_headings(soup.article)
        self.assertEqual(soup.h2.text, 'Experiment 2')

    def test_nested_regions_preserve_order_and_figures(self):
        soup = BeautifulSoup('<article><section class="page"><p>Intro</p><h2>Methods</h2><p>Body</p><h3>Detail</h3><figure><img alt="Chart"><figcaption>Caption</figcaption></figure><h2>Results</h2><p>End</p></section></article>', 'html.parser')
        before = soup.article.get_text(' ', strip=True)
        headings = group_regions(soup, soup.article)
        self.assertEqual(soup.article.get_text(' ', strip=True), before)
        self.assertEqual(len(soup.select('[data-reader-region]')), 3)
        self.assertEqual(soup.figure.find_parent('section')['aria-labelledby'], headings[1]['id'])
        nav = section_navigation(soup, headings)
        self.assertEqual(len(nav.select('a')), 2)
        first = str(soup)
        group_regions(soup, soup.article)
        self.assertEqual(str(soup), first)

    def test_existing_ids_and_page_boundaries(self):
        soup = BeautifulSoup('<article><section class="page"><h2 id="custom">Title</h2></section><section class="page"><p>Continuation</p></section></article>', 'html.parser')
        group_regions(soup, soup.article)
        self.assertEqual(soup.h2['id'], 'custom')
        self.assertEqual(soup.p.parent.get('class'), ['page'])


if __name__ == '__main__':
    unittest.main()
