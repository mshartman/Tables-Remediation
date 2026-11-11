"""Associate adjacent image panels with explicit captions; abstain on ambiguity."""
import re


def text(block):
    return ' '.join(''.join(s['text'] for s in line['spans']) for line in block.get('lines', []))


def figure_groups(blocks, page_height):
    groups = {}
    consumed = set()
    findings = []
    for index, caption in enumerate(blocks):
        label = text(caption).strip()
        if not re.match(r'^(?:Figure|Fig\.)\s+\d+[.:]\s', label, re.I):
            continue
        members = []
        j = index - 1
        while j >= 0 and (blocks[j].get('image') or re.fullmatch(r'\([a-z]\)', text(blocks[j]).strip())):
            members.insert(0, j)
            j -= 1
        images = [n for n in members if blocks[n].get('image')]
        valid = bool(images) and not any(n in consumed for n in members)
        for n in images:
            box = blocks[n]['bbox']; cb = caption['bbox']
            overlap = min(box[2], cb[2]) - max(box[0], cb[0])
            if box[3]-box[1] > page_height*.85 or not 0 <= cb[1]-box[3] <= 80 or overlap <= 0:
                valid = False
        if len(images) > 1:
            # A shared vertical band supports side-by-side panels, not stacked unrelated images.
            valid = valid and max(blocks[n]['bbox'][1] for n in images) < min(blocks[n]['bbox'][3] for n in images)
        finding = {'caption': label, 'caption_render_index': index,
                   'image_render_indexes': images if valid else [],
                   'status': 'associated_needs_review' if valid else 'unresolved',
                   'method': 'adjacency_and_geometry'}
        findings.append(finding)
        if valid:
            group = members + [index]
            groups[group[0]] = group
            consumed.update(group)
    return groups, consumed, findings
