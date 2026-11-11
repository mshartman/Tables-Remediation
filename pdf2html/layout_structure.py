"""Conservative document-wide heading evidence for the geometry layout."""
from collections import Counter
import re

from pdf_reader_extract import font_has_style


def plain(block):
    return ' '.join(''.join(s['text'] for s in line['spans']) for line in block.get('lines', [])).strip()


def split_abstract(blocks):
    result=[]
    for block in blocks:
        lines=block.get('lines', [])
        if len(lines)>1 and re.fullmatch(r'abstract', ''.join(s['text'] for s in lines[0]['spans']).strip(), re.I):
            for group in (lines[:1],lines[1:]):
                result.append({**block,'lines':group,'bbox':(
                    min(l['bbox'][0] for l in group),min(l['bbox'][1] for l in group),
                    max(l['bbox'][2] for l in group),max(l['bbox'][3] for l in group))})
        else:result.append(block)
    return result


def split_leading_headings(pages, heights, body_size):
    """Separate a recurring short heading style from prose in the same block."""
    candidates = {}
    families = Counter()
    for pi, (blocks, height) in enumerate(zip(pages, heights)):
        for bi, block in enumerate(blocks):
            lines = block.get('lines', [])
            if len(lines) < 3 or block['bbox'][1] < height * .08:
                continue
            def style(line):
                spans = [s for s in line['spans'] if s['text'].strip()]
                keys = {(s['font'], round(s['size'], 1)) for s in spans}
                return next(iter(keys)) if len(keys) == 1 else None
            first = style(lines[0])
            if first is None or not body_size * 1.08 <= first[1] <= body_size * 1.6:
                continue
            cut = 1
            while cut < len(lines) and style(lines[cut]) == first:
                cut += 1
            if cut > 3 or len(lines) - cut < 2:
                continue
            head = plain({'lines': lines[:cut]})
            tail = plain({'lines': lines[cut:]})
            spans = [s for ln in lines[cut:] for s in ln['spans'] if s['text'].strip()]
            sizes = Counter()
            fonts = Counter()
            for span in spans:
                sizes[round(span['size'], 1)] += len(span['text'])
                fonts[span['font']] += len(span['text'])
            if not sizes:
                continue
            tail_size = sizes.most_common(1)[0][0]
            # A whole-line style change is required; inline emphasis is not enough.
            if (not 3 <= len(head) <= 140 or len(tail) < 80
                    or not head[0].isupper() or head.endswith(('.', ';', ',', ':'))
                    or re.match(r'^(fig(?:ure)?\.?|table|scheme)\s*\d', head, re.I)
                    or first[0] == fonts.most_common(1)[0][0]
                    or not body_size * .9 <= tail_size <= body_size * 1.05
                    or first[1] < tail_size * 1.08):
                continue
            candidates[(pi, bi)] = (cut, first)
            families[first] += 1
    result = []
    for pi, blocks in enumerate(pages):
        output = []
        for bi, block in enumerate(blocks):
            candidate = candidates.get((pi, bi))
            if candidate is None or families[candidate[1]] < 2:
                output.append(block)
                continue
            cut, _ = candidate
            for index, group in enumerate((block['lines'][:cut], block['lines'][cut:])):
                part = {**block, 'lines': group, 'bbox': (
                    min(ln['bbox'][0] for ln in group), min(ln['bbox'][1] for ln in group),
                    max(ln['bbox'][2] for ln in group), max(ln['bbox'][3] for ln in group))}
                part['heading_level'] = None
                if index == 0:
                    part['layout_heading_evidence'] = 'recurring_leading_style_change'
                output.append(part)
        result.append(output)
    return result


def refine_headings(pages, heights, body_size):
    def margin(block, height):
        return block['bbox'][1]<height*.08 or block['bbox'][3]>height*.94
    def key(block):return re.sub(r'\d+','#',plain(block).casefold())
    counts=Counter(key(b) for blocks,height in zip(pages,heights) for b in blocks if b.get('lines') and margin(b,height))
    def signature(block):
        spans=[s for l in block['lines'] for s in l['spans'] if s['text'].strip()]
        dominant=max(spans,key=lambda s:len(s['text']))
        return dominant['font'],round(dominant['size'],1)
    anchors=Counter(signature(b) for blocks,height in zip(pages,heights) for b in blocks
                    if b.get('heading_level') and not margin(b,height) and len(plain(b))<140)
    italic_styles=Counter(signature(b) for blocks,height in zip(pages,heights) for b in blocks
                          if b.get('lines') and 3<len(plain(b))<100 and not margin(b,height)
                          and signature(b)[1]>=body_size*1.12
                          and font_has_style(max((s for l in b['lines'] for s in l['spans']),key=lambda s:len(s['text'])),'italic'))
    bold_leading = {signature(b) for blocks in pages for b in blocks
                    if b.get('layout_heading_evidence') and font_has_style(
                        max((s for ln in b['lines'] for s in ln['spans']), key=lambda s:len(s['text'])), 'bold')}
    for pi,(blocks,height) in enumerate(zip(pages,heights)):
        starts=[b['bbox'][1] for b in blocks if re.match(r'^(abstract|introduction)\b',plain(b),re.I)]
        for b in blocks:
            if not b.get('lines'):continue
            if margin(b,height) and counts[key(b)]>=2:
                b['layout_running_header']=True;b['heading_level']=None;continue
            text=plain(b)
            if pi==0 and starts and b['bbox'][1]<min(starts):continue
            if not 3<=len(text)<=140 or len(b['lines'])>3 or text.endswith(('.', ';', ',')):
                continue
            if re.match(r'^(fig(?:ure)?\.?|table|scheme)\s*\d',text,re.I):continue
            nearby=any(other is not b and len(plain(other))>60
                       and 0<=other['bbox'][1]-b['bbox'][3]<=body_size*5
                       and abs(other['bbox'][0]-b['bbox'][0])<=body_size*4 for other in blocks if other.get('lines'))
            style=signature(b)
            if nearby and b.get('layout_heading_evidence'):
                subordinate = style not in bold_leading and any(s[1] >= style[1] for s in bold_leading)
                b['heading_level'] = 3 if subordinate else 2
            elif nearby and anchors[style]>=2 and style[1]>=body_size*1.08:
                b['heading_level']=2
            elif nearby and italic_styles[style]>=2:
                b['heading_level']=3
    # Larger standalone parents can be followed by a subheading rather than prose.
    # Require the same parent style at two such transitions before propagating it.
    parent_candidates = []
    parent_eligible = set()
    for pi, (blocks, height) in enumerate(zip(pages, heights)):
        starts = [b['bbox'][1] for b in blocks if re.match(r'^(abstract|introduction)\b', plain(b), re.I)]
        for b in blocks:
            text = plain(b)
            if (not b.get('lines') or margin(b, height) or b.get('layout_running_header')
                    or len(b['lines']) > 3 or not 3 <= len(text) <= 100
                    or text.endswith(('.', ';', ',', ':'))
                    or re.match(r'^(fig(?:ure)?\.?|table|scheme)\s*\d', text, re.I)
                    or (pi == 0 and starts and b['bbox'][1] < min(starts))):
                continue
            style = signature(b)
            if not body_size * 1.08 <= style[1] <= body_size * 1.6:
                continue
            parent_eligible.add(id(b))
            following = [other for other in blocks if other is not b and other.get('lines')
                         and 0 <= other['bbox'][1] - b['bbox'][3] <= body_size * 4
                         and abs(other['bbox'][0] - b['bbox'][0]) <= body_size * 2]
            child = min(following, key=lambda other: other['bbox'][1], default=None)
            if (child and child.get('layout_heading_evidence') and child.get('heading_level')
                    and style[1] >= signature(child)[1] * 1.1):
                parent_candidates.append((b, style, signature(child)))
    families = Counter(style for _, style, _ in parent_candidates)
    parents = {style for style, count in families.items() if count >= 2}
    children = {child for _, style, child in parent_candidates if style in parents}
    for blocks, height in zip(pages, heights):
        for b in blocks:
            if not b.get('lines') or not plain(b) or margin(b, height) or b.get('layout_running_header'):
                continue
            style = signature(b)
            if style in children and b.get('layout_heading_evidence') and b.get('heading_level'):
                b['heading_level'] = 3
            elif (id(b) in parent_eligible and style in parents and len(b['lines']) <= 3 and 3 <= len(plain(b)) <= 100
                    and not plain(b).endswith(('.', ';', ',', ':'))
                    and any(other is not b and other.get('lines')
                            and (len(plain(other)) > 60 or other.get('heading_level'))
                            and 0 <= other['bbox'][1] - b['bbox'][3] <= body_size * 5
                            and abs(other['bbox'][0] - b['bbox'][0]) <= body_size * 2 for other in blocks)):
                b['heading_level'] = 2
                b['layout_heading_evidence'] = 'recurring_larger_parent_style'
