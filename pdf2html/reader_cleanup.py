"""Conservative geometry-based cleanup; retain removed margin text for inspection."""
import re
from collections import defaultdict


def plain(block):
    return ' '.join(''.join(s['text'] for s in line['spans']) for line in block.get('lines', []))


def prepare_cleanup(pages, heights):
    occurrences = defaultdict(list)
    words = set()
    for index, blocks in enumerate(pages):
        for block in blocks:
            text = plain(block)
            words.update(w.lower() for w in re.findall(r'(?<![\w-])[A-Za-z]{4,}(?![\w-])', text))
            if text and len(text) < 200 and (block['bbox'][3] < heights[index] * .08 or block['bbox'][1] > heights[index] * .92):
                key = re.sub(r'\b\d+\s+of\s+\d+\b', '[page]', text).strip()
                occurrences[key].append((index, block))
    retained = []
    changes = []
    for key, items in occurrences.items():
        if len({i for i, _ in items}) < max(3, len(pages) // 2):
            continue
        retained.append(items[0][1])
        for index, block in items:
            block['reader_cleanup_role'] = 'repeated_margin'
            changes.append({'page': index + 1, 'action': 'consolidated_margin', 'text': plain(block), 'bbox': list(block['bbox'])})
    # Only move explicit publication metadata in a narrow first-page sidebar.
    first = pages[0] if pages else []
    prose = [b for b in first if len(plain(b)) > 250]
    if prose:
        body_left = min(b['bbox'][0] for b in prose)
        for b in first:
            if b.get('reader_cleanup_role') or b['bbox'][2] > body_left + 4:
                continue
            if re.match(r'^(?:Academic Editor:|Received:|Revised:|Accepted:|Published:|Copyright:|Licensee\b)', plain(b), re.I):
                b['reader_cleanup_role'] = 'publication_metadata'
                retained.append(b)
                changes.append({'page': 1, 'action': 'moved_metadata', 'text': plain(b), 'bbox': list(b['bbox'])})
    for blocks in pages:
        for b in blocks:
            b['reader_attested_words'] = words
    return retained, changes


def join_lines(text, fragment, words):
    left = re.search(r'([A-Za-z]{3,})-$', text)
    right = re.match(r'^([a-z]{2,})\b', fragment)
    if left and right and (left[1] + right[1]).lower() in words:
        return text[:-1] + fragment
    return text + (' ' if text else '') + fragment
