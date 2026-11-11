from pathlib import Path
import tempfile
import unittest
from bs4 import BeautifulSoup
import layout_compare as lab
from layout_tables import ruled_tables


class LocalLayoutTableTests(unittest.TestCase):
    def fixture(self, sequential=True):
        doc=lab.pymupdf.open();page=doc.new_page(width=400,height=500)
        page.insert_text((40,80),'Table 1. Example observations',fontsize=10)
        for y in (90,120,245):page.draw_line((40,y),(360,y))
        for x,text in ((40,'ID'),(110,'Description'),(300,'Value')):
            page.insert_text((x,110),text,fontsize=10)
        for i,y in enumerate((145,180,225),1):
            page.insert_text((40,y),str(i if sequential else i*3),fontsize=10)
            page.insert_text((110,y),'Wrapped cell' if i==2 else 'Observation '+str(i),fontsize=10)
            if i==2:page.insert_text((110,y+12),'continued here',fontsize=10)
            page.insert_text((300,y),str(i*10),fontsize=10)
        return doc

    def test_rows_headers_and_wrapped_cells_are_preserved(self):
        with self.fixture() as doc:
            tables,report=ruled_tables(doc[0])
        self.assertEqual(len(tables),1)
        soup=BeautifulSoup(tables[0]['html'],'html.parser')
        self.assertEqual([n.get_text() for n in soup.select('thead th')],['ID','Description','Value'])
        self.assertEqual([n.get_text() for n in soup.select('tbody th[scope=row]')],['1','2','3'])
        self.assertEqual(soup.select('tbody tr')[1].select('td')[0].get_text(),'Wrapped cell continued here')
        self.assertEqual(report[0]['rows'],3)

    def test_ambiguous_rows_abstain(self):
        with self.fixture(False) as doc:
            tables,report=ruled_tables(doc[0])
        self.assertFalse(tables)
        self.assertEqual(report[0]['status'],'needs_review')

    def test_full_render_has_one_caption_and_table_structure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);pdf=root/'source.pdf'
            with self.fixture() as doc:doc.save(pdf)
            output=lab.compare(pdf,root/'result',geometry_only=True)
            soup=BeautifulSoup(output.read_text(encoding='utf-8'),'html.parser')
            table=soup.find('table')
            self.assertIsNotNone(table)
            self.assertEqual(len(table.select('thead')),1)
            self.assertEqual(len(table.select('tbody')),1)
            self.assertEqual(len(table.select('tbody tr')),3)
            self.assertIn('--table-y:',table['style'])
            self.assertEqual(len(soup.select('caption')),1)
            self.assertEqual(soup.select_one('.pb')['aria-label'],'Page 1')
            self.assertEqual(soup.select_one('nav ol a').get_text(),'Page 1')


if __name__=='__main__':unittest.main()
