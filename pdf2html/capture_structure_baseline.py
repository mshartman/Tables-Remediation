"""Freeze local comparison evidence; never convert documents or call AI services."""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CASES = {
    '963688': ('899228aa7cb743fcb566e6ae67c7ef10', 'I:/Borrowing/963688.pdf', 'Equations, numbered hierarchy, figures, references and tables'),
    '967095': ('5079f0c202424aafae27c35cde0864be', 'I:/VZB/967095.pdf', 'Publisher front matter, author markers and heading families'),
    '966990': ('e0b7a40aaabf4e78a6f76b486de920a4', 'I:/Borrowing/966990.pdf', 'Scanned pages with an overlapping text layer'),
    '964894': ('e6f96677ca034d848e0c0458ba69f8c2', 'C:/Users/mshartman/Downloads/964894.pdf', 'Scientific notation and page navigation'),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(html):
    soup = BeautifulSoup(html, 'html.parser')
    ids = Counter(t['id'] for t in soup.select('[id]'))
    return {
        'pages': len(soup.select('.page')),
        'headings': [{'level': h.name, 'text': h.get_text(' ', strip=True)} for h in soup.select('h1,h2,h3,h4,h5,h6')],
        'possible_missed_subheadings': [p.get_text(' ', strip=True) for p in soup.select('p')
            if len(p.get_text()) < 180 and re.match(r'^\d+\.\d+(?:\.\d+)*\.?\s+[A-Za-z]', p.get_text(' ', strip=True))],
        'figures': len(soup.select('figure')), 'attached_captions': len(soup.select('figure > figcaption')),
        'images': len(soup.select('img')), 'image_statuses': dict(Counter(i.get('data-alt-status', 'unmarked') for i in soup.select('img'))),
        'source_scan_disclosures': len(soup.select('details.reader-source-scan')),
        'tables': len(soup.select('table')), 'mathml': len(soup.select('math')),
        'reference_targets': len(soup.select('[data-reader-reference]')),
        'citation_links': len(soup.select('.reader-citation')),
        'external_links': len(soup.select('a[href^="https://"],a[href^="http://"]')),
        'duplicate_ids': [key for key, count in ids.items() if count > 1],
        'broken_fragments': [a['href'] for a in soup.select('a[href^="#"]') if a['href'][1:] not in ids],
    }


def main():
    # Never overwrite a baseline; private PDFs and HTML stay beneath local_runs.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest = ROOT / 'local_runs' / ('structure-baseline-' + stamp)
    dest.mkdir(parents=True, exist_ok=False)
    report = {'created_utc': stamp, 'scope': 'Historical output baseline, not a fresh conversion or WCAG certification. Heuristic flags require review.', 'cases': {}}
    for label, (job, source, purpose) in CASES.items():
        folder = dest / label
        folder.mkdir()
        entry = {'job': job, 'source_path': source, 'purpose': purpose, 'files': {}}
        report['cases'][label] = entry
        paths = {'source.pdf': Path(source)}
        paths.update({name: ROOT / 'local_runs' / 'reader' / job / name for name in (
            'article.html', 'accessibility-report.json', 'table-extraction-report.json', 'image-description-report.json')})
        for name, path in paths.items():
            if not path.is_file():
                entry['files'][name] = {'status': 'unavailable'}
                continue
            copy2(path, folder / name)
            entry['files'][name] = {'status': 'captured', 'sha256': digest(folder / name)}
        if (folder / 'article.html').exists():
            entry['metrics'] = inspect((folder / 'article.html').read_text(encoding='utf-8'))
    report['confirmed_963688_findings'] = [
        'Page 1: publishing metadata interrupts Introduction prose.',
        'Page 4: explanatory prose interrupts Equation 5.',
        'Page 10: fragments of Equation 33 follow Equation 34.',
        'Pages 10 and 18: subsection labels remain paragraphs.',
        'Figure captions are separate paragraphs, not semantic figcaptions.',
        'Table report flags unresolved tables on pages 13, 17, 19 and 21.',
        'Generated image description is verbose and describes a small graphic rather than a substantive chart.',
    ]
    report['review_protocol'] = [
        'Reconvert the same source bytes after each change; do not overwrite these snapshots.',
        'Compare heading hierarchy and candidate missed headings against the source, not counts alone.',
        'Check reading order at column, sidebar, equation and page transitions.',
        'Compare all figure/caption associations; verify chart details and alt descriptions manually.',
        'Compare table values and cell relationships, preserving explicit unresolved warnings.',
        'Check external destinations and citation targets; exercise jump and return behavior with keyboard and screen reader.',
        'Preserve source text, scientific notation, images, existing alt text and page navigation.',
        'Treat fewer warnings as improvement only when source comparison confirms the underlying problem is fixed.',
    ]
    report['manual_review_status'] = 'Representative source pages reviewed for 963688; other cases are regression snapshots pending renewed source comparison. No new screen-reader testing.'
    report['code_sha256'] = {name: digest(ROOT / name) for name in (
        'pdf_reader_extract.py', 'reader_view.py', 'reader_links.py', 'reader_frontmatter.py', 'reader_tables.py', 'local_vision_descriptions.py')}
    (dest / 'baseline.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    lines = ['STRUCTURE BASELINE', report['scope'], '', report['manual_review_status'], '']
    for label, case in report['cases'].items():
        m = case.get('metrics', {})
        lines.append(f"{label}: pages={m.get('pages')}; headings={len(m.get('headings', []))}; possible missed subsections={len(m.get('possible_missed_subheadings', []))}; figures={m.get('figures')}; captions={m.get('attached_captions')}; tables={m.get('tables')}")
        lines.extend(f'  Missing: {name}' for name, info in case['files'].items() if info['status'] == 'unavailable')
    lines += ['', 'CONFIRMED CURRENT-DOCUMENT ISSUES'] + report['confirmed_963688_findings']
    lines += ['', 'REVIEW CHECKLIST'] + report['review_protocol']
    (dest / 'summary.txt').write_text('\n'.join(lines), encoding='utf-8')
    print(dest)
    print('\n'.join(lines[:12]))


if __name__ == '__main__':
    main()
