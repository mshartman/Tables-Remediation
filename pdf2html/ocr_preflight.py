"""Bounded, local re-OCR of suspect pages. The uploaded PDF is never changed."""
from contextlib import contextmanager
import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata

from content_accessibility_utility_on_aws.pdf2html.utils.pdf_validation import validate_pdf_pages
from layout_raster import union_area


def text_quality(text):
    chars = [c for c in text if not c.isspace()]
    bad = sum(c == '\ufffd' or unicodedata.category(c) in {'Co', 'Cc', 'Cs'} for c in chars)
    words = re.findall(r'[^\W\d_]+', text, re.UNICODE)
    # Fragmentation is meaningful only for Latin prose, not CJK, formulae or tables.
    latin = [w for w in words if all('LATIN' in unicodedata.name(c, '') for c in w)]
    fragmented = len(latin) >= 30 and sum(len(w) == 1 for w in latin) / len(latin) > .30
    bad_fraction = bad / max(1, len(chars))
    broken_mapping = bool(re.search(r'\(cid:\d+\)', text)) or bad_fraction > .001
    return {'characters': len(chars), 'words': len(words), 'bad_character_fraction': round(bad_fraction, 4),
            'broken_mapping': broken_mapping, 'fragmented': fragmented}


def assess_page(page):
    text = page.get_text()
    metrics = text_quality(text)
    area = max(1, page.rect.width * page.rect.height)
    images = page.get_image_info()
    boxes = [(max(page.rect.x0, i['bbox'][0]), max(page.rect.y0, i['bbox'][1]),
              min(page.rect.x1, i['bbox'][2]), min(page.rect.y1, i['bbox'][3])) for i in images]
    image_fraction = union_area([b for b in boxes if b[2] > b[0] and b[3] > b[1]]) / area
    trace = page.get_texttrace()
    total = sum(len(s['chars']) for s in trace)
    hidden = sum(len(s['chars']) for s in trace if s['type'] == 3 or s.get('opacity', 1) < .01)
    if metrics['broken_mapping']:
        reason = 'damaged_character_mapping'
    elif (image_fraction >= .5 and (hidden or image_fraction >= .9)) or (total >= 30 and hidden >= total * .95):
        reason = 'unverified_scan_text' if metrics['characters'] else 'scan_without_text'
    elif not metrics['characters'] and (images or page.get_drawings()):
        reason = 'visible_content_without_text'
    elif metrics['fragmented']:
        reason = 'fragmented_text'
    else:
        reason = 'native_text_passed_checks' if metrics['characters'] else 'blank_page'
    return dict(metrics, reason=reason, selected=reason not in {'native_text_passed_checks', 'blank_page'})


def confidence_summary(path):
    values = []
    with Path(path).open(encoding='utf-8') as stream:
        for row in csv.DictReader(stream, delimiter='\t', quoting=csv.QUOTE_NONE):
            if row.get('level') != '5' or not row.get('text', '').strip():
                continue
            confidence = float(row['conf'])
            if not math.isfinite(confidence) or not 0 <= confidence <= 100:
                raise ValueError('Invalid OCR confidence.')
            values.append((confidence, len(row['text'].strip())))
    total = sum(size for _, size in values)
    return {'mean_confidence': round(sum(c * size for c, size in values) / max(1, total), 2),
            'low_confidence_fraction': round(sum(size for c, size in values if c < 60) / max(1, total), 4),
            'recognized_words': len(values)}


def acceptable_candidate(before, after, confidence):
    if not after['characters'] or after['broken_mapping'] or after['fragmented']:
        return False
    if confidence['mean_confidence'] < 85 or confidence['low_confidence_fraction'] > .15:
        return False
    if before['characters'] >= 100 and after['characters'] < max(30, before['characters'] * .5):
        return False
    # Do not replace an apparently readable layer with a short, confident fragment.
    if before['characters'] and not before['broken_mapping'] and not before['fragmented']:
        if after['characters'] < before['characters'] * .85:
            return False
    return True


def run_page_ocr(page, folder, executable, language, timeout):
    import pymupdf
    folder = Path(folder)
    # 300 dpi for normal article pages, with a hard per-page raster memory bound.
    dpi = min(300, max(1, int(72 * math.sqrt(24_000_000 / max(1, page.rect.get_area())))))
    image, base = folder / 'page.png', folder / 'recognized'
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    pix.save(image)
    del pix
    env = dict(os.environ, OMP_THREAD_LIMIT='1')
    subprocess.run([executable, str(image), str(base), '-l', language, '--dpi', str(dpi), 'pdf', 'tsv'],
                   check=True, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return base.with_suffix('.pdf'), confidence_summary(base.with_suffix('.tsv'))


def _integer_setting(name, default, minimum, maximum):
    value = os.getenv(name, str(default))
    if not re.fullmatch(r'[0-9]{1,4}', value) or not minimum <= int(value) <= maximum:
        raise ValueError('Invalid local OCR configuration.')
    return int(value)


def _prepare(source, scratch):
    import pymupdf
    mode = os.getenv('PDF_OCR_MODE', 'auto').strip().lower()
    if mode not in {'auto', 'off'}:
        raise ValueError('PDF_OCR_MODE must be auto or off.')
    report = {'version': 1, 'mode': mode, 'engine': 'tesseract', 'status': 'disabled', 'pages': [],
              'pages_selected': 0, 'pages_replaced': 0, 'pages_needing_review': 0,
              'quality_is_heuristic': True, 'external_api_calls': 0}
    if mode == 'off':
        return source, report
    budget = _integer_setting('PDF_OCR_BUDGET_SECONDS', 600, 30, 3600)
    per_page = _integer_setting('PDF_OCR_PAGE_TIMEOUT_SECONDS', 45, 5, 300)
    language = os.getenv('PDF_OCR_LANGUAGE', 'eng')
    if not re.fullmatch(r'[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+)*', language):
        raise ValueError('Invalid OCR language configuration.')
    report.update(language=language, page_timeout_seconds=per_page, budget_seconds=budget)
    executable = shutil.which('tesseract')
    started = time.monotonic()
    with pymupdf.open(source) as doc:
        links = [page.get_links() for page in doc]
        toc = doc.get_toc()
        for index in range(len(doc)):
            before = assess_page(doc[index])
            entry = dict(before, page=index + 1, outcome='retained')
            report['pages'].append(entry)
            if not before['selected']:
                continue
            report['pages_selected'] += 1
            remaining = budget - (time.monotonic() - started)
            if not executable or remaining <= 0:
                entry['outcome'] = 'engine_unavailable' if not executable else 'budget_exhausted'
                continue
            try:
                with tempfile.TemporaryDirectory(dir=scratch) as page_scratch:
                    candidate_path, confidence = run_page_ocr(doc[index], page_scratch, executable,
                                                              language, min(per_page, remaining))
                    with pymupdf.open(candidate_path) as candidate:
                        if len(candidate) != 1:
                            raise ValueError('OCR must return one page.')
                        after = text_quality(candidate[0].get_text())
                        entry.update(confidence=confidence, candidate_quality=after)
                        if not acceptable_candidate(before, after, confidence):
                            entry['outcome'] = 'candidate_rejected'
                            continue
                        rect = doc[index].rect
                        # Restore the source page's exact dimensions, independent of raster rounding.
                        with pymupdf.open() as replacement:
                            page = replacement.new_page(width=rect.width, height=rect.height)
                            page.show_pdf_page(rect, candidate, 0)
                            doc.delete_page(index)
                            doc.insert_pdf(replacement, start_at=index)
                        entry['outcome'] = 'replaced'
                        report['pages_replaced'] += 1
            except subprocess.TimeoutExpired:
                entry['outcome'] = 'page_timeout'
            except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError):
                # Never publish subprocess output, source text, or temporary file paths.
                entry['outcome'] = 'ocr_failed'
        report['pages_needing_review'] = sum(p['selected'] and p['outcome'] != 'replaced' for p in report['pages'])
        report['status'] = ('partial' if report['pages_needing_review'] else
                            'completed' if report['pages_replaced'] else 'not_needed')
        if not report['pages_replaced']:
            return source, report
        # Page replacement can invalidate incoming destinations. Rebuild links and bookmarks
        # only after all pages are in their final positions, preserving numbering.
        for index, original_links in enumerate(links):
            page = doc[index]
            for link in page.get_links():
                page.delete_link(link)
            for link in original_links:
                page.insert_link({k: v for k, v in link.items() if k not in {'xref', 'id'}})
        doc.set_toc(toc)
        target = Path(scratch) / 'ocr-working.pdf'
        doc.save(target, garbage=3, deflate=True)
    validate_pdf_pages(target)
    return target, report


@contextmanager
def prepare_pdf(source, report_path):
    """Keep OCR scratch out of result ZIPs and remove it even when conversion fails."""
    source, report_path = Path(source), Path(report_path)
    validate_pdf_pages(source)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pdf2html-ocr-') as scratch:
        path, report = _prepare(source, scratch)
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        yield Path(path), report


def add_ocr_notice(path, report):
    if not report.get('pages_selected'):
        return
    from bs4 import BeautifulSoup
    path = Path(path)
    soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
    main = soup.select_one('main')
    if not main:
        return
    for old in soup.select('[data-reader-ocr-summary]'):
        old.decompose()
    notice = soup.new_tag('aside', attrs={'data-reader-ocr-summary': '', 'aria-label': 'Text recognition review'})
    text = soup.new_tag('p')
    text.string = (f"Text recognition: {report['pages_replaced']} page(s) received a new OCR text layer. "
                   f"{report['pages_needing_review']} selected page(s) could not be safely replaced. "
                   'The original PDF was preserved. OCR may misread names, numbers, equations and tables; '
                   'compare important information with the source.')
    notice.append(text)
    main.insert(0, notice)
    # Older notices assumed no re-recognition. Avoid making a false claim now.
    for old in soup.select('[data-reader-ocr-warning]'):
        old.string = ('Scanned page: text may use an existing or newly recognized OCR layer. '
                      'See the text recognition notice and compare important details with the original PDF.')
    path.write_text(str(soup), encoding='utf-8')
