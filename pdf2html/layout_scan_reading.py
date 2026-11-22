"""Repair the production reader's OCR order without changing its page image."""
import re

from bs4 import BeautifulSoup

from layout_raster import page_image_text_layer
from pdf_reader_extract import contextual_headings
from reader_columns import recover_scan_columns, scan_headings, scan_order, text


def safe_fragment(markup):
    fragment = BeautifulSoup(markup, 'html.parser')
    for node in fragment.select('script, style, iframe, object, embed, form, input, button, svg'):
        node.decompose()
    allowed = {'b', 'strong', 'i', 'em', 'sup', 'sub', 'a', 'br', 'ul', 'ol', 'li', 'p'}
    for node in list(fragment.find_all(True)):
        if node.name not in allowed:
            node.unwrap()
            continue
        href = node.get('href') if node.name == 'a' else None
        node.attrs.clear()
        if href and str(href).strip().lower().startswith(('https://', 'http://', 'mailto:')):
            node['href'] = href
    for paragraph in list(fragment.select('p')):
        paragraph.unwrap()
    return fragment


def bda_blocks(data, index, source, renderer):
    if not data:
        return None
    blocks = []
    for element in data['elements']:
        locations = element.get('locations', [])
        located = [loc for loc in locations if loc['page_index'] == index]
        if not located:
            continue
        if len(locations) != 1 or element.get('type') == 'TABLE':
            return None
        if element.get('type') not in {'TEXT', 'FIGURE'}:
            continue
        box = located[0]['bounding_box']
        rect = (box['left'] * source.rect.width, box['top'] * source.rect.height,
                (box['left'] + box['width']) * source.rect.width,
                (box['top'] + box['height']) * source.rect.height)
        fragment = safe_fragment(element.get('representation', {}).get('html', ''))
        if element['type'] == 'TEXT' and not fragment.get_text(strip=True):
            continue
        sub = element.get('sub_type', '')
        blocks.append({'bbox': rect, 'bda_element': element, 'fragment': fragment,
                       'heading_level': 2 if sub in {'TITLE', 'SECTION_HEADER'} else None,
                       'heading_method': 'saved_bda_structure',
                       'metadata': sub in {'HEADER', 'FOOTER', 'PAGE_NUMBER'}})
    recognized = sum(len(b['fragment'].get_text()) for b in blocks if b['bda_element']['type'] == 'TEXT')
    # An incomplete saved extraction must not silently replace a complete page.
    if recognized < max(100, len(source.get_text().strip()) * .6):
        return None
    return blocks


def order_bda_articles(blocks, gutters):
    """Keep a story together when its heading spans adjacent newspaper columns."""
    if len(gutters) < 2:
        return sorted(blocks, key=lambda b: b['bda_element'].get('reading_order', 0))
    headings = [b for b in blocks if b.get('heading_level')]
    if not headings:
        return scan_order(blocks, gutters)
    groups = {id(head): [] for head in headings}
    leading = {id(head): [] for head in headings}
    unowned = []
    for block in blocks:
        if block.get('heading_level'):
            continue
        x0, y0, x1, y1 = block['bbox']
        center = (x0 + x1) / 2
        def aligned(head):
            a, _, c, _ = head['bbox']
            overlap = min(c, x1) - max(a, x0)
            return overlap > 0 and (a - 8 <= center <= c + 8 or overlap >= (x1-x0) * .7)
        candidates = [h for h in headings if h['bbox'][1] <= y0 + 2 and aligned(h)]
        owner = max(candidates, key=lambda h: h['bbox'][1], default=None)
        if block['bda_element']['type'] == 'FIGURE':
            below = [h for h in headings if 0 <= h['bbox'][1] - y1 <= 36 and aligned(h)]
            next_head = min(below, key=lambda h: h['bbox'][1], default=None)
            if next_head is not None:
                leading[id(next_head)].append(block)
                continue
        if owner is None or block.get('metadata'):
            unowned.append(block)
        else:
            groups[id(owner)].append(block)
    proxies = [dict(head, story=leading[id(head)] + [head] + scan_order(groups[id(head)], gutters))
               for head in headings]
    proxies.extend(dict(block, story=[block]) for block in unowned)
    return [block for proxy in scan_order(proxies, gutters) for block in proxy['story']]


def semantic_lines(block):
    lines = []
    for line in block['lines']:
        spans = [s for s in line['spans'] if s['text'].strip()]
        if not spans:
            continue
        dominant = max(spans, key=lambda s: len(s['text']))
        lines.append(dict(line, text=text(line), size=dominant['size'],
                          baseline=dominant['origin'][1]))
    return lines


def recover_scan_reading(soup, doc, renderer, bda_data=None, report=None, remediated=None):
    """Run before navigation is built, in both geometry and saved-BDA modes."""
    pages = soup.select('main > .page')
    review = []
    if report is None:
        report = {'figures': []}
    used_ids = {node['id'] for node in soup.select('[id]')}
    words = {word.lower() for page in doc for word in re.findall(r'[A-Za-z]{4,}', page.get_text())}
    alt_map = {}
    if remediated:
        for image in BeautifulSoup(remediated, 'html.parser').select('img'):
            alt_map[str(image.get('src', '')).rsplit('/', 1)[-1]] = image.get('alt', '')
            if image.has_attr('data-layout-figure-id'):
                alt_map['id:' + str(image['data-layout-figure-id'])] = image.get('alt', '')
                if image.get('data-alt-status') == 'ai-generated':
                    alt_map['status:' + str(image['data-layout-figure-id'])] = 'ai-generated'
    for index, source in enumerate(doc):
        scan = page_image_text_layer(source)
        if not scan:
            continue
        page = pages[index]
        item = {'page': index + 1, 'status': 'unchanged', 'manual_review_required': True}
        review.append(item)
        if 'fallback-page' in page.get('class', []):
            item['reason'] = 'fallback_preserved'
            continue
        if not scan['hide_visual_text']:
            item['reason'] = 'visible_source_annotations_preserved'
            continue
        preferred = bda_blocks(bda_data, index, source, renderer)
        if page.select('table, .sem-table') or (page.select('.fig') and preferred is None):
            item['reason'] = 'structured_table_or_figure_preserved'
            continue
        blocks = source.get_text('dict', flags=renderer.fitz.TEXTFLAGS_DICT & ~renderer.fitz.TEXT_PRESERVE_IMAGES)['blocks']
        recovered, evidence = recover_scan_columns(blocks, source.rect.width)
        item.update(evidence)
        if evidence['method'] != 'ocr_line_gutters' and preferred is None:
            item['reason'] = 'insufficient_multicolumn_evidence'
            continue
        if preferred is None:
            body_size = contextual_headings([recovered])
            scan_headings(recovered, body_size, source.rect.height)
        ordered = (order_bda_articles(preferred, evidence['gutters']) if preferred is not None
                   else scan_order(recovered, evidence['gutters']))
        links = [(tuple(link['from']), link['uri']) for link in source.get_links() if link.get('uri')]
        ctx = renderer.Ctx(links, words)
        ctx.page = index
        replacements, notes = [], []
        heading_tops = [b['bbox'][1] for b in ordered if b.get('heading_level')]
        for number, block in enumerate(ordered, 1):
            element = block.get('bda_element')
            if element and element['type'] == 'FIGURE':
                proxy = {'id': element['id'], 'src_id': element['id'], 'sub': element.get('sub_type', ''),
                         'html': element.get('representation', {}).get('html', ''),
                         'title': element.get('title', ''), 'rect': block['bbox']}
                figure = BeautifulSoup(renderer.render_figure(proxy, source, ctx, alt_map, 110, report, index), 'html.parser')
                x0, y0, x1, y1 = block['bbox']
                publication_mark = (heading_tops and y1 < min(heading_tops)
                                    and y0 < source.rect.height * .16
                                    and x1-x0 < source.rect.width * .25
                                    and y1-y0 < source.rect.height * .14)
                (notes if publication_mark else replacements).extend(list(figure.contents))
                continue
            if element:
                fragment = block['fragment']
            else:
                lines = semantic_lines(block)
                if not lines:
                    continue
                fragment = BeautifulSoup(renderer.clean_text(lines, ctx), 'html.parser')
            label = fragment.get_text(' ', strip=True)
            if not label:
                continue
            # The original visual twins use their own link-number registry.
            for link in fragment.select('a[data-l]'):
                del link['data-l']
            level = block.get('heading_level')
            is_list = element and element.get('sub_type') == 'LIST'
            node = soup.new_tag(f'h{level}' if level else 'div' if is_list else 'p')
            identifier = f'layout-scan-{index + 1}-{number}'
            while identifier in used_ids:
                identifier += '-text'
            used_ids.add(identifier)
            node['id'] = identifier
            node['data-layout-scan-text'] = ''
            node['data-source-bbox'] = ' '.join(f'{n:.2f}' for n in block['bbox'])
            if level:
                node['data-heading-method'] = block['heading_method']
            span = soup.new_tag('div' if is_list else 'span', attrs={'class': 'txt'})
            x0, y0, x1, y1 = block['bbox']
            span['style'] = (f'--text-x:{x0:.2f}pt;--text-y:{y0:.2f}pt;'
                             f'--text-width:{max(1, x1-x0):.2f}pt;--text-height:{max(1, y1-y0):.2f}pt')
            span.extend(list(fragment.contents))
            node.append(span)
            # Retain OCR debris in a disclosure, never silently discard it.
            debris = block.get('metadata') or not level and (sum(c.isalpha() for c in label) < 2 or
                                      sum(c.isalnum() for c in label) / len(label) < .25)
            (notes if debris else replacements).append(node)
        if not replacements:
            item['reason'] = 'no_replacement_text'
            continue
        for node in list(page.find_all(recursive=False)):
            if preferred is not None and 'fig' in node.get('class', []):
                node.decompose()
                continue
            if node.select_one('.txt'):
                for old_text in node.select('.txt'):
                    old_text.decompose()
                node.name = 'div'
                node['aria-hidden'] = 'true'
                node['class'] = list(node.get('class', [])) + ['layout-scan-source-visual']
        source_disclosures = [node.extract() for node in page.select('.reader-source-scan')]
        for node in replacements:
            page.append(node)
        warning = soup.new_tag('p', attrs={'class': 'layout-scan-review', 'data-reader-ocr-warning': ''})
        wording = 'saved Bedrock Data Automation transcription' if preferred is not None else 'existing PDF OCR'
        warning.string = ('Scanned document: this reading text uses ' + wording + '. '
                          'Recognition errors and uncertain article boundaries remain; compare with the original page.')
        if source_disclosures:
            source_disclosures[0].append(warning)
        else:
            page.append(warning)
        if notes:
            disclosure = soup.new_tag('details', attrs={'class': 'layout-scan-notes'})
            summary = soup.new_tag('summary')
            summary.string = 'Publication details and unplaced OCR fragments (source review)'
            disclosure.append(summary)
            disclosure.extend(notes)
            page.append(disclosure)
        for disclosure in source_disclosures:
            page.append(disclosure)
        page['data-layout-scan-recovered'] = ''
        item.update(status='recovered', reading_blocks=len(replacements),
                    provisional_headings=sum(bool(b.get('heading_level')) for b in ordered),
                    unplaced_fragments=len(notes), wording_source='saved_bda_transcription' if preferred is not None else 'existing_pdf_ocr_no_retranscription')
        item['article_order_method'] = 'heading_regions_and_column_geometry' if preferred is not None else 'column_geometry'
    return review
