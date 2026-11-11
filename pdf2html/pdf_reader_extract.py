"""Geometry-based local extraction; no model or cloud calls."""
import base64
from collections import Counter
from html import escape
from pathlib import Path
import re
import sys

# Project-local installation also works without changing system Python.
sys.path.insert(0, str(Path(__file__).parent / '.reader-deps'))
import pymupdf
from reader_tables import extract_tables, contains
from reader_frontmatter import group_frontmatter
from reader_structure import record_page, record_render_blocks
from reader_cleanup import prepare_cleanup, join_lines
from reader_figures import figure_groups


def line_html(line):
    spans = [s for s in line['spans'] if s['text']]
    if not spans:
        return ''
    dominant = max(spans, key=lambda s: s['size'])
    baseline = dominant['origin'][1]
    result = []
    for span in spans:
        text = escape(span['text'])
        shift = span['origin'][1] - baseline
        if span['size'] < dominant['size'] * .9 and shift < -dominant['size'] * .12:
            text = '<sup>' + text.strip() + '</sup>' + (' ' if text.endswith(' ') else '')
        elif span['size'] < dominant['size'] * .9 and shift > dominant['size'] * .10:
            text = '<sub>' + text.strip() + '</sub>' + (' ' if text.endswith(' ') else '')
        result.append(text)
    return ''.join(result).strip()


def font_has_style(span, style):
    font = span.get('font', '').lower().split('+')[-1]
    if style in font:
        return True
    if re.search(r'\.' + ('b' if style == 'bold' else 'i') + r'$', font):
        return True
    # Publisher-prefixed Computer Modern fonts often omit style flags and names.
    patterns = {'bold': r'(?:^|adv)cm(?:bx|b)\d',
                'italic': r'(?:^|adv)cm(?:ti|sl)\d'}
    return bool(re.search(patterns[style], font))


def heading_level(block, body_size):
    """Require typographic evidence, not just a number at the start of text."""
    lines = block['lines']
    spans = [s for line in lines for s in line['spans'] if s['text'].strip()]
    plain = ' '.join(''.join(s['text'] for s in line['spans']) for line in lines).strip()
    if not spans or len(plain) > 180 or len(lines) > 3:
        return None
    if re.match(r'^(?:fig(?:ure)?\.?|table|scheme|plate)\s*\d|^\([a-z]\)', plain, re.I):
        return None
    if len(re.findall(r'[A-Za-z]', plain)) < 2:
        return None
    # Formula fonts and operators are not article section headings.
    if any('stix' in s.get('font', '').lower() for s in spans) or re.search(r'[=\u2190-\u21ff]', plain):
        return None
    count = sum(len(s['text'].strip()) for s in spans)
    def proportion(style):
        return sum(len(s['text'].strip()) for s in spans if font_has_style(s, style)) / count
    bold, italic = proportion('bold'), proportion('italic')
    numbered = re.match(r'^(\d+(?:\.\d+)*)(?:\.)?\s+[A-Za-z]', plain)
    if numbered and (bold > .7 or italic > .7):
        number = numbered.group(1)
        return min(6, 2 + (0 if number.endswith('.0') else number.count('.')))
    if bold > .8 and not plain.endswith(('.', ';', ',')):
        return 2
    # Letter-spaced abstract labels often have no bold font.
    if re.sub(r'\s+', '', plain).lower() == 'abstract':
        return 2
    return None


def contextual_headings(pages):
    """Use prose geometry and document-wide typography, not boldness alone."""
    sizes = Counter()
    for blocks in pages:
        for b in blocks:
            spans = [s for l in b.get('lines', []) for s in l['spans'] if s['text'].strip()]
            if sum(len(s['text']) for s in spans) > 200:
                for s in spans:
                    sizes[round(s['size'], 1)] += len(s['text'])
    body_size = sizes.most_common(1)[0][0] if sizes else 10
    for page_index, blocks in enumerate(pages):
        text_blocks = [b for b in blocks if 'lines' in b]
        def text(b):
            return ' '.join(''.join(s['text'] for s in l['spans']) for l in b['lines']).strip()
        starts = [b['bbox'][1] for b in text_blocks if re.match(r'^(?:abstract\b|1(?:\.0)?\.?\s+[A-Za-z])', text(b), re.I)]
        for b in text_blocks:
            b['heading_level'] = None
            plain = text(b)
            level = heading_level(b, body_size)
            spans = [s for l in b['lines'] for s in l['spans'] if s['text'].strip()]
            if not spans:
                continue
            size = sum(s['size'] * len(s['text']) for s in spans) / sum(len(s['text']) for s in spans)
            # Front matter above Abstract/Introduction is not a section heading.
            if page_index == 0 and starts and b['bbox'][1] < min(starts):
                continue
            # Text overlaid on chart images is not part of the section hierarchy.
            text_area = (max(x['bbox'][2] for x in text_blocks) - min(x['bbox'][0] for x in text_blocks)) * (max(x['bbox'][3] for x in text_blocks) - min(x['bbox'][1] for x in text_blocks))
            if any(img.get('image') and contains(img['bbox'], b['bbox'])
                   and (img['bbox'][2] - img['bbox'][0]) * (img['bbox'][3] - img['bbox'][1]) < text_area * .6
                   for img in blocks):
                continue
            following = [other for other in text_blocks if other is not b
                         and 0 <= other['bbox'][1] - b['bbox'][3] <= body_size * 4
                         and abs(other['bbox'][0] - b['bbox'][0]) <= body_size * 2
                         and (len(text(other)) > 100 or re.match(r'^\d+\.\d+\.?\s+[A-Za-z]', text(other)))]
            if size < body_size * .9:
                continue
            # Unknown font names can qualify through numbering and adjacent prose.
            if level is None and following and size >= body_size * 1.08 and len(plain) < 160:
                match = re.match(r'^(\d+(?:\.\d+)*)(?:\.)?\s+[A-Za-z]', plain)
                if match:
                    level = 2 if match[1].endswith('.0') else min(6, 2 + match[1].count('.'))
            if level and (following or re.sub(r'\s+', '', plain).lower().rstrip(':') in {'abstract', 'references', 'acknowledgments', 'acknowledgement'}):
                b['heading_level'] = level
    recover_heading_families(pages, body_size)
    return body_size


def recover_heading_families(pages, body_size):
    """Recover recurring subsection typography beneath recognized parents."""
    def plain(b):
        return ' '.join(''.join(s['text'] for s in l['spans']) for l in b.get('lines', [])).strip()
    prose_fonts = Counter()
    recognized = set()
    candidates = []
    bibliography = False
    for blocks in pages:
        for b in blocks:
            text = plain(b)
            spans = [s for l in b.get('lines', []) for s in l['spans'] if s['text'].strip()]
            if len(text) > 200:
                for s in spans:
                    prose_fonts[s['font']] += len(s['text'])
            if re.fullmatch(r'(?:\d+\.?\s+)?(?:References|Bibliography|Works cited)', text, re.I):
                bibliography = True
            number = re.match(r'^(\d+(?:\.\d+)*)(?:\.)?\s+[A-Za-z]', text)
            if b.get('heading_level') and number:
                recognized.add(number[1])
            if bibliography or not number or '.' not in number[1] or not spans:
                continue
            if len(text) > 180 or len(b['lines']) > 3 or text.endswith(('.', ';')) or re.search(r'[=\u2190-\u21ff]', text):
                continue
            dominant = max(spans, key=lambda s: len(s['text']))
            if not .9 * body_size <= dominant['size'] <= 1.4 * body_size:
                continue
            if any(img.get('image') and contains(img['bbox'], b['bbox'])
                   and img['bbox'][2] - img['bbox'][0] < 400 for img in blocks):
                continue
            following = any(other is not b and len(plain(other)) > 100
                            and 0 <= other['bbox'][1] - b['bbox'][3] <= body_size * 4
                            and abs(other['bbox'][0] - b['bbox'][0]) <= body_size * 4
                            for other in blocks if 'lines' in other)
            if following:
                candidates.append((b, number[1], (dominant['font'], round(dominant['size'], 1))))
    common_prose = prose_fonts.most_common(1)[0][0] if prose_fonts else None
    families = Counter((number.count('.'), style) for _, number, style in candidates)
    counts = Counter(number for _, number, _ in candidates)
    siblings = {}
    for _, number, style in candidates:
        parent, suffix = number.rsplit('.', 1)
        siblings.setdefault((parent, style), set()).add(int(suffix))
    for b, number, style in sorted(candidates, key=lambda item: item[1].count('.')):
        if counts[number] != 1 or families[(number.count('.'), style)] < 2:
            continue
        parent = number.rsplit('.', 1)[0]
        if parent not in recognized:
            continue
        if style[0] == common_prose:
            sequence = sorted(siblings[(parent, style)])
            if number.count('.') < 2 or len(sequence) < 2 or sequence != list(range(1, len(sequence) + 1)):
                continue
        b['heading_level'] = min(6, 2 + number.count('.'))
        b['heading_method'] = 'numbered_family_with_parent_and_adjacent_prose'
        recognized.add(number)


def block_html(block, body_size):
    lines = block['lines']
    text = ''
    for line in lines:
        fragment = line_html(line)
        if text.endswith('\u00ad'):
            text = text[:-1] + fragment
        else:
            text = join_lines(text, fragment, block.get('reader_attested_words', set()))
    text = text.replace('\u00ad', '')
    level = block['heading_level'] if 'heading_level' in block else heading_level(block, body_size)
    if level and re.sub(r'\s+', '', text).lower() == 'abstract':
        text = 'Abstract'
    tag = f'h{level}' if level else 'p'
    return f'<{tag}>{text}</{tag}>'


def ordered(blocks, width):
    """Full-width blocks separate bands; read each band column by column."""
    wide = sorted([b for b in blocks if b['bbox'][2] - b['bbox'][0] > width * .62], key=lambda b: b['bbox'][1])
    remaining = [b for b in blocks if b not in wide]
    output = []
    def columns(items):
        left = [b for b in items if b['bbox'][0] < width * .49]
        right = [b for b in items if b['bbox'][0] >= width * .49]
        return sorted(left, key=lambda b: b['bbox'][1]) + sorted(right, key=lambda b: b['bbox'][1])
    for barrier in wide:
        before = [b for b in remaining if b['bbox'][1] < barrier['bbox'][1]]
        output.extend(columns(before))
        remaining = [b for b in remaining if b not in before]
        output.append(barrier)
    return output + columns(remaining)


def is_duplicate_scan(block, blocks, page_rect):
    """Only collapse near-full-page images with substantial overlapping text."""
    if not block.get('image'):
        return False
    box = pymupdf.Rect(block['bbox']) & page_rect
    if box.width < page_rect.width * .9 or box.height < page_rect.height * .9:
        return False
    text = ''.join(s['text'] for b in blocks for line in b.get('lines', [])
                   if contains(block['bbox'], line['bbox']) for s in line['spans'])
    return len(text.strip()) >= 200


def image_element(block, number):
    encoded = base64.b64encode(block['image']).decode('ascii')
    width = max(1, round((block['bbox'][2] - block['bbox'][0]) * 96 / 72))
    return f'<img width="{width}" data-alt-status="missing" src="data:image/{escape(block["ext"])};base64,{encoded}" alt="Extracted image on page {number}; a descriptive alternative has not been supplied.">'


def extract_article(data, language='en', table_report=None, title=None, structure_report=None):
    sections = []
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        page_blocks = [page.get_text('dict')['blocks'] for page in doc]
        contextual_headings(page_blocks)
        if structure_report is not None:
            structure_report.update(schema_version=1, status='extracted',
                                    scope='Source geometry and heuristic render order; not verified semantics.',
                                    pages=[record_page(page, page_blocks[i], i + 1) for i, page in enumerate(doc)])
        retained, cleanup_changes = prepare_cleanup(page_blocks, [p.rect.height for p in doc])
        if structure_report is not None:
            structure_report['cleanup_changes'] = cleanup_changes
        for number, page in enumerate(doc, 1):
            blocks = [b for b in page_blocks[number - 1] if not b.get('reader_cleanup_role')]
            if number == 1 and title:
                blocks = group_frontmatter(blocks, title, page.rect.width, line_html)
            sizes = Counter()
            for b in blocks:
                for line in b.get('lines', []):
                    for span in line['spans']:
                        sizes[round(span['size'], 1)] += len(span['text'])
            body_size = sizes.most_common(1)[0][0] if sizes else 10
            tables, findings = extract_tables(page, blocks, line_html)
            if table_report is not None:
                table_report.extend(dict(item, page=number) for item in findings)
            kept = []
            for block in blocks:
                if block.get('table_caption_consumed'):
                    continue
                if 'lines' in block:
                    lines = [line for line in block['lines'] if not any(contains(t['bbox'], line['bbox']) for t in tables)]
                    if lines:
                        kept.append(dict(block, lines=lines))
                else:
                    kept.append(block)
            output = []
            scans = []
            if findings:
                output.append('<p class="table-review-notice">Table accessibility: this page contains detected tables or table captions that need human review. Unresolved table content remains in the extracted text and may not preserve cell relationships.</p>')
            render_blocks = ordered(kept + tables, page.rect.width)
            groups, consumed, figure_findings = figure_groups(render_blocks, page.rect.height)
            if structure_report is not None:
                record_render_blocks(structure_report['pages'][number - 1], render_blocks)
                structure_report['pages'][number - 1]['figure_associations'] = figure_findings
            for index, block in enumerate(render_blocks):
                if index in groups:
                    group = groups[index]
                    content = ''.join(image_element(render_blocks[n], number) if render_blocks[n].get('image')
                                      else block_html(render_blocks[n], body_size) for n in group[:-1])
                    caption_html = block_html(render_blocks[group[-1]], body_size)
                    caption = caption_html.split('>', 1)[1].rsplit('</', 1)[0]
                    output.append('<figure>' + content + '<figcaption>' + caption + '</figcaption></figure>')
                    continue
                if index in consumed:
                    continue
                if 'frontmatter_html' in block:
                    output.append(block['frontmatter_html'])
                elif 'table_html' in block:
                    output.append(block['table_html'])
                elif 'lines' in block:
                    output.append(block_html(block, body_size))
                elif block.get('image'):
                    image_html = '<figure>' + image_element(block, number) + '</figure>'
                    if is_duplicate_scan(block, blocks, page.rect):
                        scans.append(image_html)
                    else:
                        output.append(image_html)
            if scans:
                output.append(f'<details class="reader-source-scan"><summary>View original scanned page {number}</summary><p>This scan is retained for reference. Figures within it have not been separately extracted or described; the text above may contain OCR errors.</p>' + ''.join(scans) + '</details>')
            sections.append(f'<section class="page" id="page-{number}">' + (''.join(output) or '<p>No selectable content found on this page.</p>') + '</section>')
    metadata = ''
    if retained:
        metadata = '<details class="reader-publication-info"><summary>Publication information and repeated page details</summary>' + ''.join(block_html(b, 10) for b in retained) + '</details>'
    return '<html lang="' + escape(language, quote=True) + '"><body>' + metadata + ''.join(sections) + '</body></html>'
