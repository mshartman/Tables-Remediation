"""Build semantic tables from local PDF coordinates before document remediation."""
from dataclasses import dataclass, field
import re
from bs4 import BeautifulSoup

from .html_tables import table_html
from .table_structure import detect_page
from .tagged import tagged_tables


@dataclass
class PageTables:
    tables: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)


def resolve_page(page, *, first_row_is_header=None, row_headers=None, line_settings=None):
    result = PageTables()
    tagged = tagged_tables(page)
    for record in tagged:
        result.tables.append('<div class="table-scroll" style="overflow-x:auto" tabindex="0" '
                             f'role="region" aria-label="Table on PDF page {page.page_number}">'
                             + record['html'] + '</div>')
    for table in detect_page(page, line_settings=line_settings)['tables']:
        a = table['bbox']
        if any(a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
               for b in (r['bbox'] for r in tagged)):
            continue
        # Namespace generated HTML IDs independently of the detector's JSON IDs.
        table['id'] = 'local-table-' + table['id']
        try:
            html = table_html(table, page.chars, page.page_number, first_row_is_header, row_headers)
            left, top, right, _ = a
            if top > page.bbox[1]+1:
                band = page.within_bbox((page.bbox[0], max(page.bbox[1],top-40), page.bbox[2],top))
                captions = [line['text'] for line in band.extract_text_lines(return_chars=False)
                            if abs(line['x0']-left) <= 12 and line['x1'] <= right+2
                            and top-line['bottom'] <= 15 and re.match(r'^Table\s+\d+\b',line['text'],re.I)]
                if len(captions) == 1:
                    fragment = BeautifulSoup(html, 'html.parser')
                    fragment.caption.string = captions[0]
                    html = str(fragment)
            result.tables.append(html)
        except ValueError as error:
            result.review.append(f'Page {page.page_number}: {error}')
    return result
