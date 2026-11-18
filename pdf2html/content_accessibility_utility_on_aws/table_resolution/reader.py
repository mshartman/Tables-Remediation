"""Build semantic tables from local PDF coordinates before document remediation."""
from dataclasses import dataclass, field

from .html_tables import table_html
from .table_structure import detect_page


@dataclass
class PageTables:
    tables: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)


def resolve_page(page, *, first_row_is_header=True, row_headers=None, line_settings=None):
    result = PageTables()
    for table in detect_page(page, line_settings=line_settings)['tables']:
        # Namespace generated HTML IDs independently of the detector's JSON IDs.
        table['id'] = 'local-table-' + table['id']
        try:
            result.tables.append(table_html(table, page.chars, page.page_number,
                                            int(first_row_is_header), row_headers))
        except ValueError as error:
            result.review.append(f'Page {page.page_number}: {error}')
    return result
