"""Local-only PDF table extraction for the Table Lab.

This module deliberately has no AWS or model dependency. It turns tables found
by pdfplumber into accessible HTML that can be inspected and customized locally.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
import pdfplumber
from bs4 import BeautifulSoup
from content_accessibility_utility_on_aws.table_resolution.continuation import carry_continuation_headers

from content_accessibility_utility_on_aws.table_resolution.reader import resolve_page

try:
    from .local_table_customization import LOCAL_TABLE_LAB_POLICY, LocalTableLabPolicy
except ImportError:  # Allows the small student-worker package to run standalone.
    from local_table_customization import LOCAL_TABLE_LAB_POLICY, LocalTableLabPolicy


def extract_pdf_tables_to_html(
    pdf_path: str | Path,
    output_path: str | Path,
    document_title: str,
    policy: LocalTableLabPolicy = LOCAL_TABLE_LAB_POLICY,
) -> dict:
    """Extract PDF tables locally and write one accessible HTML document.

    Column header inference uses source evidence by default. Change
    ``LOCAL_TABLE_LAB_POLICY`` while evaluating alternate extraction strategies.
    """
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sections = []
    navigation = []
    table_count = 0
    review = []
    pages_with_tables = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                result = resolve_page(page, first_row_is_header=policy.first_row_is_header,
                                      row_headers=policy.first_column_is_row_header,
                                      line_settings=policy.table_settings)
            finally:
                page.close()
            if not result.tables and not result.review:
                continue
            table_count += len(result.tables)
            pages_with_tables += bool(result.tables)
            review.extend(result.review)
            page_tables = result.tables + [f'<p>{escape(note)}</p>' for note in result.review]
            section_id = f"page-{page_number}"
            navigation.append(
                f'<li><a href="#{section_id}">Page {page_number}</a></li>'
            )
            heading = f"<h2>Page {page_number}</h2>" if policy.include_page_headings else ""
            sections.append(
                f'<section class="page" id="{section_id}" aria-label="Page {page_number}">'
                f"{heading}{''.join(page_tables)}</section>"
            )

    if navigation:
        nav_html = '<nav aria-label="Pages"><h2>Pages</h2><ul>' + "".join(navigation) + "</ul></nav>"
        content_html = "".join(sections)
    else:
        nav_html = ""
        content_html = "<p>No extractable tables were found in this PDF.</p>"

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(document_title)}</title>
  </head>
  <body>
    <main>
      <h1>{escape(document_title)}</h1>
      <p>Local Table Lab output. Tables were extracted without AWS or generative AI.</p>
      {nav_html}
      {content_html}
    </main>
  </body>
</html>
"""
    soup = BeautifulSoup(html, "html.parser")
    carry_continuation_headers(soup)
    output_path.write_text(str(soup), encoding="utf-8")

    return {
        "html_path": str(output_path),
        "pages_with_tables": pages_with_tables,
        "review": review,
        "tables_extracted": table_count,
    }
