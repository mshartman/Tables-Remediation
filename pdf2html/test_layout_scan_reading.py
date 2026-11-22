"""Production scan recovery using synthetic saved BDA; no cloud/model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
import layout_compare as lab
from layout_scan_reading import order_bda_articles, safe_fragment


def element(identifier, markup, box, sub='PARAGRAPH', order=0):
    return {'id': identifier, 'type': 'TEXT', 'sub_type': sub, 'reading_order': order,
            'representation': {'html': markup}, 'locations': [{'page_index': 0,
            'bounding_box': dict(zip(('left', 'top', 'width', 'height'), box))}]}


class ProductionScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'source.pdf'
        self.bda = self.root / 'saved.json'

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, scan=True):
        with lab.pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            if scan:
                pix = lab.pymupdf.Pixmap(lab.pymupdf.csRGB, (0, 0, 20, 20), 0)
                pix.clear_with(255)
                page.insert_image(page.rect, stream=pix.tobytes('png'))
            for column in range(3):
                for row in range(9):
                    page.insert_text((30 + 180 * column, 190 + 12 * row),
                                     f'Legacy OCR sample column {column} row {row} here.', fontsize=8, render_mode=3)
            doc.save(self.pdf)
        elements = []
        for column in range(3):
            x = (30 + column * 180) / 600
            elements.extend([
                element(f'h{column}', f'<h2>Correct heading {column}</h2>', (x, .18, .25, .035), 'SECTION_HEADER', 2*column),
                element(f'p{column}', f'<p>Correct transcription {column}. ' + 'Source wording remains available for review. ' * 12 + '</p>',
                        (x, .23, .25, .2), order=2*column+1)])
        self.bda.write_text(json.dumps({'elements': elements}), encoding='utf-8')

    def render(self, **kwargs):
        with patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
            path = lab.compare(self.pdf, self.root / 'out', geometry_only=True, title='Test issue', **kwargs)
        return (BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser'),
                json.loads(path.with_name('layout-review.json').read_text(encoding='utf-8')))

    def test_geometry_production_path_reuses_bda_not_legacy_ocr(self):
        self.fixture()
        soup, report = self.render(scan_bda_json=self.bda)
        self.assertEqual(report['scan_reading_recovery'][0]['wording_source'], 'saved_bda_transcription')
        labels = [node.get_text() for node in soup.select('[data-layout-scan-text] .txt')]
        self.assertIn('Correct heading 0', labels)
        self.assertFalse(any('Legacy OCR' in label for label in labels))
        self.assertEqual(len(soup.select('.reader-source-scan')), 1)
        self.assertTrue(soup.select('.layout-scan-source-visual[aria-hidden="true"]'))
        self.assertFalse(soup.select('.layout-scan-source-visual .txt'))
        self.assertEqual(len(soup.select('[id]')), len({n['id'] for n in soup.select('[id]')}))
        for link in soup.select('a[href^="#"]'):
            self.assertIsNotNone(soup.find(id=link['href'][1:]))
        self.assertEqual(soup.select_one('.page').find_all(recursive=False)[-1].get('class'), ['reader-source-scan'])

    def test_native_document_is_not_replaced_by_scan_transcription(self):
        self.fixture(scan=False)
        soup, report = self.render(scan_bda_json=self.bda)
        self.assertEqual(report['scan_reading_recovery'], [])
        self.assertIn('Legacy OCR', ' '.join(n.get_text() for n in soup.select('.txt')))
        self.assertFalse(soup.select('[data-layout-scan-text]'))

    def test_visible_annotation_on_scan_is_not_lost_to_saved_transcription(self):
        self.fixture()
        with lab.pymupdf.open(self.pdf) as doc:
            doc[0].insert_text((40, 420), 'Visible annotation must survive', fontsize=12)
            doc.saveIncr()
        soup, report = self.render(scan_bda_json=self.bda)
        self.assertEqual(report['scan_reading_recovery'][0]['reason'], 'visible_source_annotations_preserved')
        self.assertIn('Visible annotation must survive', ' '.join(n.get_text() for n in soup.select('.txt')))

    def test_invalid_optional_bda_retains_usable_geometry(self):
        self.fixture()
        self.bda.write_text('{invalid', encoding='utf-8')
        soup, report = self.render(scan_bda_json=self.bda)
        self.assertEqual(report['scan_bda_status'], 'invalid_or_unavailable_geometry_retained')
        self.assertIn('Legacy OCR', ' '.join(n.get_text() for n in soup.select('.txt')))

    def test_saved_bda_mode_also_uses_saved_transcription(self):
        self.fixture()
        with patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
            path = lab.compare(self.pdf, self.root / 'out', bda_json=self.bda)
        soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        self.assertIn('Correct transcription 0', ' '.join(n.get_text() for n in soup.select('.txt')))
        self.assertTrue(soup.select('.reader-source-scan'))

    def test_incomplete_saved_transcription_cannot_drop_page_content(self):
        self.fixture()
        data = {'elements': [element('short', '<p>Incomplete extraction</p>', (.1, .1, .3, .1))]}
        self.bda.write_text(json.dumps(data), encoding='utf-8')
        soup, report = self.render(scan_bda_json=self.bda)
        self.assertIn('Legacy OCR', ' '.join(n.get_text() for n in soup.select('.txt')))
        self.assertNotEqual(report['scan_reading_recovery'][0].get('wording_source'), 'saved_bda_transcription')

    def test_active_markup_and_unresolved_fragment_links_are_not_imported(self):
        fragment = safe_fragment('<p id="dup" onclick="evil()">Text <em>kept</em>'
                                 '<a href="javascript:evil()">bad</a><a href="#missing">fragment</a>'
                                 '<a href="https://example.org">good</a><script>evil()</script>'
                                 '<img src="https://tracker.example/x"><iframe src="/x"></iframe></p>')
        self.assertFalse(fragment.select('script, iframe, img, [id], [onclick]'))
        self.assertEqual([a['href'] for a in fragment.select('a[href]')], ['https://example.org'])
        self.assertEqual(fragment.em.get_text(), 'kept')

    def test_heading_regions_keep_parallel_stories_together_without_loss(self):
        def proxy(identifier, box, heading=False):
            return {'bbox': box, 'heading_level': 2 if heading else None,
                    'bda_element': {'id': identifier, 'type': 'TEXT', 'reading_order': 0}}
        spanning = proxy('spanning', (350, 100, 700, 130), True)
        introduction = proxy('intro', (350, 140, 700, 170))
        inset = proxy('inset', (350, 190, 490, 210), True)
        inset_body = proxy('inset-body', (350, 220, 490, 350))
        continuation = proxy('continuation', (530, 190, 700, 400))
        blocks = [spanning, introduction, inset, inset_body, continuation]
        result = order_bda_articles(blocks, [170, 340, 510])
        self.assertEqual([b['bda_element']['id'] for b in result],
                         ['spanning', 'intro', 'continuation', 'inset', 'inset-body'])
        self.assertEqual({id(b) for b in result}, {id(b) for b in blocks})

    def test_docker_includes_both_new_runtime_modules(self):
        docker = (Path(__file__).parent / 'Dockerfile.apprunner').read_text()
        self.assertIn('COPY reader_columns.py layout_scan_reading.py ./', docker)


if __name__ == '__main__':
    unittest.main()
