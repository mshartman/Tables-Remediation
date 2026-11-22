"""Conservative column recovery for OCR-backed page images; no model calls."""
from bisect import bisect_right
from collections import defaultdict
from statistics import median
import re


def text(line):
    return ''.join(span['text'] for span in line.get('spans', []))


def bounds(items):
    boxes = [item['bbox'] for item in items]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def detect_gutters(blocks, width):
    # Repeated left edges of substantial text lines are stronger evidence than
    # a newspaper's OCR block boxes, which may bridge several unrelated stories.
    lines = [line for block in blocks for line in block.get('lines', [])
             if len(text(line).strip()) >= 25
             and width * .10 <= line['bbox'][2] - line['bbox'][0] <= width * .36]
    clusters = []
    for line in sorted(lines, key=lambda item: item['bbox'][0]):
        if not clusters or line['bbox'][0] - median(l['bbox'][0] for l in clusters[-1]) > width * .012:
            clusters.append([])
        clusters[-1].append(line)
    clusters = [group for group in clusters if len(group) >= max(6, len(lines) * .045)]
    gutters = []
    for left, right in zip(clusters, clusters[1:]):
        start = median(line['bbox'][0] for line in right)
        end = median(line['bbox'][2] for line in left)
        if 2 <= start - end <= width * .08:
            gutters.append((end + start) / 2)
    return gutters


def merge_row_fragments(lines):
    groups = []
    for line in sorted(lines, key=lambda item: (item['bbox'][1], item['bbox'][0])):
        if groups:
            row = groups[-1][0]
            height = min(line['bbox'][3] - line['bbox'][1], row['bbox'][3] - row['bbox'][1])
            overlap = min(line['bbox'][3], row['bbox'][3]) - max(line['bbox'][1], row['bbox'][1])
            if height > 0 and overlap / height >= .65:
                groups[-1].append(line)
                continue
        groups.append([line])
    rows = []
    for group in groups:
        group.sort(key=lambda item: item['bbox'][0])
        spans = []
        for line in group:
            incoming = [dict(span) for span in line['spans']]
            if spans and incoming and not spans[-1]['text'].endswith(' ') and not incoming[0]['text'].startswith(' '):
                incoming[0]['text'] = ' ' + incoming[0]['text']
            spans.extend(incoming)
        rows.append(dict(group[0], bbox=bounds(group), spans=spans))
    return rows


def recover_scan_columns(blocks, width):
    gutters = detect_gutters(blocks, width)
    if len(gutters) < 2:
        return blocks, {'method': 'original_blocks', 'gutters': gutters}
    output = []
    split_count = 0
    body_sizes = [s['size'] for b in blocks for l in b.get('lines', [])
                  if len(text(l).strip()) >= 25 for s in l['spans']
                  for _ in range(len(s['text']))]
    body_size = median(body_sizes) if body_sizes else 10
    for block in blocks:
        if 'lines' not in block:
            output.append(block)
            continue
        groups = defaultdict(list)
        for line in block['lines']:
            box = line['bbox']
            start, end = bisect_right(gutters, box[0] + 1), bisect_right(gutters, box[2] - 1)
            count = sum(len(s['text']) for s in line['spans'])
            size = sum(s['size'] * len(s['text']) for s in line['spans']) / max(1, count)
            groups[(start, end, size >= body_size * 1.65)].append(line)
        split_count += max(0, len(groups) - 1)
        for (start, end, large), lines in groups.items():
            rows = merge_row_fragments(lines)
            runs = []
            for row in rows:
                if not runs or (large and row['bbox'][1] - runs[-1][-1]['bbox'][3] > body_size * 2):
                    runs.append([])
                runs[-1].append(row)
            for run in runs:
                output.append(dict(block, lines=run, bbox=bounds(run),
                                   reader_column_span=(start, end)))
    return output, {'method': 'ocr_line_gutters', 'gutters': gutters,
                    'split_blocks': split_count, 'review_required': True}


def scan_order(blocks, gutters):
    """Split at evidenced gutters, treating spanning headlines as local barriers."""
    if not gutters or len(blocks) < 2:
        return sorted(blocks, key=lambda b: (b['bbox'][1], b['bbox'][0]))
    def crosses(block, cut):
        return block['bbox'][0] < cut < block['bbox'][2]
    cut = min(gutters, key=lambda x: sum(b['bbox'][3] - b['bbox'][1] for b in blocks if crosses(b, x)))
    barriers = sorted([b for b in blocks if crosses(b, cut)], key=lambda b: (b['bbox'][1], b['bbox'][0]))
    remaining = [b for b in blocks if not crosses(b, cut)]
    result = []
    def sides(items):
        left = [b for b in items if b['bbox'][2] <= cut]
        right = [b for b in items if b['bbox'][0] >= cut]
        return (scan_order(left, [x for x in gutters if x < cut]) +
                scan_order(right, [x for x in gutters if x > cut]))
    for barrier in barriers:
        before = [b for b in remaining if b['bbox'][1] < barrier['bbox'][1]]
        result.extend(sides(before))
        chosen = {id(b) for b in before}
        remaining = [b for b in remaining if id(b) not in chosen]
        result.append(barrier)
    return result + sides(remaining)


def scan_headings(blocks, body_size, height):
    """OCR fonts often lack bold flags; require size, position and nearby prose."""
    for block in blocks:
        lines = block.get('lines', [])
        if not lines:
            continue
        block['heading_level'] = None
        label = ' '.join(text(line) for line in lines).strip()
        if not 5 <= len(label) <= 140 or not 1 <= len(label.split()) <= 18 or len(lines) > 3:
            continue
        if re.match(r'^(?:by\b|fig(?:ure)?\.?\s*\d|table\s*\d|\(?continued\b)', label, re.I):
            continue
        if block['bbox'][1] < height * .025 or block['bbox'][3] > height * .95:
            continue
        if sum(c.isalpha() for c in label) < len(label) * .55:
            continue
        spans = [s for line in lines for s in line['spans'] if s['text'].strip()]
        count = sum(len(s['text']) for s in spans)
        size = sum(s['size'] * len(s['text']) for s in spans) / max(1, count)
        if size < body_size * 1.65:
            continue
        for other in blocks:
            if other is block or not other.get('lines'):
                continue
            overlap = min(block['bbox'][2], other['bbox'][2]) - max(block['bbox'][0], other['bbox'][0])
            if (0 <= other['bbox'][1] - block['bbox'][3] <= size * 4
                    and overlap > 20 and sum(len(text(line)) for line in other['lines']) > 70):
                block['heading_level'] = 2
                block['heading_method'] = 'scan_relative_size_with_adjacent_prose'
                break
    candidates = sorted([b for b in blocks if b.get('heading_level')], key=lambda b: b['bbox'][1])
    consumed = set()
    for first, second in zip(candidates, candidates[1:]):
        if id(first) in consumed:
            continue
        a, b = first['bbox'], second['bbox']
        overlap = min(a[2], b[2]) - max(a[0], b[0])
        if (first.get('reader_column_span') == second.get('reader_column_span')
                and -.2 * (a[3] - a[1]) <= b[1] - a[3] <= .5 * (a[3] - a[1])
                and overlap > .5 * min(a[2] - a[0], b[2] - b[0])
                and len(' '.join(text(l) for l in first['lines'] + second['lines'])) < 160):
            first['lines'] = first['lines'] + second['lines']
            first['bbox'] = bounds([first, second])
            consumed.add(id(second))
    blocks[:] = [b for b in blocks if id(b) not in consumed]
