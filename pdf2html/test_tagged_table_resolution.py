"""A headerless survey must survive conversion from tagged PDF to article HTML."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pymupdf
from bs4 import BeautifulSoup

from pdf_reader_extract import extract_article
from reader_view import render_reader


CAPTION = "Table 1: Survey statements."
BEFORE = "Visitors answered the following questions."
AFTER = "Each response was recorded once."
STATEMENTS = (
    "I could open the exhibit.",
    "The prompts were clear.",
    "I followed the instructions.",
    "The interface felt familiar.",
    "I understood the controls.",
    "The display was legible.",
    "I could hear the audio.",
    "The interaction was convenient.",
)


def survey_pdf(invalid_reference=None):
    """Build actual marked content and a parent tree. Cells have no printed grid."""
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_font(fontname="helv")
        root = doc.get_new_xref()
        parent_tree = doc.get_new_xref()
        owners = []
        stream = []

        def element(role, parent):
            xref = doc.get_new_xref()
            doc.update_object(xref, f"<< /Type /StructElem /S /{role} /P {parent} 0 R /Pg {page.xref} 0 R >>")
            return xref

        def children(parent, items):
            refs = " ".join(f"{child} 0 R" for child in items)
            doc.xref_set_key(parent, "K", f"[{refs}]")

        def marked_text(owner, x, y, value, reference=None):
            mcid = len(owners)
            owners.append(owner)
            doc.xref_set_key(owner, "K", str(mcid if reference is None else reference))
            stream.append(f"/P << /MCID {mcid} >> BDC BT /helv 11 Tf {x} {y} Td ({value}) Tj ET EMC")
            return mcid

        article = element("Document", root)
        before = element("P", article)
        marked_text(before, 50, 720, BEFORE)
        caption = element("Caption", article)
        marked_text(caption, 50, 690, CAPTION)
        table = element("Table", article)
        rows = []
        for i, statement in enumerate(STATEMENTS):
            row = element("TR", table)
            rows.append(row)
            qcell, value_cell = element("TD", row), element("TD", row)
            children(row, [qcell, value_cell])
            qtext, value_text = element("P", qcell), element("P", value_cell)
            children(qcell, [qtext])
            children(value_cell, [value_text])
            y = 674 - i * 16
            qid = marked_text(qtext, 50, y, f"Q{i + 1}")
            reference = None
            if i == 0 and invalid_reference:
                reference = qid if invalid_reference == "duplicate" else 9999
            marked_text(value_text, 135, y, statement, reference)
        children(table, rows)
        after = element("P", article)
        marked_text(after, 50, 510, AFTER)
        children(article, [before, caption, table, after])
        owner_refs = " ".join(f"{ref} 0 R" for ref in owners)
        doc.update_object(parent_tree, f"<< /Nums [0 [{owner_refs}]] >>")
        doc.update_object(root, f"<< /Type /StructTreeRoot /K [{article} 0 R] /ParentTree {parent_tree} 0 R >>")
        doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
        doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<< /Marked true >>")
        doc.xref_set_key(page.xref, "StructParents", "0")
        contents = doc.get_new_xref()
        doc.update_object(contents, "<< >>")
        doc.update_stream(contents, "\n".join(stream).encode("ascii"))
        page.set_contents(contents)
        return doc.tobytes()


class TaggedSurveyTests(unittest.TestCase):
    def test_tagged_survey_keeps_every_label_and_statement_in_one_row(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "article.html"
            path.write_text(extract_article(survey_pdf()), encoding="utf-8")
            render_reader(path, "Survey results")
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(len(soup.select("table")), 1)
        table = soup.table
        self.assertEqual(table.caption.get_text(" ", strip=True), CAPTION)
        self.assertFalse(table.select("thead, th[scope=col]"))
        rows = table.select("tr")
        self.assertEqual(len(rows), len(STATEMENTS))
        for i, (row, statement) in enumerate(zip(rows, STATEMENTS), 1):
            label, cell = row.find_all(["th", "td"], recursive=False)
            self.assertEqual((label.name, label.get("scope"), label.get_text(strip=True)), ("th", "row", f"Q{i}"))
            self.assertEqual((cell.name, cell.get_text(" ", strip=True)), ("td", statement))
            self.assertIn(label["id"], cell["headers"])
            self.assertEqual(len(soup.find_all(id=label["id"])), 1)
        body = soup.get_text(" ", strip=True)
        for text in (BEFORE, CAPTION, AFTER, *STATEMENTS):
            self.assertEqual(body.count(text), 1, text)
        self.assertLess(body.index(BEFORE), body.index(CAPTION))
        self.assertLess(body.index(STATEMENTS[-1]), body.index(AFTER))

    def test_incomplete_and_shared_cell_references_preserve_the_source(self):
        for invalid in ("missing", "duplicate"):
            with self.subTest(invalid=invalid):
                soup = BeautifulSoup(extract_article(survey_pdf(invalid)), "html.parser")
                self.assertIsNone(soup.table)
                text = soup.get_text(" ", strip=True)
                for phrase in (AFTER, BEFORE, CAPTION, *STATEMENTS):
                    self.assertEqual(text.count(phrase), 1, phrase)


if __name__ == "__main__":
    unittest.main()
