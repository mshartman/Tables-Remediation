"""Reconcile locally resolved cells with the article's converted HTML."""
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
import pdfplumber

from .continuation import carry_continuation_headers
from .reader import resolve_page


def words(tag):
    return Counter(tag.get_text(" ", strip=True).split())


def plain_block(tag):
    # Replacing a block must not discard links, destinations, images or semantics.
    return (isinstance(tag, Tag) and tag.name == "p" and not tag.attrs
            and all(child.name == "span" and not child.attrs for child in tag.find_all(True)))


def matching_runs(page, target):
    """Find sibling paragraphs containing exactly the table's words, in any order."""
    size = target.total()
    for first in page.find_all("p"):
        if first.find_parent(["table", "figure", "details", "nav"]) or not plain_block(first):
            continue
        run, counts = [], Counter()
        node = first
        while plain_block(node):
            block = words(node)
            if not block:
                break
            counts.update(block)
            if counts.total() > size or not counts <= target:
                break
            run.append(node)
            if counts == target:
                yield run
                break
            node = node.next_sibling
            while isinstance(node, NavigableString) and not node.strip():
                node = node.next_sibling


def insert_tables(page, fragments, soup):
    """Keep unrelated/ambiguous article content intact; never remove it by bbox alone."""
    used_ids = {node['id'] for node in soup.select('[id]')}
    for fragment in fragments:
        wrapper = BeautifulSoup(fragment, "html.parser").div
        # Avoid collisions with existing document anchors and generated relationships.
        prefix = "tr-"
        while any(prefix + node['id'] in used_ids for node in wrapper.select('[id]')):
            prefix += 'tr-'
        for node in [wrapper, *wrapper.find_all(True)]:
            if node.has_attr('id'):
                node['id'] = prefix + node['id']
                used_ids.add(node['id'])
            if node.has_attr('headers'):
                node['headers'] = [prefix + ref for ref in node['headers']]
            if node.has_attr('aria-labelledby'):
                node['aria-labelledby'] = prefix + node['aria-labelledby']

        cell_text = Counter()
        for cell in wrapper.table.find_all(['td', 'th']):
            cell_text.update(words(cell))
        if not cell_text:
            continue
        # Keep an existing semantic table when it already represents the same cells.
        if any([c.get_text(" ", strip=True).split() for c in table.select('th,td')]
               == [c.get_text(" ", strip=True).split() for c in wrapper.select('th,td')]
               and table.select_one('th[scope],td[headers]')
               for table in page.find_all('table')):
            continue
        runs = matching_runs(page, cell_text)
        first = next(runs, None)
        if first and next(runs, None) is None:
            first[0].insert_before(wrapper)
            for node in first:
                node.decompose()
        else:
            details = soup.new_tag('details')
            summary = soup.new_tag('summary')
            summary.string = 'Read reconstructed table: ' + wrapper.caption.get_text(strip=True)
            details.append(summary)
            details.append(wrapper)
            page.append(details)


def resolve_document(pdf_path, html_dir, *, single_file, page_range=None):
    """Apply to verified page locations in either output format of the page builder."""
    if page_range:
        return  # Numbering of a selected subset may differ from the source.
    html_dir = Path(html_dir)
    with pdfplumber.open(pdf_path) as document:
        if single_file:
            path = html_dir / 'remediated.html'
            soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
            pages = soup.select('div.page')
            if len(pages) != len(document.pages) or any(
                    page.get('id') != f'page-{i}' for i, page in enumerate(pages)):
                return
        changed = False
        for i, pdf_page in enumerate(document.pages):
            if not single_file:
                path = html_dir / f'page-{i}.html'
                if not path.is_file():
                    continue
                soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
            page = pages[i] if single_file else soup.body
            if page is None or page.has_attr('data-local-tables-resolved'):
                continue
            try:
                result = resolve_page(pdf_page)
            finally:
                pdf_page.close()
            if result.tables:
                insert_tables(page, result.tables, soup)
                page['data-local-tables-resolved'] = 'true'
                changed = True
                if not single_file:
                    path.write_text(str(soup), encoding='utf-8')
        if single_file and changed:
            carry_continuation_headers(soup)
            path.write_text(str(soup), encoding='utf-8')
