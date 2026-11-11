import unittest

from bs4 import BeautifulSoup
from layout_presentation import decorate, navigation_headings, reading_label


def text(label, tag='p', y=180, identifier=None):
    id_attribute = f' id="{identifier}"' if identifier else ''
    return (f'<{tag}{id_attribute}><span class="vis" aria-hidden="true">'
            f'<span class="ln" style="left:50pt;top:{y}pt;font-size:12pt">{label}</span></span>'
            f'<span class="txt">{label}</span></{tag}>')


def page(content, number=1):
    return (f'<div class="page" style="width:600pt;height:800pt">'
            f'<span class="pb" id="page-{number}"></span><span class="pbl">Page {number}</span>'
            + content + '</div>')


def document(content):
    return BeautifulSoup('<html><head><title>Source</title></head><body>'
                         '<div class="toolbar"><button id="viewToggle">Reading view</button></div>'
                         '<main id="main-content">' + content + '</main></body></html>', 'html.parser')


PROSE = 'This substantive paragraph explains the research and its methods in ordinary flowing text. ' * 3


class ArticleNavigationTests(unittest.TestCase):
    def test_cover_and_article_front_matter_are_not_sections(self):
        soup = document(page(text('Research topic and its implications', 'h2', 100)
                             + text('A. Researcher and B. Collaborator', 'h2', 140)
                             + text('To cite this article: A bibliographic description.'))
                        + page(text('Research topic and its implications', 'h2', 100)
                               + text('ABSTRACT') + text(PROSE)
                               + text('Introduction', 'h2', 400) + text(PROSE, y=430)
                               + text('Methods', 'h2', 500) + text(PROSE, y=530), 2))
        before = [n.get_text() for n in soup.select('.txt')]
        review = {}
        entries = navigation_headings(soup, review=review)
        self.assertEqual([label for _, label in entries], ['Introduction', 'Methods'])
        self.assertEqual([n.get_text() for n in soup.select('.txt')], before)
        self.assertEqual(review['body_start']['page'], 2)
        self.assertEqual(len(soup.select('p[data-layout-nav-exclude="front_matter_before_body"]')), 3)

    def test_numbered_introduction_and_inline_abstract_are_body_anchors(self):
        for anchor in (text('1. Introduction', 'h2') + text(PROSE),
                       text('Abstract: ' + PROSE) + text('Results', 'h2'),
                       text('Abstract: ' + PROSE + '<a href="https://example.org">a source</a>') + text('Results', 'h2')):
            with self.subTest(anchor=anchor[:60]):
                soup = document(page(text('Authors and affiliations', 'h2', 100) + anchor))
                entries = navigation_headings(soup)
                self.assertNotIn('Authors and affiliations', [label for _, label in entries])

    def test_spaced_abstract_and_side_summary_are_recognized(self):
        for label in ('A B S T R A C T', 'Summary'):
            soup = document(page(text('Publisher details', 'h2', 70) + text(label)
                                 + text(PROSE) + text('Discussion', 'h2', 500)))
            self.assertEqual([label for _, label in navigation_headings(soup)], ['Discussion'])

    def test_substantive_earlier_body_is_not_removed_for_late_introduction(self):
        for content in (page(text('Earlier findings', 'h2') + text(PROSE * 4))
                        + page(text('Introduction', 'h2') + text(PROSE), 2),
                        page(text('Earlier findings', 'h2') + text(PROSE * 4)
                             + text('Introduction', 'h2', 500) + text(PROSE, y=540))):
            soup = document(content)
            self.assertIn('Earlier findings', [label for _, label in navigation_headings(soup)])

    def test_no_anchor_preserves_uncertain_sections_except_exact_document_title(self):
        title = 'An essay about scientific methods'
        soup = document(page(text(title, 'h2') + text(PROSE)
                             + text('Building a discipline', 'h2', 500) + text(PROSE)))
        self.assertEqual([label for _, label in navigation_headings(soup, title)], ['Building a discipline'])
        self.assertIn(title, soup.get_text())

    def test_document_title_comparison_normalizes_ligatures(self):
        soup = document(page(text('Finding evidence in the field', 'h2') + text(PROSE)))
        self.assertEqual(navigation_headings(soup, 'Finding evidence in the \ufb01eld'), [])

    def test_linked_contents_label_is_not_a_body_start(self):
        soup = document(page(text('Preface', 'h2') + text('<a href="#intro">Introduction</a>', 'h2') + text(PROSE)))
        self.assertIn('Preface', [label for _, label in navigation_headings(soup)])

    def test_repeated_margin_text_is_evidence_across_different_tag_types(self):
        label = 'A long running article title and its publication details 203'
        soup = document(page(text(label, 'h2', 70) + text('Real body section', 'h2', 250))
                        + page(text(label.replace('203', '204'), 'p', 70), 2))
        self.assertEqual([label for _, label in navigation_headings(soup)], ['Real body section'])
        self.assertIsNotNone(soup.select_one('p[data-layout-running-header]'))

    def test_body_heading_is_not_suppressed_by_similar_margin_text(self):
        label = 'A substantive discussion of the study findings'
        soup = document(page(text(label, 'p', 30)) + page(text(label, 'p', 30), 2)
                        + page(text(label, 'h2', 400), 3))
        self.assertEqual([label for _, label in navigation_headings(soup)], [label])

    def test_repeated_real_headings_at_page_tops_are_not_enough_to_prove_running_headers(self):
        label = 'Statistical analysis of study outcomes'
        soup = document(page(text(label, 'h3', 60) + text(PROSE, y=90))
                        + page(text(label, 'h3', 60) + text(PROSE, y=90), 2))
        self.assertEqual([label for _, label in navigation_headings(soup)], [label, label])

    def test_fuzzy_margin_matching_is_long_and_spatially_constrained(self):
        first = 'A STUDY OF IMPORTANT OBSERVATIONS IN MANY DIFFERENT RESEARCH SETTINGS / Smith et al. 203'
        second = '204 A STUDY OF IMPORTANT OBSERVATIONS IN MANY DIFFERENT RESEARCH SETTINGS / Sm1th et al.'
        soup = document(page(text(first, 'h2', 60)) + page(text(second, 'p', 60), 2))
        self.assertEqual(navigation_headings(soup), [])

    def test_fallback_and_object_headings_stay_readable_but_not_article_links(self):
        soup = document(page('<section class="fallback-text"><h2 id="fallback">Extracted text for PDF page 1</h2><p>OCR text</p></section>'
                             + '<table><tr><td><h3>Table topic</h3></td></tr></table>'
                             + text('Real section', 'h2', 400)))
        self.assertEqual([label for _, label in navigation_headings(soup)], ['Real section'])
        self.assertEqual(soup.find(id='fallback').name, 'h2')

    def test_nested_native_lists_and_unique_context_for_repeated_subsections(self):
        soup = document(page(text('First main section', 'h2', 150, 'original-main')
                             + text('Outcomes', 'h3', 250) + text('Second main section', 'h2', 400)
                             + text('Outcomes', 'h3', 500)))
        result = decorate(soup, soup.select('main > .page'), True, 'A document title')
        details = next(n for n in soup.select('nav details') if n.summary.get_text() == 'Article sections')
        self.assertEqual(len(details.ul.find_all('li', recursive=False)), 2)
        self.assertEqual(len(details.select('li > ul > li')), 2)
        links = details.select('a')
        self.assertEqual(links[0]['href'], '#original-main')
        self.assertEqual([a['aria-label'] for a in links if a.get('aria-label')],
                         ['Outcomes (First main section)', 'Outcomes (Second main section)'])
        self.assertEqual(result['section_count'], 4)
        for link in links:
            self.assertEqual(soup.find(id=link['href'][1:])['tabindex'], '-1')

    def test_existing_ids_are_preserved_and_generated_ids_cannot_collide(self):
        soup = document(page('<span id="layout-section-1"></span>' + text('Methods', 'h2', 200)
                             + text('Results', 'h2', 300, 'source-results')))
        decorate(soup, soup.select('main > .page'), True, 'Document')
        ids = [n['id'] for n in soup.select('[id]')]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIsNotNone(soup.select_one('nav a[href="#source-results"]'))

    def test_repeated_parent_names_do_not_produce_identical_subsection_link_names(self):
        soup = document(page(text('A recurring parent section', 'h2', 150)
                             + text('Outcomes', 'h3', 250) + text('A recurring parent section', 'h2', 400)
                             + text('Outcomes', 'h3', 500)))
        decorate(soup, soup.select('main > .page'), True, 'Document')
        names = [n['aria-label'] for n in soup.select('nav a[aria-label]')]
        self.assertEqual(len(names), len(set(names)))

    def test_empty_section_list_explains_page_navigation(self):
        soup = document(page('<section class="fallback-text"><h2>Extracted text for PDF page 1</h2></section>'))
        decorate(soup, soup.select('main > .page'), True, 'Document')
        self.assertEqual([n.summary.get_text() for n in soup.select('nav details')], ['Jump to page'])
        self.assertIn('Use Jump to page', soup.select_one('.layout-nav-note').get_text())

    def test_inline_scientific_labels_are_not_artificially_split(self):
        soup = BeautifulSoup('<h2><span class="txt">VO<sub>x</sub> and H<sub>2</sub>O</span></h2>', 'html.parser')
        self.assertEqual(reading_label(soup.h2), 'VOx and H2O')

    def test_view_switch_is_above_title_with_a_stable_name_and_description(self):
        soup = document(page(text('Methods', 'h2') + text(PROSE)))
        decorate(soup, soup.select('main > .page'), True, 'Document title')
        header = soup.select_one('.layout-intro')
        children = list(header.children)
        self.assertLess(children.index(header.select_one('.layout-controls')), children.index(header.h1))
        switch = header.find(id='viewToggle')
        self.assertEqual(switch['role'], 'switch')
        self.assertEqual(switch['type'], 'button')
        self.assertEqual(switch['aria-checked'], 'false')
        self.assertNotIn('aria-pressed', switch.attrs)
        self.assertEqual(switch['aria-controls'], 'main-content')
        self.assertEqual(switch.select_one('[data-view-state]')['aria-hidden'], 'true')
        self.assertEqual(reading_label(switch), 'Reading view')
        self.assertIsNotNone(soup.find(id=switch['aria-describedby']))
        self.assertNotIn('flowing text', header.get_text().lower())
        self.assertNotIn('flowing text', switch.get('title', '').lower())


if __name__ == '__main__':
    unittest.main()
