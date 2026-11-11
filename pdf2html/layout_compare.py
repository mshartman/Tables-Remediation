"""Offline comparison renderer; never invokes AWS or an image-description API."""
import argparse
import base64
from html import escape
import json
import importlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
for folder in ('.layout-deps', '.reader-deps'):
    sys.path.insert(0, str(ROOT / folder))

import pymupdf
from bs4 import BeautifulSoup
try:
    iris_layout = importlib.import_module('experimental_layout.iris_layout')
except ModuleNotFoundError as error:
    if error.name != 'experimental_layout.iris_layout':
        raise
    iris_layout = None
from pdf_reader_extract import contextual_headings, ordered
from reader_audit import audit_reader
from layout_presentation import decorate
from layout_structure import refine_headings, split_abstract, split_leading_headings
from layout_tables import ruled_tables, inside
from layout_raster import page_image_text_layer
from layout_annotations import rotation_profile


def geometry_structure(doc):
    """Adapt local heuristics to the renderer's element schema, not BDA output."""
    blocks = [split_abstract(page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks'])
              for page in doc]
    body_size = contextual_headings(blocks)
    blocks = split_leading_headings(blocks, [p.rect.height for p in doc], body_size)
    body_size = contextual_headings(blocks)
    refine_headings(blocks, [p.rect.height for p in doc], body_size)
    elements = []
    table_findings = []
    composite_pages = []
    for pi, page in enumerate(doc):
        composite = page_image_text_layer(page)
        if composite:
            composite_pages.append(dict(composite,page=pi+1))
        tables, findings = ruled_tables(page)
        table_findings.extend(dict(item,page=pi+1) for item in findings)
        for index, block in enumerate(ordered(blocks[pi], page.rect.width)):
            if not block.get('lines'):
                continue
            lines = [line for line in block['lines'] if not any(inside(line,t['bbox']) for t in tables)]
            if not lines:
                continue
            block = dict(block,lines=lines,bbox=(min(l['bbox'][0] for l in lines),min(l['bbox'][1] for l in lines),
                                               max(l['bbox'][2] for l in lines),max(l['bbox'][3] for l in lines)))
            x0, y0, x1, y1 = block['bbox']
            level = block.get('heading_level')
            elements.append({
                'id': f'local-{pi}-{index}', 'type': 'TEXT',
                'sub_type': ('HEADER' if block.get('layout_running_header') else
                             'TITLE' if level == 1 else 'SECTION_HEADER' if level else 'LOCAL_PARAGRAPH'),
                'heading_level': level,
                'heading_method': block.get('layout_heading_evidence'),
                'reading_order': index, 'representation': {},
                'locations': [{'page_index': pi, 'bounding_box': {
                    'left': x0 / page.rect.width, 'top': y0 / page.rect.height,
                    'width': (x1-x0) / page.rect.width, 'height': (y1-y0) / page.rect.height}}],
            })
        for index, table in enumerate(tables):
            x0,y0,x1,y1=table['bbox']
            elements.append({'id':f'local-table-{pi}-{index}','type':'TABLE','reading_order':0,
                'representation':{'html':table['html']},'locations':[{'page_index':pi,'bounding_box':{
                    'left':x0/page.rect.width,'top':y0/page.rect.height,
                    'width':(x1-x0)/page.rect.width,'height':(y1-y0)/page.rect.height}}]})
        # Order tables among the text blocks by the same column-aware geometry.
        page_elements=[e for e in elements if e['locations'][0]['page_index']==pi]
        proxies=[]
        for element in page_elements:
            b=element['locations'][0]['bounding_box']
            proxies.append({'bbox':(b['left']*page.rect.width,b['top']*page.rect.height,
                                    (b['left']+b['width'])*page.rect.width,(b['top']+b['height'])*page.rect.height),'element':element})
        for index,proxy in enumerate(ordered(proxies,page.rect.width)):
            proxy['element']['reading_order']=index
        for index, info in enumerate(page.get_image_info()):
            if composite:
                continue
            x0, y0, x1, y1 = pymupdf.Rect(info['bbox']) & page.rect
            if x1 <= x0 or y1 <= y0:
                continue
            elements.append({
                'id': f'local-image-{pi}-{index}', 'type': 'FIGURE', 'sub_type': '',
                'reading_order': len(elements), 'representation': {},
                'locations': [{'page_index': pi, 'bounding_box': {
                    'left': x0/page.rect.width, 'top': y0/page.rect.height,
                    'width': (x1-x0)/page.rect.width, 'height': (y1-y0)/page.rect.height}}],
            })
    return {'elements': elements, 'local_table_findings':table_findings,'composite_text_pages':composite_pages}


def validate_structure(data, page_count):
    if not isinstance(data, dict) or not isinstance(data.get('elements'), list) or not data['elements']:
        raise ValueError('Structure JSON must contain a nonempty elements array. Use --geometry-only for a local demo.')
    located = 0
    identifiers = set()
    for element in data['elements']:
        if not isinstance(element, dict) or not isinstance(element.get('id'), str) or element['id'] in identifiers:
            raise ValueError('Each structure element needs a unique string id.')
        identifiers.add(element['id'])
        for loc in element.get('locations', []):
            pi = loc.get('page_index')
            box = loc.get('bounding_box', {})
            if type(pi) is not int or not 0 <= pi < page_count:
                raise ValueError('Structure page indices do not match this PDF.')
            values = [box.get(k) for k in ('left', 'top', 'width', 'height')]
            if any(type(v) not in (int, float) or not 0 <= v <= 1 for v in values):
                raise ValueError('Structure bounding boxes must use normalized values between 0 and 1.')
            if values[2] <= 0 or values[3] <= 0 or values[0]+values[2] > 1.01 or values[1]+values[3] > 1.01:
                raise ValueError('Structure bounding box is outside the page or empty.')
            located += 1
    if not located:
        raise ValueError('No located elements were found in the structure JSON.')


def fallback_pages(doc, data, rotations=None):
    findings = []
    for pi, page in enumerate(doc):
        rotated = (rotations[pi] if rotations is not None else rotation_profile(page))['requires_fallback']
        if rotated or not page.get_text().strip():
            # Coordinate sorting can reverse vertical words; retain PDF stream order.
            text = page.get_text(sort=not rotated).strip()
            if not text:
                # BDA text can preserve OCR when the PDF has no text layer.
                fragments = []
                for el in sorted(data['elements'], key=lambda e: e.get('reading_order', 0)):
                    if any(loc['page_index'] == pi for loc in el.get('locations', [])):
                        fragments.append(BeautifulSoup(el.get('representation', {}).get('html', ''), 'html.parser').get_text(' ', strip=True))
                text = '\n'.join(filter(None, fragments))
            findings.append({'page': pi+1, 'reason': 'rotated_text' if rotated else 'no_pdf_text',
                             'text': text})
    return findings


def finish_output(path, doc, report, fallbacks, geometry_only, title):
    soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
    if title:
        soup.title.string = title
    pages = soup.select('main > .page')
    for i, page in enumerate(pages, 1):
        marker = page.select_one('.pb')
        marker['id'] = f'layout-page-{i}'
        marker['aria-label'] = f'Page {i}'
        marker['tabindex'] = '-1'
        page.select_one('.pbl').string = f'Page {i}'
    for item in fallbacks:
        page = pages[item['page']-1]
        for node in list(page.contents):
            if getattr(node, 'name', None) and not set(node.get('class', [])) & {'pb', 'pbl'}:
                node.extract()
        pix = doc[item['page']-1].get_pixmap(dpi=110)
        src = 'data:image/png;base64,' + base64.b64encode(pix.tobytes('png')).decode('ascii')
        page.append(BeautifulSoup(
            '<figure class="fallback-image"><img data-alt-status="missing" src="' + src + '" alt="Original PDF page ' + str(item['page']) +
            '. Layout reconstruction is unavailable; review the extracted text below."></figure>' +
            '<section class="fallback-text"><h2>Extracted text for PDF page ' + str(item['page']) + '</h2><p>' +
            escape(item['text'] or 'No readable text is available. OCR or a transcribed alternative is required.')
            .replace('\n', '<br>') + '</p></section>', 'html.parser'))
        page['class'].append('fallback-page')
    for item in report.get('margin_note_pages',[]):
        page=pages[item['page']-1]
        disclosure=soup.new_tag('details',attrs={'class':'reader-margin-notes'})
        summary=soup.new_tag('summary');summary.string='Source margin notes';disclosure.append(summary)
        for note in item['notes']:
            text=soup.new_tag('p');text.string=note['text'];disclosure.append(text)
            rect=pymupdf.Rect(note['bbox']) & doc[item['page']-1].rect
            if rect.is_empty:continue
            pix=doc[item['page']-1].get_pixmap(clip=rect,dpi=110)
            source='data:image/png;base64,'+base64.b64encode(pix.tobytes('png')).decode('ascii')
            visual=soup.new_tag('img',src=source,alt='',attrs={'class':'source-margin-visual','aria-hidden':'true'})
            visual['style']=f'position:absolute;left:{rect.x0:.2f}pt;top:{rect.y0:.2f}pt;width:{rect.width:.2f}pt;height:{rect.height:.2f}pt'
            page.append(visual)
        page.append(disclosure)
    for item in report.get('composite_text_pages',[]):
        page=pages[item['page']-1]
        if 'fallback-page' in page.get('class',[]):continue
        if item['hide_visual_text']:page['data-layout-image-text']='hidden-source-text'
        source=page.select_one('.bg')['src']
        disclosure=soup.new_tag('details',attrs={'class':'reader-source-scan'})
        summary=soup.new_tag('summary');summary.string='Original page image and text-layer notice'
        disclosure.append(summary)
        note=soup.new_tag('p');note.string=item['review'];disclosure.append(note)
        image=soup.new_tag('img',src=source,alt=f'Original page {item["page"]}, for visual comparison. Figures on this page still require descriptive alternatives.')
        disclosure.append(image);page.append(disclosure)
    if report.get('composite_text_pages'):
        warning=soup.new_tag('aside',attrs={'data-layout-scan-warning':'','aria-label':'Text-layer review notice'})
        warning.string='Some pages contain a full-page image and an unverified text layer. The text is available in Reading view; OCR accuracy and any figures still require review.'
        soup.select_one('main').insert(0,warning)
    # Source table markup must not retain active content or arbitrary protocols.
    for node in soup.select('script, iframe, object, embed, form, base, link, meta[http-equiv]'):
        node.decompose()
    trusted_script = soup.new_tag('script')
    trusted_script.string = BeautifulSoup(iris_layout.TEMPLATE, 'html.parser').script.string
    soup.body.append(trusted_script)
    for node in soup.find_all(True):
        for attr in list(node.attrs):
            if attr.lower().startswith('on'):
                del node[attr]
        if node.has_attr('href') and not str(node['href']).lower().startswith(('https://', 'http://', 'mailto:', '#')):
            del node['href']
        if node.name == 'img' and not str(node.get('src', '')).startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
            node.replace_with(soup.new_string('[External image omitted from offline comparison]'))
    policy = soup.new_tag('meta', attrs={'http-equiv': 'Content-Security-Policy', 'content':
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"})
    soup.head.insert(0, policy)
    report['article_navigation'] = decorate(soup, pages, geometry_only, title or soup.title.get_text())
    output = '<!doctype html>\n' + str(soup)
    path.write_text(output, encoding='utf-8')
    report['static_audit'] = audit_reader(output,table_findings=report.get('local_table_findings'))


def compare(pdf, output, bda_json=None, remediated=None, geometry_only=False, title=None):
    if iris_layout is None:
        raise RuntimeError('The optional page-layout renderer is private and is not included in this repository. Use the legacy reader or obtain an authorized private copy.')
    pdf, output = Path(pdf).resolve(), Path(output).resolve()
    if bool(bda_json) == bool(geometry_only):
        raise ValueError('Choose --bda-json PATH or --geometry-only, not both.')
    if output == pdf or pdf in output.parents:
        raise ValueError('Choose a separate output directory.')
    output.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf) as doc:
        if doc.needs_pass or not len(doc):
            raise ValueError('PDF must contain pages and be readable without a password.')
        data = geometry_structure(doc) if geometry_only else json.loads(Path(bda_json).read_text(encoding='utf-8-sig'))
        if not geometry_only:
            validate_structure(data, len(doc))
        structure = output / 'layout-structure.json'
        products = {structure, output / 'layout.html', output / 'layout-review.json'}
        if pdf in products or any(p and Path(p).resolve() in products for p in (bda_json, remediated)):
            raise ValueError('Output must not overwrite a supplied input file.')
        structure.write_text(json.dumps(data, indent=2), encoding='utf-8')
        rotations=[rotation_profile(page) for page in doc]
        fallbacks = fallback_pages(doc, data, rotations)
        target = output / 'layout.html'
        report = iris_layout.build(str(pdf), str(structure), str(target),
                                   str(remediated) if remediated else None, dpi=110, bg_dpi=110)
        report.update(structure_source='local_geometry' if geometry_only else 'saved_bda',
                      network_calls=0, source=str(pdf),
                      local_table_findings=data.get('local_table_findings',[]),
                      composite_text_pages=data.get('composite_text_pages',[]),
                      margin_note_pages=[{'page':i+1,'notes':item['notes']} for i,item in enumerate(rotations) if item['notes']],
                      fallback_pages=[{k:v for k,v in item.items() if k != 'text'} for item in fallbacks],
                      limitations=['Human review required. Local table recovery supports only unambiguous ruled tables with sequential row IDs.',
                                   'Reading order, scientific content and image alternatives are not certified.'])
        finish_output(target, doc, report, fallbacks, geometry_only, title or pdf.stem)
        (output / 'layout-review.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--bda-json', type=Path)
    group.add_argument('--geometry-only', action='store_true')
    parser.add_argument('--remediated', type=Path)
    parser.add_argument('--title')
    args = parser.parse_args()
    try:
        print(compare(**vars(args)))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f'Comparison could not be created: {exc}\n')


if __name__ == '__main__':
    main()
