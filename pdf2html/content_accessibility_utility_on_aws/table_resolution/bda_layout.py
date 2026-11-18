"""Use verified local table geometry in the saved BDA layout representation."""
from bs4 import BeautifulSoup

from .article import glyphs, open_source, overlaps, resolve_article_tables, tagged_article_tables


def apply_to_layout(doc, data):
    """Replace only single-page text/table elements fully accounted for by a recovered table."""
    findings = data.setdefault('local_table_findings', [])
    with open_source(doc) as source:
        for index, page in enumerate(doc):
            blocks = page.get_text('dict', flags=0)['blocks']
            pdf_page = source.pages[index]
            try:
                tagged, _ = tagged_article_tables(pdf_page, page, blocks)
                inferred, _ = resolve_article_tables(pdf_page, page, blocks, tagged)
                tables = tagged + inferred
            finally:
                pdf_page.close()
            for table in tables:
                box = table['bbox']
                normalized = (box[0]/page.rect.width, box[1]/page.rect.height,
                              box[2]/page.rect.width, box[3]/page.rect.height)
                matched = matching_elements(data['elements'], index, normalized, table['html'])
                if not matched:
                    findings.append(dict(page=index+1, bbox=box, status='needs_review',
                                         reason='Local table could not be aligned to source elements.'))
                    continue
                first = min(matched, key=lambda e: e.get('reading_order', 0))
                left, top, right, bottom = normalized
                first.update(type='TABLE', sub_type='', representation={'html': table['html']},
                             locations=[{'page_index': index, 'bounding_box': {
                                 'left': left, 'top': top, 'width': right-left, 'height': bottom-top}}])
                removed = {e['id'] for e in matched if e is not first}
                data['elements'] = [e for e in data['elements'] if e['id'] not in removed]
                findings.append(dict(page=index+1, bbox=box, status='structured_needs_review'))


def matching_elements(elements, page_index, box, html):
    selected = []
    text = []
    for element in elements:
        locations = element.get('locations', [])
        for location in locations:
            if location.get('page_index') != page_index:
                continue
            b = location['bounding_box']
            rect = (b['left'], b['top'], b['left']+b['width'], b['top']+b['height'])
            if not overlaps(box, rect):
                continue
            if (len(locations) != 1 or element.get('type') not in {'TEXT','TABLE'}
                    or rect[0] < box[0]-.002 or rect[1] < box[1]-.002
                    or rect[2] > box[2]+.002 or rect[3] > box[3]+.002):
                return []
            fragment = BeautifulSoup(element.get('representation', {}).get('html', ''), 'html.parser')
            if fragment.select_one('img, a, math, svg, [id], th[scope], td[headers]'):
                return []
            text.append(fragment.get_text())
            selected.append(element)
    if glyphs(''.join(text)) != glyphs(BeautifulSoup(html, 'html.parser').get_text()):
        return []
    return selected
