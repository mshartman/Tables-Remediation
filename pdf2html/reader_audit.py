"""Limited static checks of final HTML, not a WCAG conformance certification."""
from collections import Counter
from urllib.parse import unquote
from bs4 import BeautifulSoup
from image_descriptions import needs_description


def audit_reader(html, table_findings=None):
    soup = BeautifulSoup(html, 'html.parser')
    findings = []

    def add(code, message, count=1):
        if count:
            findings.append({'code': code, 'message': message, 'count': count})

    if not soup.title or not soup.title.get_text(strip=True):
        add('missing_title', 'A meaningful document title is required.')
    if not soup.html or not soup.html.get('lang', '').strip():
        add('missing_language', 'Document language is missing.')
    if len(soup.select('main')) != 1:
        add('main_landmark', 'Expected one main landmark.')
    if len(soup.select('h1')) != 1:
        add('document_heading', 'Expected one top-level document heading.')
    ids = Counter(node['id'] for node in soup.select('[id]'))
    add('duplicate_ids', 'Duplicate IDs can break navigation and associations.', sum(n - 1 for n in ids.values()))
    for link in soup.select('a[href^="#"]'):
        target = unquote(link['href'][1:])
        if target and target not in ids:
            add('broken_fragment', 'An internal link has no matching destination.')
    for node in soup.select('[aria-labelledby], [aria-describedby]'):
        for attribute in ('aria-labelledby', 'aria-describedby'):
            if any(identifier not in ids for identifier in node.get(attribute, '').split()):
                add('broken_aria_reference', 'An accessible name or description references a missing ID.')
    images = soup.select('img')
    add('source_page_image_review', 'Page-image text layers and their figure alternatives require source review.',len(soup.select('[data-layout-scan-warning]')))
    add('source_margin_note_review', 'Rotated margin text was retained separately; verify its placement and meaning.',len(soup.select('.reader-margin-notes')))
    add('missing_image_alternative', 'Image alternatives are missing or placeholders.', sum(needs_description(img) for img in images))
    add('ai_alternative_review', 'AI-generated descriptions require human review.', sum(img.get('data-alt-status') == 'ai-generated' for img in images))
    for table in soup.select('table'):
        if not table.select('th'):
            add('table_without_headers', 'Table has no semantic header cells; verify its purpose and structure.')
        table_ids = Counter(n['id'] for n in table.select('th[id]'))
        for cell in table.select('[headers]'):
            refs = cell['headers']
            if isinstance(refs, str):
                refs = refs.split()
            if any(table_ids[ref] != 1 or ids[ref] != 1 for ref in refs):
                add('invalid_table_headers', 'A table cell references a missing or ambiguous header.')
        if table.has_attr('data-reader-table-review'):
            add('table_structure_review', 'Table structure requires source review; automatic repair abstained.')
    labels = {}
    for link in soup.select('a[href]'):
        name = link.get('aria-label') or link.get_text(' ', strip=True)
        if name:
            labels.setdefault(name, set()).add(link['href'])
    add('ambiguous_link_names', 'Identical link names have different destinations; review their context.',
        sum(len(destinations) > 1 for destinations in labels.values()))
    add('table_extraction_review', 'Detected table structures or unresolved captions require source comparison.', len(table_findings or []))
    return {
        'schema_version': 1,
        'status': 'issues_found' if findings else 'manual_review_required',
        'scope': 'Static checks only; not full WCAG 2.1 AA conformance testing.',
        'findings': findings,
        'manual_checks': [
            'Compare reading order, text, figures and table values with the source PDF.',
            'Check the meaning and accuracy of alt text, including empty decorative alternatives.',
            'Test screen-reader navigation, keyboard behavior, zoom, contrast and text spacing.',
            'Verify complex table header associations and detailed alternatives for scientific figures.',
        ],
    }
