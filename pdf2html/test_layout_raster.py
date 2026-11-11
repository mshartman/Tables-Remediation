from pathlib import Path
import tempfile
import unittest
from bs4 import BeautifulSoup
import layout_compare as lab
from layout_raster import union_area,page_image_text_layer,mark_hidden_visual_spans


class RasterTextTests(unittest.TestCase):
    def fixture(self,kind):
        doc=lab.pymupdf.open();page=doc.new_page(width=600,height=800)
        pix=lab.pymupdf.Pixmap(lab.pymupdf.csRGB,(0,0,10,10),0);pix.clear_with(255)
        image=pix.tobytes('png')
        text=('This source text must remain readable when a page image overlaps its OCR layer. '*8)
        if kind=='painted_under':page.insert_textbox((40,80,560,350),text,fontsize=11)
        if kind=='tiles':
            for y in range(0,800,40):page.insert_image((0,y,600,y+40),stream=image)
        elif kind=='small_figure':page.insert_image((320,400,560,600),stream=image)
        else:page.insert_image(page.rect,stream=image)
        if kind!='painted_under':page.insert_textbox((40,80,560,350),text,fontsize=11,render_mode=3 if kind in ('tiles','invisible') else 0)
        return doc

    def test_union_does_not_double_count_overlaps(self):
        self.assertEqual(union_area([(0,0,10,10),(0,0,10,10)]),100)
        self.assertEqual(union_area([(0,0,10,5),(0,5,10,10)]),100)

    def test_single_image_tiled_and_occluded_text_are_recognized(self):
        for kind in ('invisible','tiles','painted_under'):
            with self.subTest(kind=kind),self.fixture(kind) as doc:
                result=page_image_text_layer(doc[0])
                self.assertIsNotNone(result)
                self.assertTrue(result['hide_visual_text'])

    def test_native_text_over_background_is_not_hidden(self):
        with self.fixture('native') as doc:
            self.assertFalse(page_image_text_layer(doc[0])['hide_visual_text'])

    def test_mixed_visible_annotation_is_not_hidden(self):
        with self.fixture('invisible') as doc:
            doc[0].insert_text((70,400),'Visible annotation must remain visible',fontsize=12)
            self.assertFalse(page_image_text_layer(doc[0])['hide_visual_text'])

    def test_hidden_visual_spans_keep_semantics_and_visible_annotations(self):
        with self.fixture('invisible') as doc:
            page = doc[0]
            page.insert_text((70, 400), 'Visible annotation must remain visible', fontsize=12)
            lines = lab.iris_layout.extract_lines(page)
            self.assertGreater(mark_hidden_visual_spans(page, lines), 0)
            ctx = lab.iris_layout.Ctx([], set())
            visual = BeautifulSoup(''.join(lab.iris_layout.render_line(ln, ctx) for ln in lines), 'html.parser').get_text()
            semantic = BeautifulSoup(lab.iris_layout.clean_text(lines, ctx), 'html.parser').get_text()
            self.assertNotIn('This source text', visual)
            self.assertIn('Visible annotation', visual)
            self.assertIn('This source text', semantic)
            self.assertIn('Visible annotation', semantic)

    def test_visible_overlapping_glyphs_are_not_removed(self):
        with self.fixture('invisible') as doc:
            page = doc[0]
            page.insert_text((70, 400), 'Overlapping', fontsize=12, render_mode=3)
            page.insert_text((70, 400), 'Overlapping', fontsize=12)
            lines = lab.iris_layout.extract_lines(page)
            mark_hidden_visual_spans(page, lines)
            overlaps = [s for ln in lines for s in ln['spans'] if 'Overlapping' in s['text']]
            self.assertTrue(overlaps)
            self.assertTrue(all(not s.get('visual_hidden') for s in overlaps))

    def test_occluded_source_text_is_only_hidden_visually(self):
        with self.fixture('painted_under') as doc:
            lines = lab.iris_layout.extract_lines(doc[0])
            self.assertGreater(mark_hidden_visual_spans(doc[0], lines), 0)
            self.assertTrue(all(s.get('visual_hidden') for ln in lines for s in ln['spans'] if s['text'].strip()))

    def test_mixed_scan_render_retains_only_annotation_in_visual_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.fixture('invisible') as doc:
                doc[0].insert_text((70, 400), 'Visible annotation must remain visible', fontsize=12)
                doc.save(root/'source.pdf')
            path = lab.compare(root/'source.pdf', root/'output', geometry_only=True)
            soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
            visual = ' '.join(n.get_text() for n in soup.select('.vis'))
            semantic = ' '.join(n.get_text() for n in soup.select('.txt'))
            self.assertNotIn('This source text', visual)
            self.assertIn('Visible annotation', visual)
            self.assertIn('This source text', semantic)

    def test_vertical_margin_note_does_not_duplicate_horizontal_ocr(self):
        with self.fixture('invisible') as doc:
            doc[0].insert_text((20,700),'Vertical source margin note',fontsize=7,rotate=90)
            self.assertTrue(page_image_text_layer(doc[0])['hide_visual_text'])

    def test_inset_scan_requires_hidden_ocr_evidence(self):
        for invisible in (True,False):
            with lab.pymupdf.open() as doc:
                page=doc.new_page(width=600,height=800)
                pix=lab.pymupdf.Pixmap(lab.pymupdf.csRGB,(0,0,10,10),0);pix.clear_with(255)
                page.insert_image((20,40,580,760),stream=pix.tobytes('png'))
                page.insert_textbox((60,80,540,500),'Source text remains readable across an inset scan. '*15,
                                    fontsize=10,render_mode=3 if invisible else 0)
                result=page_image_text_layer(page)
                if invisible:
                    self.assertEqual(result['recognition'],'inset_raster_with_hidden_text')
                else:self.assertIsNone(result)

    def test_legitimate_small_figure_is_not_suppressed(self):
        with self.fixture('small_figure') as doc:
            self.assertIsNone(page_image_text_layer(doc[0]))
            data=lab.geometry_structure(doc)
            self.assertEqual(sum(e['type']=='FIGURE' for e in data['elements']),1)

    def test_ocr_survives_render_and_page_image_is_disclosed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf'
            with self.fixture('tiles') as doc:doc.save(pdf)
            path=lab.compare(pdf,root/'output',geometry_only=True)
            soup=BeautifulSoup(path.read_text(encoding='utf-8'),'html.parser')
            self.assertTrue(soup.select('.txt'))
            self.assertIn('This source text must remain readable',soup.select_one('.txt').get_text())
            self.assertIsNotNone(soup.select_one('[data-layout-scan-warning]'))
            self.assertEqual(len(soup.select('.reader-source-scan')),1)
            self.assertFalse(soup.select('figure.fig'))


if __name__=='__main__':unittest.main()
