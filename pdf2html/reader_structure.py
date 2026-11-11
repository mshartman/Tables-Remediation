"""JSON-safe source evidence and provenance, independent of HTML presentation."""
import hashlib


def box(value):
    return [float(n) for n in value]


def record_block(block, identifier):
    kind = ('frontmatter' if 'frontmatter_html' in block else
            'table' if 'table_html' in block else
            'text' if 'lines' in block else 'image' if block.get('image') else 'unknown')
    record = {'id': identifier, 'kind': kind, 'bbox': box(block['bbox'])}
    if kind == 'text':
        record['lines'] = [
            {'bbox': box(line['bbox']), 'direction': box(line.get('dir', (1, 0))),
             'spans': [{key: (box(span[key]) if key in {'bbox', 'origin'} else span[key])
                        for key in ('text', 'font', 'size', 'flags', 'color', 'bbox', 'origin') if key in span}
                       for span in line['spans']]}
            for line in block['lines']]
        record['heading'] = {'level': block.get('heading_level'),
                             'method': block.get('heading_method', 'contextual_typography_heuristic'),
                             'confidence': 'unvalidated'}
    if kind == 'image':
        record['image'] = {'sha256': hashlib.sha256(block['image']).hexdigest(),
                           'format': block.get('ext'), 'width': block.get('width'),
                           'height': block.get('height')}
    return record


def overlaps(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def record_page(page, blocks, number):
    sources = [record_block(block, f'p{number}-source-{i}') for i, block in enumerate(blocks, 1)]
    links = []
    for link in page.get_links():
        item = {key: link[key] for key in ('kind', 'page', 'uri', 'nameddest') if key in link}
        if 'from' in link:
            item['bbox'] = box(link['from'])
            item['overlapping_source_ids'] = [b['id'] for b in sources if overlaps(b['bbox'], item['bbox'])]
        if 'to' in link and hasattr(link['to'], 'x'):
            item['destination_point'] = [link['to'].x, link['to'].y]
        links.append(item)
    return {'number': number, 'width': page.rect.width, 'height': page.rect.height,
            'rotation': page.rotation, 'coordinate_system': 'PyMuPDF text coordinates in points',
            'source_blocks': sources, 'links': links, 'render_blocks': []}


def record_render_blocks(page_record, blocks):
    for i, block in enumerate(blocks, 1):
        item = record_block(block, f'p{page_record["number"]}-render-{i}')
        item['overlapping_source_ids'] = [source['id'] for source in page_record['source_blocks']
                                          if overlaps(source['bbox'], item['bbox'])]
        item['relationship_method'] = 'geometric_overlap_not_semantic_association'
        page_record['render_blocks'].append(item)
