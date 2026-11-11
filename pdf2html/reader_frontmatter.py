"""Conservative first-page grouping anchored to the supplied article title."""
import base64
from html import escape


def group_frontmatter(blocks, title, width, render_line):
    def plain(b):
        return ' '.join(''.join(s['text'] for s in line['spans']) for line in b.get('lines', []))
    normalize = lambda value: ' '.join(value.split()).casefold()
    titles = [b for b in blocks if normalize(plain(b)) == normalize(title)]
    if len(titles) != 1:
        return blocks
    title_block = titles[0]
    box = title_block['bbox']
    prose = [b for b in blocks if b['bbox'][1] >= box[3]
             and b['bbox'][0] >= box[0] - 5 and len(plain(b)) > 180]
    if not prose:
        return blocks
    boundary = min(b['bbox'][1] for b in prose)
    if boundary - box[3] > 100:
        return blocks
    authors = [b for b in blocks if b is not title_block
               and b['bbox'][1] >= box[3] and b['bbox'][3] <= boundary
               and b['bbox'][0] >= box[0] - 5]
    metadata = [b for b in blocks if b is not title_block and b not in authors
                and (b['bbox'][3] <= box[1] or
                     (b['bbox'][2] < box[0] and b['bbox'][3] < boundary))]
    if not authors:
        return blocks
    def image(b):
        encoded = base64.b64encode(b['image']).decode('ascii')
        w = max(1, round((b['bbox'][2] - b['bbox'][0]) * 96 / 72))
        return f'<img width="{w}" data-alt-status="missing" alt="Publisher image; description unavailable." src="data:image/{escape(b["ext"])};base64,{encoded}">'
    rows = []
    for b in sorted(authors, key=lambda b: (b['bbox'][1], b['bbox'][0])):
        if 'lines' in b:
            for line in b['lines']:
                y = line['bbox'][3]
                row = next((r for r in rows if abs(r[0] - y) < 5), None)
                if row is None:
                    row = [y, []]; rows.append(row)
                row[1].extend(line['spans'])
    author_html = ' '.join(render_line({'spans': sorted(spans, key=lambda s: s['origin'][0])})
                           for _, spans in sorted(rows))
    icons = ''.join(image(b) for b in authors if b.get('image'))
    meta_html = ''.join('<p>' + ' '.join(render_line(l) for l in b['lines']) + '</p>'
                        if 'lines' in b else image(b) if b.get('image') else ''
                        for b in sorted(metadata, key=lambda b: (b['bbox'][1], b['bbox'][0])))
    html = ('<section class="reader-frontmatter" aria-label="Article information">'
            '<p class="reader-authors">' + author_html + '</p>'
            '<div class="reader-publisher-details">' + meta_html + icons + '</div></section>')
    grouped = authors + metadata
    remaining = [b for b in blocks if b not in grouped]
    remaining.append({'bbox': (0, min(b['bbox'][1] for b in grouped), width, boundary), 'frontmatter_html': html})
    return remaining
