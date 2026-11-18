"""Local reading view built from detected cells and the PDF's text layer."""
from html import escape
from bisect import bisect_right

from pdfplumber.utils import extract_text


def assign_characters(table, characters):
    """Assign each intersecting glyph once, rejecting uncertain ownership."""
    groups = {cell['id']: [] for cell in table['cells']}
    slots = {}
    for cell in table['cells']:
        for row in range(cell['row'], cell['row'] + cell['row_span']):
            for column in range(cell['column'], cell['column'] + cell['column_span']):
                if (row, column) in slots:
                    raise ValueError('Multiple cells occupy the same grid position.')
                slots[row, column] = cell
    left, top, right, bottom = table['bbox']
    for char in characters:
        if not char.get('text'):
            continue
        x0, y0, x1, y1 = (char[k] for k in ('x0', 'top', 'x1', 'bottom'))
        if x1 <= left or x0 >= right or y1 <= top or y0 >= bottom:
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        cell = slots.get((bisect_right(table['y_boundaries'], cy)-1,
                          bisect_right(table['x_boundaries'], cx)-1))
        if cell is None and not char['text'].strip():
            continue
        if cell is None:
            raise ValueError('Text crosses the outside boundary or has ambiguous cell ownership.')
        a, b, c, d = cell['bbox']
        if char['text'].strip() and (x0 < a - 1 or y0 < b - 1 or x1 > c + 1 or y1 > d + 1):
            raise ValueError('A detected cell boundary crosses a character. Review the source table.')
        groups[cell['id']].append(char)
    if not any(char['text'].strip() for chars in groups.values() for char in chars):
        raise ValueError('This table has no usable PDF text layer. A transcription is needed.')
    return groups


def table_html(table, characters, page_number, header_rows=0, row_headers=False):
    groups = assign_characters(table, characters)
    prefix = table['id']
    if not 0 <= header_rows < table['row_count']:
        raise ValueError('The selected header count must leave at least one body row.')
    for cell in table['cells']:
        if cell['row'] < header_rows < cell['row'] + cell['row_span']:
            raise ValueError('A merged cell crosses the selected header/body boundary.')
    values = {key: extract_text(chars, x_tolerance_ratio=0.15, y_tolerance=3) or ''
              for key, chars in groups.items()}
    cells = sorted(table['cells'], key=lambda c: (c['row'], c['column']))
    columns = [c for c in cells if c['row'] < header_rows]
    if row_headers is None:
        first = next((c for c in columns if c['column'] == 0 and c['column_span'] == 1), None)
        row_headers = bool(first and values[first['id']].strip().casefold() in
                           {'gene', 'compound', 'name', 'identifier', 'id'})
    row_labels = [c for c in cells if row_headers and c['row'] >= header_rows
                  and c['column'] == 0 and c['column_span'] == 1]
    sections = [c for c in cells if c['row'] >= header_rows and c['column'] == 0
                and c['column_span'] == table['column_count'] and c['row_span'] == 1]
    header_ids = {c['id'] for c in columns + row_labels + sections if values[c['id']].strip()}
    label = f'Table on PDF page {page_number}'
    result = [f'<div class="table-scroll" style="overflow-x:auto" role="region" tabindex="0" aria-labelledby="{prefix}-title">',
              f'<table data-reader-headers-repaired=""><caption id="{prefix}-title">{label} · {table["column_count"]} columns</caption>']
    if header_rows:
        result.append('<thead>')
    else:
        result.append('<tbody>')
    section_starts = {c['row'] for c in sections}
    for row in range(table['row_count']):
        if header_rows and row == header_rows:
            result.append('</thead><tbody>')
        elif row > header_rows and row in section_starts:
            result.append('</tbody><tbody>')
        result.append('<tr>')
        for cell in (c for c in cells if c['row'] == row):
            key = cell['id']
            is_header = key in header_ids
            tag = 'th' if is_header else 'td'
            attrs = [f'id="{prefix}-{key}"', f'rowspan="{cell["row_span"]}"',
                     f'colspan="{cell["column_span"]}"']
            if cell in columns and is_header and cell['column_span'] == 1:
                attrs.append('scope="col"')
            elif cell in row_labels and is_header:
                attrs.append('scope="row"')
            refs = [c for c in columns if c['row'] < row and
                    max(c['column'], cell['column']) < min(c['column'] + c['column_span'],
                                                         cell['column'] + cell['column_span'])]
            if row >= header_rows and cell not in sections:
                refs += [c for c in row_labels if c['row'] <= row < c['row'] + c['row_span']]
                preceding = [c for c in sections if c['row'] < row]
                refs += preceding[-1:]
            refs = [f'{prefix}-{c["id"]}' for c in refs if c['id'] != key and c['id'] in header_ids]
            if refs:
                attrs.append(f'headers="{" ".join(refs)}"')
            if cell in sections:
                attrs.append('class="table-section"')
                if is_header:
                    attrs.append('scope="rowgroup"')
            result.append(f'<{tag} {" ".join(attrs)}>{escape(values[key])}</{tag}>')
        result.append('</tr>')
    result.append('</tbody></table></div>')
    return ''.join(result)
