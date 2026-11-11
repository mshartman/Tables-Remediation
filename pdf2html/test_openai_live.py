"""Explicit one-image smoke test; never run as part of the unit-test suite."""
import getpass
import json
from pathlib import Path
from uuid import uuid4
from urllib.error import HTTPError
from bs4 import BeautifulSoup
from openai_descriptions import request_description
from reader_view import render_reader


def main():
    source = Path('local_runs/reader/5079f0c202424aafae27c35cde0864be/article.html')
    soup = BeautifulSoup(source.read_text(encoding='utf-8'), 'html.parser')
    images = [img for img in soup.select('figure img') if not img.find_parent('details')]
    img = max(images, key=lambda item: int(item.get('width', 0)))
    key = getpass.getpass('API key (not saved): ')
    try:
        description, usage = request_description(img['src'], '', key)
    except HTTPError as error:
        print(json.dumps({'status': 'failed', 'http_status': error.code}))
        return
    finally:
        key = None
    img['alt'] = description
    img['data-alt-status'] = 'ai-generated'
    folder = Path('local_runs/reader') / uuid4().hex
    folder.mkdir()
    target = folder / 'article.html'
    target.write_text(str(soup), encoding='utf-8')
    render_reader(target, soup.title.get_text())
    report = {'model': 'gpt-5-nano', 'calls': 1, 'generated': 1, 'usage': usage, 'description': description}
    (folder / 'image-description-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(dict(report, url='http://127.0.0.1:8001/article/' + folder.name)))


if __name__ == '__main__':
    main()
