"""Capture evidence without changing the reader or converted articles."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from shutil import copy2

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DEST = ROOT / 'local_runs' / 'wcag-baseline-2026-10-18'
JOBS = {'965899': '3e540a35afd944a3a4134d61544d6620',
        '964971': 'aa3cc86216db4923a3fac6da835265cb'}


def inspect(html):
    soup = BeautifulSoup(html, 'html.parser')
    ids = Counter(t['id'] for t in soup.find_all(id=True))
    return {
        'title': soup.title.get_text() if soup.title else None,
        'lang': soup.html.get('lang') if soup.html else None,
        'pages': len(soup.select('.page')),
        'main_count': len(soup.select('main')),
        'headings': [{'level': h.name, 'text': h.get_text(' ', strip=True)} for h in soup.select('h1,h2,h3,h4,h5,h6')],
        'tables': len(soup.select('table')),
        'table_caption_paragraphs': [p.get_text(' ', strip=True)[:180] for p in soup.select('p') if p.get_text().startswith('Table ')],
        'images': [{'alt': i.get('alt'), 'status': i.get('data-alt-status')} for i in soup.select('img')],
        'duplicate_ids': [key for key, count in ids.items() if count > 1],
        'broken_fragments': [a['href'] for a in soup.select('a[href]') if a['href'].startswith('#') and a['href'][1:] not in ids],
    }


def contrast(a, b):
    def luminance(value):
        rgb = [int(value[i:i+2], 16) / 255 for i in (1, 3, 5)]
        rgb = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * w for c, w in zip(rgb, [.2126, .7152, .0722]))
    values = sorted([luminance(a), luminance(b)])
    return round((values[1] + .05) / (values[0] + .05), 2)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    evidence = {}
    for label, job in JOBS.items():
        source = ROOT / 'local_runs' / 'reader' / job / 'article.html'
        target = DEST / (label + '.html')
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise RuntimeError('Baseline already exists with different content; do not overwrite it.')
        copy2(source, target)
        evidence[label] = inspect(target.read_text(encoding='utf-8'))
        evidence[label]['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    evidence['contrast'] = {a + ' on ' + b: contrast(a,b) for a,b in [
        ('#192b3b','#ffffff'), ('#435c70','#f2f5f8'), ('#125d91','#ffffff'),
        ('#ffffff','#143d5a'), ('#a14b00','#ffffff')]}
    (DEST / 'evidence.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({label: {'pages': evidence[label]['pages'], 'images': len(evidence[label]['images']),
                              'tables': evidence[label]['tables'], 'table_mentions': len(evidence[label]['table_caption_paragraphs'])} for label in JOBS}))
    print(json.dumps(evidence['contrast']))


if __name__ == '__main__':
    main()
