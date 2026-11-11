import tempfile
import unittest
from pathlib import Path
from bs4 import BeautifulSoup
from pdf_reader_extract import block_html, heading_level, ordered, contextual_headings
from reader_view import render_reader


def block(text, font='CharisSIL', bbox=(30, 60, 280, 80)):
    return {'bbox': bbox, 'lines': [{'spans': [{'text': text, 'font': font, 'size': 8,
                                              'origin': (bbox[0], bbox[1])}]}]}


class ReaderStructureTests(unittest.TestCase):
    def test_body_font_deep_headings_require_consecutive_siblings(self):
        from pdf_reader_extract import recover_heading_families
        parent = block('2.3. Storage', 'OtherStyle')
        parent['heading_level'] = 3
        first = block('2.3.1. Battery Model', bbox=(30, 100, 280, 110))
        second = block('2.3.2. Capacitor Model', bbox=(30, 200, 280, 210))
        prose = lambda y: block('Ordinary explanatory prose. ' * 12, bbox=(30,y,280,y+40))
        pages = [[parent, first, prose(115), second, prose(215)]]
        recover_heading_families(pages, 8)
        self.assertEqual(first['heading_level'], 4)
        self.assertEqual(second['heading_level'], 4)
        first.pop('heading_level'); second.pop('heading_level')
        second['lines'][0]['spans'][0]['text'] = '2.3.4. Capacitor Model'
        recover_heading_families(pages, 8)
        self.assertNotIn('heading_level', first)

    def test_repeated_unknown_font_subsections_need_parent(self):
        parent = block('2. Methods', 'Times-Bold', (30, 20, 280, 30))
        prose = lambda y: block('Long ordinary supporting prose. ' * 12, bbox=(30, y, 280, y+40))
        first = block('2.1. Sampling', 'OpaqueStyle', (30, 100, 280, 110))
        second = block('2.2. Analysis', 'OpaqueStyle', (30, 200, 280, 210))
        orphan = block('8.1. Unrelated', 'OpaqueStyle', (30, 300, 280, 310))
        contextual_headings([[parent, prose(35), first, prose(115), second, prose(215), orphan, prose(315)]])
        self.assertEqual(first['heading_level'], 3)
        self.assertEqual(second['heading_level'], 3)
        self.assertIsNone(orphan['heading_level'])

    def test_family_does_not_promote_prose_or_bibliography(self):
        parent = block('2. Methods', 'Times-Bold', (30, 20, 280, 30))
        prose = lambda y: block('Long ordinary supporting prose. ' * 12, bbox=(30, y, 280, y+40))
        first = block('2.1. Plain list item', bbox=(30, 100, 280, 110))
        second = block('2.2. Plain list item', bbox=(30, 200, 280, 210))
        contextual_headings([[parent, prose(35), first, prose(115), second, prose(215)]])
        self.assertIsNone(first['heading_level'])
        self.assertIsNone(second['heading_level'])

    def test_split_number_unknown_font_heading(self):
        heading = block('1.', 'OpaqueFont', (30, 20, 200, 40))
        heading['lines'].append(block('Introduction', 'OpaqueFont')['lines'][0])
        for line in heading['lines']:
            for span in line['spans']:
                span['size'] = 14
        prose = block('Body text with supporting details. ' * 12, bbox=(30, 50, 280, 140))
        contextual_headings([[heading, prose]])
        self.assertEqual(heading['heading_level'], 2)
        self.assertEqual(heading_level(block('2.1. Methods', 'Opaque.B'), 8), 3)

    def test_context_rejects_front_matter_and_captions(self):
        author = block('Jane Smith', 'Times-Bold', (30, 10, 150, 20))
        heading = block('1.0 Introduction', 'Times-Bold', (30, 50, 160, 60))
        prose = block('Ordinary body text with supporting details. ' * 8, bbox=(30, 70, 280, 140))
        caption = block('Figure 1: Experimental apparatus', 'Times-Bold', (30, 160, 250, 170))
        contextual_headings([[author, heading, prose, caption]])
        self.assertIsNone(author['heading_level'])
        self.assertEqual(heading['heading_level'], 2)
        self.assertIsNone(caption['heading_level'])

    def test_headings_require_style(self):
        self.assertEqual(heading_level(block('1. Introduction', 'AdvCMBX10.TT'), 10), 2)
        self.assertEqual(heading_level(block('2.1. Resources', 'AdvCMTI10.TT'), 10), 3)
        self.assertIsNone(heading_level(block('1. Ordinary prose', 'AdvCMR10.TT'), 10))
        self.assertEqual(heading_level(block('1. Introduction', 'CharisSIL-Bold'), 8), 2)
        self.assertEqual(heading_level(block('3.1. SB-242235', 'CharisSIL-Italic'), 8), 3)
        self.assertIsNone(heading_level(block('87.50 Li, 99.18 Co Tao et al.,[87]'), 6))
        self.assertIsNone(heading_level(block('No data was used for the research described in the article.'), 6))
        self.assertIsNone(heading_level(block('2 LiCoO2 + 3 CO = Li2O', 'STIX-Regular'), 8))
        self.assertEqual(heading_level(block('References', 'CharisSIL-Bold'), 8), 2)

    def test_geometry_not_number_sort(self):
        left = block('4. Source section', bbox=(30, 80, 280, 100))
        right = block('2. Source section', bbox=(310, 50, 560, 70))
        self.assertEqual(ordered([right, left], 595), [left, right])

    def test_reader_preserves_language_and_has_one_title(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent / 'local_runs') as folder:
            path = Path(folder) / 'article.html'
            path.write_text('<html lang="fr"><body><section class="page"><p>Example title</p><h2>1. Section</h2><h3>1.1. Subsection</h3><p lang="de">Guten Tag</p><p>LiCoO<sub>2</sub></p></section></body></html>', encoding='utf-8')
            render_reader(path, 'Example title')
            render_reader(path, 'Example title')
            soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
            self.assertEqual(soup.html['lang'], 'fr')
            self.assertEqual(len(soup.select('main')), 1)
            self.assertEqual(soup.get_text().count('Example title'), 2)  # title and h1
            self.assertEqual(soup.select_one('p[lang]')['lang'], 'de')
            self.assertEqual(soup.h3.get_text(), '1.1. Subsection')
            self.assertEqual(soup.sub.get_text(), '2')


if __name__ == '__main__':
    unittest.main()
