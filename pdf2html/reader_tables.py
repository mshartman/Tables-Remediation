"""Conservative local grid-table extraction; ambiguous layouts remain source text."""
import re


def contains(outer, inner, tolerance=1):
    return (inner[0] >= outer[0] - tolerance and inner[1] >= outer[1] - tolerance
            and inner[2] <= outer[2] + tolerance and inner[3] <= outer[3] + tolerance)


def extract_tables(page, blocks, render_line):
    replacements, entries = [], []
    captions = [b for b in blocks if 'lines' in b and re.match(
        r'^Table\s+\d+\b', ' '.join(s['text'] for l in b['lines'] for s in l['spans']).strip(), re.I)]
    try:
        tables = page.find_tables().tables
    except Exception:
        return [], [{'status': 'detection_failed', 'reason': 'Local table detection failed.'}]
    used_captions = set()
    for table in tables:
        box = table.bbox
        reason = None
        rows = table.rows
        if len(rows) < 2 or table.col_count < 2 or any(
                len(row.cells) != table.col_count or any(cell is None for cell in row.cells)
                for row in rows):
            reason = 'Merged or incomplete cells require review.'
        overlapping = [b for b in blocks if contains(box, b['bbox'])]
        if any(b.get('image') for b in overlapping):
            reason = 'Images within cells require review.'
        # Reject text crossing the boundary rather than dropping neighboring prose.
        for block in blocks:
            for line in block.get('lines', []):
                b = line['bbox']
                intersects = b[0] < box[2] and b[2] > box[0] and b[1] < box[3] and b[3] > box[1]
                if intersects and not contains(box, b):
                    reason = 'Text crosses the detected table boundary.'
        # Vector figures inside cells cannot be reconstructed as text.
        for drawing in page.get_drawings():
            rect = drawing['rect']
            if contains(box, rect) and any(item[0] == 'c' for item in drawing['items']):
                reason = 'Vector artwork within cells requires review.'
        cell_rows = []
        if not reason:
            for row_index, row in enumerate(rows):
                cells = []
                for cell in row.cells:
                    lines = [line for block in blocks for line in block.get('lines', [])
                             if contains(cell, line['bbox'])]
                    spans = [s for line in lines for s in line['spans'] if s['text'].strip()]
                    if row_index == 0 and (not spans or not all('bold' in s.get('font', '').lower() for s in spans)):
                        reason = 'Column header semantics are uncertain.'
                    cells.append(' '.join(render_line(line) for line in sorted(lines, key=lambda l: l['bbox'][1])))
                cell_rows.append(cells)
        if reason:
            entries.append({'status': 'needs_review', 'bbox': list(box), 'reason': reason})
            continue
        nearby = [(i, b) for i, b in enumerate(captions) if i not in used_captions
                  and 0 <= box[1] - b['bbox'][3] <= 60
                  and b['bbox'][0] >= box[0] - 5 and b['bbox'][2] <= box[2] + 5]
        caption = ''
        if nearby:
            index, block = min(nearby, key=lambda pair: box[1] - pair[1]['bbox'][3])
            used_captions.add(index)
            caption = '<caption>' + ' '.join(render_line(l) for l in block['lines']) + '</caption>'
        html = '<table>' + caption + '<thead><tr>' + ''.join(
            '<th scope="col">' + value + '</th>' for value in cell_rows[0]) + '</tr></thead><tbody>'
        html += ''.join('<tr>' + ''.join('<td>' + value + '</td>' for value in row) + '</tr>' for row in cell_rows[1:])
        html += '</tbody></table>'
        replacements.append({'bbox': box, 'table_html': html})
        entries.append({'status': 'structured_needs_review', 'bbox': list(box),
                        'rows': len(rows), 'columns': table.col_count})
    for i, block in enumerate(captions):
        if i not in used_captions:
            title = ' '.join(s['text'] for l in block['lines'] for s in l['spans'])
            entries.append({'status': 'needs_review', 'caption': title,
                            'reason': 'Caption found without a confidently structured table.'})
    # Only remove text wholly inside accepted cells, plus explicitly associated captions.
    for block in blocks:
        if any(block is captions[i] for i in used_captions):
            block['table_caption_consumed'] = True
    return replacements, entries
