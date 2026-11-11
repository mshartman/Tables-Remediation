"""Create local-only spacing/resize fixtures without altering baseline evidence."""
from pathlib import Path
from bs4 import BeautifulSoup
from reader_view import render_reader

ROOT = Path(__file__).parent / 'local_runs' / 'reader'
JOBS = ['42dc771964fd4f05a3ed1f7d1759c5ee', '06ebd6bd4ba44e569852f0826721b326']
OVERRIDE = ('html{font-size:200% !important}'
            '*{line-height:1.5 !important;letter-spacing:.12em !important;'
            'word-spacing:.16em !important}p{margin-bottom:2em !important}')


def main():
    for i, job in enumerate(JOBS, 1):
        path = ROOT / job / 'article.html'
        soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        render_reader(path, soup.title.get_text())
        soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        style = soup.new_tag('style')
        style.string = OVERRIDE
        soup.head.append(style)
        destination = ROOT / ('a4' + str(i) * 30)
        destination.mkdir(exist_ok=True)
        (destination / 'article.html').write_text(str(soup), encoding='utf-8')
        print('/article/' + destination.name)


if __name__ == '__main__':
    main()
