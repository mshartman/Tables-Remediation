"""Fit locally resolved tables into PyMuPDF's page reading order."""
from collections import Counter
from io import BytesIO
import re
import unicodedata

from bs4 import BeautifulSoup
import pdfplumber

from .html_tables import table_html
from .table_structure import detect_page


def open_source(doc):
    return pdfplumber.open(doc.name or BytesIO(doc.tobytes()))


def overlaps(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def glyphs(text):
    return Counter(c for c in unicodedata.normalize('NFKC', text) if not c.isspace())


def line_text(line):
    return ''.join(span['text'] for span in line['spans'])


def resolve_article_tables(source, page, blocks, existing):
    """Return new tables plus the precise source lines accounted for by each."""
    recovered, findings = [], []
    if page.rotation or any(abs(a-b) > .1 for a, b in zip(source.bbox, page.rect)):
        return recovered, findings
    lines = [line for block in blocks for line in block.get('lines', [])]
    for table in detect_page(source)['tables']:
        bbox = table['bbox']
        if any(overlaps(bbox, old['bbox']) for old in existing + recovered):
            continue
        if any(overlaps(bbox, image['bbox']) for image in page.get_image_info()):
            continue
        if any(overlaps(bbox, link['from']) for link in page.get_links()):
            continue
        local = [line for line in lines if overlaps(bbox, line['bbox'])]
        top_band = table['y_boundaries'][1]
        headers = [span for line in local if line['bbox'][3] <= top_band + 1
                   for span in line['spans'] if span['text'].strip()]
        if not headers or not all('bold' in span['font'].lower() for span in headers):
            continue
        try:
            markup = table_html(table, source.chars, page.number + 1, 1, None)
        except ValueError as error:
            findings.append(dict(status='needs_review', bbox=bbox, reason=str(error)))
            continue
        tag = BeautifulSoup(markup, 'html.parser').table
        if glyphs(''.join(line_text(line) for line in local)) != glyphs(
                ''.join(cell.get_text() for cell in tag.select('th,td'))):
            findings.append(dict(status='needs_review', bbox=bbox,
                                 reason='Converted cells do not account for the source text.'))
            continue
        caption_lines = [line for line in lines
                         if 0 <= bbox[1] - line['bbox'][3] <= 15
                         and bbox[0]-2 <= line['bbox'][0] <= bbox[0]+12
                         and line['bbox'][2] <= bbox[2]+2
                         and re.match(r'^Table\s+\d+\b', line_text(line).strip(), re.I)]
        box = list(bbox)
        if len(caption_lines) == 1:
            caption = caption_lines[0]
            tag.caption.string = line_text(caption).strip()
            box[1] = caption['bbox'][1]
            local.append(caption)
        tag['data-local-table'] = 'resolved'
        tag['data-reader-headers-repaired'] = ''
        recovered.append(dict(bbox=box, html=str(tag), consumed={id(line) for line in local}))
        findings.append(dict(status='structured_needs_review', bbox=bbox,
                             method=table['method'], rows=table['row_count'],
                             columns=table['column_count'], caption=tag.caption.get_text()))
    return recovered, findings
