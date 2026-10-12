"""Local-only PDF table extraction for the Table Lab.

This module deliberately has no AWS or model dependency. It turns tables found
by pdfplumber into accessible HTML that can be inspected and customized locally.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Iterable

try:
    from .local_table_customization import LOCAL_TABLE_LAB_POLICY, LocalTableLabPolicy
except ImportError:  # Allows the small student-worker package to run standalone.
    from local_table_customization import LOCAL_TABLE_LAB_POLICY, LocalTableLabPolicy


def _clean_cell(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def _normalize_rows(rows: Iterable[Iterable[object]]) -> list[list[str]]:
    normalized = [[_clean_cell(cell) for cell in row] for row in rows if row]
    if not normalized:
        return []

    column_count = max(len(row) for row in normalized)
    return [row + [""] * (column_count - len(row)) for row in normalized]


def _render_table(rows: list[list[str]], caption: str, policy: LocalTableLabPolicy) -> str:
    if not rows:
        return ""

    parts = ["<table>", f"<caption>{escape(caption)}</caption>"]
    data_rows = rows

    if policy.first_row_is_header:
        header_row, *data_rows = rows
        parts.append("<thead><tr>")
        parts.extend(f'<th scope="col">{escape(cell)}</th>' for cell in header_row)
        parts.append("</tr></thead>")

    column_count = len(rows[0])
    parts.append("<tbody>")
    for row in data_rows:
        nonempty_cells = [cell for cell in row if cell]
        if len(nonempty_cells) == 1:
            # PDF tables often use a one-cell row as a section label. Preserve
            # that relationship for screen readers instead of rendering blanks.
            parts.append('<tr class="table-section">')
            parts.append(
                f'<th scope="rowgroup" colspan="{column_count}">{escape(nonempty_cells[0])}</th>'
            )
            parts.append("</tr>")
            continue

        parts.append("<tr>")
        parts.extend(f"<td>{escape(cell)}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def extract_pdf_tables_to_html(
    pdf_path: str | Path,
    output_path: str | Path,
    document_title: str,
    policy: LocalTableLabPolicy = LOCAL_TABLE_LAB_POLICY,
) -> dict:
    """Extract PDF tables locally and write one accessible HTML document.

    The first extracted row becomes the column-header row by default. Change
    ``LOCAL_TABLE_LAB_POLICY`` while evaluating alternate extraction strategies.
    """
    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError(
            "The local Table Lab requires pdfplumber. Run the Table Lab setup script "
            "again to install it."
        ) from error

    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sections = []
    navigation = []
    table_count = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            extracted_tables = page.extract_tables(policy.table_settings)
            if not extracted_tables:
                continue

            page_tables = []
            for table_index, raw_table in enumerate(extracted_tables, start=1):
                rows = _normalize_rows(raw_table)
                if not rows:
                    continue

                table_count += 1
                caption = f"Extracted table {table_count} on page {page_number}"
                page_tables.append(_render_table(rows, caption, policy))

            if not page_tables:
                continue

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
    output_path.write_text(html, encoding="utf-8")

    return {
        "html_path": str(output_path),
        "pages_with_tables": len(sections),
        "tables_extracted": table_count,
    }
