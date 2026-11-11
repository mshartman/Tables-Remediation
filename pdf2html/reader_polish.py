"""Evidence-based final HTML repairs. No model calls or invented scientific content."""
import re
from collections import Counter
from urllib.parse import urlsplit


def source_urls(pdf_path):
    if not pdf_path:
        return []
    from pypdf import PdfReader
    urls = set()
    for page in PdfReader(pdf_path).pages:
        for ref in page.get('/Annots', []):
            action = ref.get_object().get('/A', {})
            if hasattr(action, 'get_object'):
                action = action.get_object()
            uri = str(action.get('/URI', ''))
            if urlsplit(uri).scheme in {'http', 'https'}:
                urls.add(uri)
    return sorted(urls)


def repair_links(soup, article, urls):
    # Match the full printed URL against an actual PDF destination. Ignoring line
    # wrap whitespace/hyphens is allowed only when the complete destination matches.
    compact = lambda value: re.sub(r'[\s-]', '', value)
    for block in article.find_all('p'):
        for url in urls:
            nodes = list(block.find_all(string=True))
            text = ''.join(str(node) for node in nodes)
            positions = [i for i, char in enumerate(text) if not char.isspace() and char != '-']
            folded = ''.join(text[i] for i in positions)
            needle = compact(url)
            start = folded.find(needle)
            if start < 0 or folded.find(needle, start + 1) >= 0:
                continue
            begin, end = positions[start], positions[start + len(needle) - 1] + 1
            # Unwrap only links within this matched URL, retaining other markup.
            offset = 0
            for node in nodes:
                stop = offset + len(str(node))
                anchor = node.find_parent('a')
                if offset < end and stop > begin and anchor:
                    anchor.unwrap()
                offset = stop
            nodes = list(block.find_all(string=True))
            offset = 0
            inserted = False
            for node in nodes:
                value = str(node)
                stop = offset + len(value)
                if offset < end and stop > begin:
                    pieces = []
                    if offset < begin:
                        pieces.append(value[:begin-offset])
                    if not inserted:
                        link = soup.new_tag('a', href=url)
                        link.string = url
                        pieces.append(link)
                        inserted = True
                    if stop > end:
                        pieces.append(value[end-offset:])
                    node.replace_with(*pieces)
                offset = stop
    for link in article.select('a[href]'):
        href = link['href'].strip()
        parsed = urlsplit(href)
        if parsed.hostname in {'orcid.org', 'www.orcid.org'}:
            identifier = parsed.path.strip('/')
            if re.fullmatch(r'\d{4}-\d{4}-\d{4}-\d{3}[\dX]', identifier):
                link['href'] = 'https://orcid.org/' + identifier
                link.string = 'ORCID ' + identifier
                link.attrs.pop('aria-label', None)
        elif re.fullmatch(r'[^\s@/:]+@[^\s@/]+\.[^\s@/]+', href):
            link['href'] = 'mailto:' + href


def repair_tables(soup, article):
    used = {tag['id'] for tag in soup.select('[id]')}
    def unique(base):
        candidate = base
        while candidate in used:
            candidate += '-new'
        used.add(candidate)
        return candidate
    for index, table in enumerate(article.find_all('table'), 1):
        previous = table.find_previous_sibling()
        if previous and previous.name == 'div' and 'reader-table' in previous.get('class', []):
            previous = None
        if table.parent and 'reader-table' in table.parent.get('class', []):
            previous = table.parent.find_previous_sibling()
        if previous and previous.name == 'p' and re.match(r'^Table\s+\d+[.:\s]', previous.get_text(' ', strip=True), re.I):
            if table.caption:
                table.caption.decompose()
            caption = soup.new_tag('caption')
            for child in list(previous.contents):
                caption.append(child.extract())
            previous.decompose()
            table.insert(0, caption)
        elif table.caption:
            table.insert(0, table.caption.extract())
        if table.has_attr('data-reader-headers-repaired'):
            continue
        rows = [r for r in table.find_all('tr') if r.find_parent('table') is table]
        # Do not infer complex rowspans or nested tables. Preserve and flag them.
        if table.find('table') or any(int(c.get('rowspan', 1)) != 1 for c in table.find_all(['td', 'th'])):
            table['data-reader-table-review'] = 'complex spans'
            continue
        header_rows = []
        for row in rows:
            cells = row.find_all(['td', 'th'], recursive=False)
            if cells and any(c.name == 'th' for c in cells) and all(c.name == 'th' or not c.get_text(strip=True) for c in cells):
                header_rows.append(row)
            else:
                break
        if not header_rows or len(header_rows) == len(rows):
            continue
        widths = [sum(int(c.get('colspan', 1)) for c in r.find_all(['td', 'th'], recursive=False)) for r in rows]
        data_rows = rows[len(header_rows):]
        if len(set(widths)) != 1 or any(int(c.get('colspan', 1)) != 1 for r in data_rows for c in r.find_all(['td', 'th'], recursive=False)):
            table['data-reader-table-review'] = 'irregular columns'
            continue
        associations = [[] for _ in range(widths[0])]
        for row_number, row in enumerate(header_rows):
            column = 0
            for cell in row.find_all(['td', 'th'], recursive=False):
                span = int(cell.get('colspan', 1))
                if cell.name == 'th':
                    cell['id'] = unique(f'reader-table-{index}-head-{row_number}-{column}')
                    # Explicit headers encode grouped spans; scope is only used
                    # on individual columns to avoid asserting nonexistent colgroups.
                    cell.attrs.pop('scope', None)
                    if span == 1:
                        cell['scope'] = 'col'
                    cell.attrs.pop('headers', None)
                    for position in range(column, column+span):
                        associations[position].append(cell['id'])
                column += span
        thead = table.find('thead', recursive=False)
        if thead is None:
            thead = soup.new_tag('thead')
            if table.caption:
                table.caption.insert_after(thead)
            else:
                table.insert(0, thead)
        for row in header_rows:
            thead.append(row.extract())
        # Infer row labels only for a blank corner followed by a fully numeric matrix.
        row_labels = not associations[0] and all(
            (cells := r.find_all(['td', 'th'], recursive=False)) and cells[0].get_text(strip=True)
            and all(re.fullmatch(r'[\s\d.,+%\-\u2212]+', c.get_text()) for c in cells[1:])
            for r in data_rows)
        for row_number, row in enumerate(data_rows):
            cells = row.find_all(['td', 'th'], recursive=False)
            row_id = None
            if row_labels:
                cells[0].name = 'th'
                cells[0]['scope'] = 'row'
                row_id = unique(f'reader-table-{index}-row-{row_number}')
                cells[0]['id'] = row_id
            for column, cell in enumerate(cells):
                headers = associations[column] + ([row_id] if row_id and column else [])
                if headers:
                    cell['headers'] = headers
                else:
                    cell.attrs.pop('headers', None)
        table['data-reader-headers-repaired'] = ''


def polish_document(soup, article, urls=()):
    repair_links(soup, article, urls)
    repair_tables(soup, article)
    group_extracted_figures(soup, article)
    # Repair only wrapped words attested elsewhere as an intact word. This
    # deliberately leaves uncertain hyphens, names, URLs and formulas unchanged.
    vocabulary = set(re.findall(r'\b[a-zA-Z]{4,}\b', article.get_text(' ').lower()))
    for node in list(article.find_all(string=True)):
        if node.find_parent(['a', 'code', 'pre', 'script', 'style', 'math', 'svg']):
            continue
        value = re.sub(r'\b([A-Za-z]{2,})-\s+([a-z]{2,})\b',
                       lambda m: m[1] + m[2] if (m[1] + m[2]).lower() in vocabulary else m[0], str(node))
        if value != str(node):
            node.replace_with(value)


def source_symbol_findings(pdf_path, html):
    """Flag lost mathematical symbols, without inferring their original position."""
    if not pdf_path:
        return []
    from pypdf import PdfReader
    from bs4 import BeautifulSoup
    source = '\n'.join(page.extract_text() or '' for page in PdfReader(pdf_path).pages)
    output = BeautifulSoup(html, 'html.parser').get_text()
    missing = [symbol for symbol in '\u0394\u2248\u2264\u2265\u2260' if symbol in source and symbol not in output]
    return ([{'code': 'scientific_symbol_loss', 'count': len(missing),
              'message': 'Source mathematical symbols are absent from HTML; compare expressions with the PDF.',
              'symbols': missing}] if missing else [])


def group_extracted_figures(soup, article):
    for page in article.select('.page'):
        # BDA sometimes puts the same caption twice inside one chart OCR block.
        # Expose a verified repeated suffix as the caption; retain all OCR in details.
        for wrapper in list(page.find_all('div', recursive=False)):
            if not wrapper.find('img'):
                continue
            paragraphs = wrapper.find_all('p', recursive=False)
            if len(paragraphs) != 1:
                continue
            text = paragraphs[0].get_text(' ', strip=True)
            matches = list(re.finditer(r'Fig(?:ure|\.)?\s*\d+[.:\s]', text, re.I))
            if len(matches) < 2:
                continue
            suffix = text[matches[-1].start():].strip()
            if not 100 <= len(suffix) <= 2000 or suffix not in text[:matches[-1].start()]:
                continue
            last = wrapper
            sibling = last.find_next_sibling()
            while sibling and sibling.name == 'div' and sibling.find('img') and not sibling.get_text(strip=True):
                last = sibling
                sibling = last.find_next_sibling()
            caption = soup.new_tag('p')
            caption.string = suffix
            last.insert_after(caption)
        for caption in list(page.find_all('p', recursive=False)):
            if not re.match(r'^Fig(?:ure|\.)?\s*\d+[.:\s]', caption.get_text(' ', strip=True), re.I):
                continue
            wrappers = []
            previous = caption.find_previous_sibling()
            while previous and previous.name == 'div' and previous.find('img') and not previous.find(['table', 'h2', 'h3']):
                wrappers.insert(0, previous)
                previous = previous.find_previous_sibling()
            if not wrappers:
                continue
            figure = soup.new_tag('figure')
            wrappers[0].insert_before(figure)
            extracted = []
            for wrapper in wrappers:
                for image in list(wrapper.find_all('img')):
                    figure.append(image.extract())
                    if image.get('alt', '').strip().upper() in {'CHART', 'FIGURE', 'IMAGE'}:
                        image['alt'] = 'Figure panel; a detailed description has not been supplied.'
                        image['data-alt-status'] = 'missing'
                if wrapper.get_text(strip=True):
                    extracted.append(wrapper.extract())
                else:
                    wrapper.decompose()
            caption.name = 'figcaption'
            figure.append(caption.extract())
            if extracted:
                details = soup.new_tag('details', attrs={'class': 'reader-figure-extraction'})
                summary = soup.new_tag('summary')
                summary.string = 'Extracted figure text (unverified reading order)'
                details.append(summary)
                for wrapper in extracted:
                    details.append(wrapper)
                figure.append(details)
