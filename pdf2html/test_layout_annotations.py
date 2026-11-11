from pathlib import Path
import tempfile
import unittest
from bs4 import BeautifulSoup
import layout_compare as lab
from layout_annotations import rotation_profile


class MarginAnnotationTests(unittest.TestCase):
    def fixture(self,x=20,note='A source note retained without changing the main article.'):
        doc=lab.pymupdf.open();page=doc.new_page(width=600,height=800)
        page.insert_textbox((60,80,540,500),'Ordinary body content should stay in its normal reading layout. '*12,fontsize=11)
        page.insert_text((x,750),note,fontsize=7,rotate=90)
        return doc

    def test_minor_edge_note_is_separated(self):
        with self.fixture() as doc:
            result=rotation_profile(doc[0])
            self.assertFalse(result['requires_fallback'])
            self.assertEqual(len(result['notes']),1)

    def test_central_rotated_content_still_falls_back(self):
        with self.fixture(x=300) as doc:
            self.assertTrue(rotation_profile(doc[0])['requires_fallback'])

    def test_substantial_rotated_text_still_falls_back(self):
        with self.fixture(note='Rotated content '*22) as doc:
            self.assertTrue(rotation_profile(doc[0])['requires_fallback'])

    def test_render_preserves_note_and_main_text_without_fallback_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf'
            with self.fixture() as doc:doc.save(pdf)
            output=lab.compare(pdf,root/'output',geometry_only=True)
            soup=BeautifulSoup(output.read_text(encoding='utf-8'),'html.parser')
            self.assertFalse(soup.select('.fallback-page'))
            self.assertIn('Ordinary body content',soup.select_one('.txt').get_text())
            self.assertIn('A source note retained',soup.select_one('.reader-margin-notes').get_text())
            self.assertEqual(soup.select_one('.source-margin-visual')['alt'],'')
            self.assertEqual(soup.select_one('.source-margin-visual')['aria-hidden'],'true')
            self.assertFalse(any('Source margin notes' in a.get_text() for a in soup.select('nav a')))


if __name__=='__main__':unittest.main()
