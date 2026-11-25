"""Read single-page, rectangular tables whose source tags identify their text.

Marked-content IDs (MCIDs) are page-local. A missing text run, unsupported
cell span, or ambiguous owner leaves the table to the other reader paths.
"""
from collections import Counter, defaultdict
from html import escape
import re
import unicodedata
from weakref import WeakKeyDictionary

from pdfplumber.utils import extract_text


_document_indexes = WeakKeyDictionary()
_TEXT_TAGS = {'P', 'Span', 'Div', 'LBody', 'Strong', 'Em'}


def _walk(node, inherited_page=None):
    page = node.get('page_number', inherited_page)
    yield node, page
    for child in node.get('children', []):
        yield from _walk(child, page)


def _refs(node, inherited_page):
    return [(page, mcid) for item, page in _walk(node, inherited_page)
            for mcid in item.get('mcids', [])]


def _index(pdf):
    if pdf in _document_indexes:
        return _document_indexes[pdf]
    by_page = defaultdict(list)
    try:
        roots = pdf.structure_tree
        owners = Counter()
        tables = []
        for root in roots:
            for node, page in _walk(root):
                owners.update((page, mcid) for mcid in node.get('mcids', []))
                if node.get('type') == 'Table':
                    tables.append((node, page))
        for number, (table, page) in enumerate(tables, 1):
            refs = _refs(table, page)
            pages = {p for p, _ in refs}
            if (len(pages) == 1 and None not in pages and
                    all(owners[ref] == 1 and (None, ref[1]) not in owners for ref in refs)):
                by_page[refs[0][0]].append((number, table, page))
    except MemoryError:
        raise
    except Exception:
        # Tag trees are optional; some PDFs have invalid object/parent links.
        by_page.clear()
    _document_indexes[pdf] = by_page
    return by_page


def _rows_and_caption(table):
    rows, caption = [], None
    for node in table.get('children', []):
        kind = node.get('type')
        if kind == 'TR':
            rows.append(node)
        elif kind in {'THead', 'TBody', 'TFoot'}:
            children = node.get('children', [])
            if node.get('mcids') or any(child.get('type') != 'TR' for child in children):
                return [], None
            if 'page_number' in node:
                children = [dict({'page_number': node['page_number']}, **child) for child in children]
            rows.extend(children)
        elif kind == 'Caption' and caption is None:
            caption = node
        else:
            return [], None
    return rows, caption


def _content(node, default_page, by_mcid):
    for item, _ in _walk(node, default_page):
        if ((item is not node and item.get('type') not in _TEXT_TAGS)
                or item.get('actual_text') or item.get('alt_text')):
            return None
    refs = _refs(node, default_page)
    if not refs or any(not by_mcid.get(mcid) for _, mcid in refs):
        return None
    chars = [c for _, mcid in refs for c in by_mcid[mcid]]
    value = ' '.join(extract_text(chars, x_tolerance_ratio=.15, y_tolerance=3).split())
    # Normalization permits typographic ligatures such as fi, but every other
    # visible character needs to survive assignment to this specific cell.
    raw = ''.join(char['text'] for char in chars)
    if not value or _glyphs(raw) != _glyphs(value):
        return None
    return value, chars


def _glyphs(value):
    return Counter(c for c in unicodedata.normalize('NFKC', value) if not c.isspace())


def _bbox(chars):
    return [min(c['x0'] for c in chars), min(c['top'] for c in chars),
            max(c['x1'] for c in chars), max(c['bottom'] for c in chars)]


def _render(page, seq, table, parent_page, by_mcid):
    rows, caption_node = _rows_and_caption(table)
    if len(rows) < 2 or table.get('mcids'):
        return None
    data, source_chars = [], []
    width = len(rows[0].get('children', []))
    if width < 2:
        return None
    for row in rows:
        children = row.get('children', [])
        if len(children) != width or row.get('mcids'):
            return None
        cells = []
        for cell in children:
            attrs = cell.get('attributes', {})
            if (cell.get('type') not in {'TD', 'TH'} or attrs.get('ColSpan', 1) != 1
                    or attrs.get('RowSpan', 1) != 1 or attrs.get('Headers')):
                return None
            content = _content(cell, row.get('page_number', parent_page), by_mcid)
            if content is None:
                return None
            value, chars = content
            scope = attrs.get('Scope')
            if scope not in {None, 'Row', 'Column'}:
                return None
            cells.append((cell['type'], value, scope))
            source_chars.extend(chars)
        data.append(cells)
    box = _bbox(source_chars)
    # Source tags sometimes omit a label or reuse an MCID. Never replace a
    # table that would cut out additional visible text between the tagged cells.
    owned = {id(c) for c in source_chars}
    for char in page.chars:
        if id(char) in owned or not char['text'].strip():
            continue
        x, y = (char['x0']+char['x1'])/2, (char['top']+char['bottom'])/2
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            return None
    caption = f'Table on PDF page {page.page_number}'
    cap_box = None
    if caption_node is not None:
        content = _content(caption_node, parent_page, by_mcid)
        if content is None:
            return None
        caption, chars = content
        cap_box = _bbox(chars)
    prefix = f'source-table-p{page.page_number}-{seq}'
    html = ['<table data-local-table="tagged" data-reader-headers-repaired="">',
            f'<caption id="{prefix}-title">{escape(caption)}</caption>']
    has_header = all(kind == 'TH' and scope in {None, 'Column'} for kind, _, scope in data[0])
    body = data[1:] if has_header else data
    # Only promote plain TDs when consecutive Q labels identify survey rows.
    ids = [re.fullmatch(r'Q([1-9][0-9]{0,8})', row[0][1], re.I) for row in body]
    question_labels = len(ids) >= 2 and all(ids) and all(
        int(b[1]) == int(a[1])+1 for a, b in zip(ids, ids[1:]))
    if has_header:
        html.append('<thead><tr>')
        for col, (_, value, _) in enumerate(data[0]):
            html.append(f'<th scope="col" id="{prefix}-col{col}">{escape(value)}</th>')
        html.append('</tr></thead>')
    html.append('<tbody>')
    for n, row in enumerate(body):
        row_header = row[0][0] == 'TH' or question_labels
        html.append('<tr>')
        for col, (kind, value, scope) in enumerate(row):
            if kind == 'TH' and (col != 0 or scope not in {None, 'Row'}):
                return None
            refs = [f'{prefix}-col{col}'] if has_header else []
            if col > 0 and row_header:
                refs.append(f'{prefix}-row{n}')
            attrs = f' headers="{" ".join(refs)}"' if refs else ''
            if col == 0 and row_header:
                html.append(f'<th scope="row" id="{prefix}-row{n}"{attrs}>{escape(value)}</th>')
            else:
                html.append(f'<td{attrs}>{escape(value)}</td>')
        html.append('</tr>')
    html.append('</tbody></table>')
    return dict(bbox=box, html=''.join(html), rows=len(data), columns=width,
                method='tags', caption=caption, caption_bbox=cap_box)


def tagged_tables(page):
    """Return complete source-tagged table candidates in pdfplumber coordinates."""
    candidates = _index(page.pdf).get(page.page_number, [])
    if not candidates:
        return []
    by_mcid = defaultdict(list)
    for char in page.chars:
        if char.get('mcid') is not None:
            by_mcid[char['mcid']].append(char)
    results = []
    for seq, table, parent_page in candidates:
        try:
            candidate = _render(page, seq, table, parent_page, by_mcid)
        except (AttributeError, KeyError, TypeError, ValueError):
            # A malformed attribute or character record is insufficient evidence.
            continue
        if candidate:
            results.append(candidate)
    return results
