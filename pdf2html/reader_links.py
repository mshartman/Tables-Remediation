"""Conservative visible-URL and numbered-reference linking without AI calls."""
import re
from bs4 import NavigableString

ENTRY = re.compile(r'(?<!\S)(\d{1,3})\.\s+(?=[A-Z][A-Za-z\u00c0-\u024f\u2019\'-]+,)')
CITATION = re.compile(r'\[(\d{1,3}(?:\s*[-\u2013,;]\s*\d{1,3})*)\]')
URL = re.compile(r'https?://[^\s<>"\[\]]+|www\.[^\s<>"\[\]]+', re.I)


def add_document_links(soup, article):
    used = {tag['id'] for tag in soup.select('[id]')}
    references = {}
    active = False
    expected = 1
    for block in list(article.find_all(['h2', 'h3', 'h4', 'p'])):
        text = block.get_text(' ', strip=True)
        if re.fullmatch(r'(?:\d+\.?\s+)?(?:references|works cited|bibliography)', text, re.I):
            active = True
            continue
        if block.name != 'p':
            active = False
        if not active:
            continue
        if block.has_attr('data-reader-reference'):
            number = int(block['data-reader-reference'])
            references[number] = block['id']
            expected = number + 1
            continue
        # BDA often supplies one linked bibliography entry per paragraph. Keep
        # its inline markup instead of skipping every entry containing an anchor.
        first = re.match(r'^(\d{1,3})\.\s+\S', text)
        if first and int(first[1]) == expected and len(list(ENTRY.finditer(text))) <= 1:
            identifier = f'reader-ref-{expected}'
            while identifier in used:
                identifier += '-entry'
            used.add(identifier)
            block['id'] = identifier
            block['tabindex'] = '-1'
            block['data-reader-reference'] = str(expected)
            references[expected] = identifier
            expected += 1
            continue
        # Do not flatten existing links, emphasis or scientific markup.
        if block.find(True):
            continue
        text = block.get_text()
        matches = list(ENTRY.finditer(text))
        numbers = [int(m[1]) for m in matches]
        if not matches or text[:matches[0].start()].strip() or numbers != list(range(expected, expected + len(numbers))):
            continue
        for i, match in enumerate(matches):
            number = int(match[1])
            identifier = f'reader-ref-{number}'
            while identifier in used:
                identifier += '-entry'
            used.add(identifier)
            ref = soup.new_tag('p', id=identifier, attrs={
                'tabindex': '-1', 'data-reader-reference': str(number)})
            end = matches[i+1].start() if i+1 < len(matches) else len(text)
            ref.string = text[match.start():end].strip()
            block.insert_before(ref)
            references[number] = identifier
        expected += len(matches)
        block.decompose()

    for node in list(article.find_all(string=True)):
        if node.find_parent(['a', 'script', 'style', 'code', 'pre']) or not isinstance(node, NavigableString):
            continue
        text = str(node)
        candidates = [(m.start(), m.end(), 'url', m) for m in URL.finditer(text)]
        if not node.find_parent(attrs={'data-reader-reference': True}):
            candidates += [(m.start(), m.end(), 'citation', m) for m in CITATION.finditer(text)]
        cursor = 0
        pieces = []
        for start, end, kind, match in sorted(candidates, key=lambda item: item[0]):
            if start < cursor:
                continue
            label = match[0]
            if kind == 'url':
                label = label.rstrip('.,;:!?')
                while label.endswith(')') and label.count(')') > label.count('('):
                    label = label[:-1]
                href = label if label.lower().startswith(('http://', 'https://')) else 'https://' + label
                end = start + len(label)
                link = soup.new_tag('a', href=href)
            else:
                numbers = [int(n) for n in re.findall(r'\d+', match[1])]
                if not all(n in references for n in numbers):
                    continue
                # Keep printed ranges intact; identify precisely where the jump lands.
                link = soup.new_tag('a', href='#' + references[numbers[0]], attrs={
                    'class': 'reader-citation',
                    'aria-label': f'Citation {label}. Go to reference {numbers[0]}.'})
            pieces.append(NavigableString(text[cursor:start]))
            link.string = label
            pieces.append(link)
            cursor = end
        if pieces:
            pieces.append(NavigableString(text[cursor:]))
            node.replace_with(*pieces)
