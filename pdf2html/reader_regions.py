"""Build semantic reading regions without changing source text or sequence."""
from bs4 import Tag
from collections import Counter
import re


def normalize_section_headings(article):
    """Demote recurring page-leading furniture; repair evidenced reversed numbering."""
    pages = article.select('.page')
    leading = []
    for page in pages:
        blocks = page.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'table', 'figure'])
        if blocks and blocks[0].name in {'h1', 'h2', 'h3', 'h4'}:
            leading.append(blocks[0])
    key = lambda node: ' '.join(node.get_text(' ', strip=True).split()).casefold()
    counts = Counter(key(node) for node in leading)
    # Publishers may put an update badge before the first-page article title.
    if pages:
        for node in pages[0].find_all(['p', 'h1', 'h2', 'h3', 'h4', 'table', 'figure'])[:3]:
            if node.name.startswith('h') and counts[key(node)] >= 3 and not any(node is item for item in leading):
                leading.append(node)
    for node in leading:
        if counts[key(node)] >= 3:
            node.name = 'p'
            node['data-reader-running-header'] = ''
            node.attrs.pop('data-reader-heading', None)
    candidates = []
    for heading in article.select('h2'):
        if heading.has_attr('data-reader-number-normalized') or heading.find(['a', 'sup', 'sub']):
            continue
        label = heading.get_text(' ', strip=True)
        match = re.fullmatch(r'([A-Za-z][A-Za-z ,:&/-]{2,90})\s+([1-9]\d?)', label)
        if match:
            candidates.append((heading, match.group(1), int(match.group(2))))
    # A lone trailing number may be a year, part number, or meaningful title text.
    if len(candidates) >= 3 and [n for _, _, n in candidates] == list(range(1, len(candidates) + 1)):
        for heading, label, number in candidates:
            heading.clear()
            heading.append(f'{number} {label}')
            heading['data-reader-number-normalized'] = ''


def group_regions(soup, article):
    for region in list(article.select('[data-reader-region]')):
        region.unwrap()
    used = {tag['id'] for tag in soup.select('[id]')}
    headings = []
    for page in article.select('.page'):
        stack = []
        for node in list(page.contents):
            if not isinstance(node, Tag):
                if stack:
                    stack[-1][1].append(node.extract())
                continue
            if node.name in {'h2', 'h3', 'h4', 'h5', 'h6'}:
                level = int(node.name[1])
                while stack and stack[-1][0] >= level:
                    stack.pop()
                identifier = node.get('id')
                if not identifier:
                    identifier = f'reader-section-{len(headings)+1}'
                    while identifier in used:
                        identifier += '-heading'
                    node['id'] = identifier
                    used.add(identifier)
                node['tabindex'] = '-1'
                node['data-reader-heading'] = ''
                region = soup.new_tag('section', attrs={
                    'data-reader-region': '', 'aria-labelledby': identifier})
                if stack:
                    stack[-1][1].append(region)
                else:
                    node.insert_before(region)
                region.append(node.extract())
                stack.append((level, region))
                headings.append(node)
            elif stack and 'reader-page' not in node.get('class', []):
                stack[-1][1].append(node.extract())
    return headings


def section_navigation(soup, headings):
    nav = soup.new_tag('nav', attrs={'class': 'reader-outline', 'aria-label': 'Article sections'})
    details = soup.new_tag('details')
    summary = soup.new_tag('summary')
    summary.string = 'Article sections'
    details.append(summary)
    listing = soup.new_tag('ul')
    for heading in headings:
        if heading.name != 'h2':
            continue
        item = soup.new_tag('li')
        link = soup.new_tag('a', href='#' + heading['id'], attrs={'class': 'reader-section-link'})
        link.string = heading.get_text(' ', strip=True)
        item.append(link)
        listing.append(item)
    details.append(listing)
    nav.append(details)
    return nav
