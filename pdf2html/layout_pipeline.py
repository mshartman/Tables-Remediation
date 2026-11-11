"""Feature-gated layout output for App Runner; cloud work stays in its caller."""
import json
import os
from pathlib import Path


def build_layout_preview(pdf_path, output_dir, legacy_html, conversion_result, title):
    mode = os.getenv('READER_LAYOUT_MODE', 'legacy').lower().strip()
    if mode == 'legacy':
        return None
    output_dir = Path(output_dir)
    status = {'requested_mode': mode, 'status': 'fallback', 'preview': 'legacy'}
    target = None
    try:
        if mode not in {'geometry', 'bda'}:
            raise ValueError('READER_LAYOUT_MODE must be legacy, geometry, or bda.')
        # Import only when selected; legacy mode needs no extra PDF libraries.
        from layout_compare import compare
        structure = conversion_result.get('bda_layout_structure_path')
        if mode == 'bda' and not structure:
            raise ValueError('No saved BDA structure is available. The original reader was retained.')
        target = compare(pdf_path, output_dir / 'layout',
                         bda_json=structure if mode == 'bda' else None,
                         geometry_only=mode == 'geometry', remediated=legacy_html, title=title)
        refresh_layout_review(target)
        status.update(status='completed', preview='layout', filename='layout/layout.html')
    except Exception as exc:
        target = None
        # Preserve the usable legacy result even if optional rendering fails.
        status.update(error_type=type(exc).__name__,
                      message='Layout could not be built; the original reader is available.')
    (output_dir / 'layout-status.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
    return target


def refresh_layout_review(path):
    """Refresh final image notices and audit after optional image descriptions."""
    from bs4 import BeautifulSoup
    from image_descriptions import annotate_image_gaps
    from reader_audit import audit_reader
    path = Path(path)
    soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
    main = soup.select_one('main')
    annotate_image_gaps(soup, main)
    text = '<!doctype html>\n' + str(soup)
    path.write_text(text, encoding='utf-8')
    report_path = path.with_name('layout-review.json')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report['static_audit'] = audit_reader(text,table_findings=report.get('local_table_findings'))
    for figure in report.get('figures', []):
        img = next((node for node in main.select('img[data-layout-figure-id]')
                    if node['data-layout-figure-id'] == str(figure['id'])), None)
        if img:
            figure.update(alt=img.get('alt', ''), needs_review=True,
                          alt_status=img.get('data-alt-status', 'inherited_unverified'))
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')


def preview_path(result):
    """One selection rule for both the upload page and asynchronous job API."""
    remediation = result.get('remediation_result') or {}
    return Path(result.get('layout_preview_path') or remediation.get('remediated_html_path')
                or result['conversion_result']['html_path'])
