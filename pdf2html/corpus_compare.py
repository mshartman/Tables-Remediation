"""Compare frozen development runs without presenting heuristic metrics as accuracy."""
import argparse
from collections import Counter
from html import escape
import json
import os
from pathlib import Path


def compare_runs(before,after):
    if before['status']!='complete' or after['status']!='complete':raise ValueError('Both baseline runs must be complete.')
    if before['manifest_sha256']!=after['manifest_sha256'] or before['limits']!=after['limits']:
        raise ValueError('The source manifest and run limits must match.')
    if before.get('measurement_version',1)!=after.get('measurement_version',1):
        raise ValueError('Measurement versions differ; remeasure both frozen runs consistently.')
    old={r['sha256']:r for r in before['results']};new={r['sha256']:r for r in after['results']}
    if set(old)!=set(new):raise ValueError('Both runs must contain the same source hashes.')
    cases=[]
    critical={'duplicate_ids','broken_fragment','broken_aria_reference','invalid_table_headers'}
    for sha,entry in new.items():
        prior=old[sha];a=prior.get('metrics',{});b=entry.get('metrics',{});flags=[]
        old_coverage=a.get('matched_source_token_fraction');new_coverage=b.get('matched_source_token_fraction')
        delta=round(new_coverage-old_coverage,4) if old_coverage is not None and new_coverage is not None else None
        if prior['status']=='converted' and entry['status']!='converted':flags.append('conversion_status_regression')
        if delta is not None and delta<-.02:flags.append('text_coverage_drop_requires_review')
        if a.get('rendered_pages') is not None and a.get('rendered_pages')!=b.get('rendered_pages'):flags.append('rendered_page_count_changed')
        previous=Counter({f['code']:f['count'] for f in a.get('static_findings',[]) if f['code'] in critical})
        current=Counter({f['code']:f['count'] for f in b.get('static_findings',[]) if f['code'] in critical})
        if current-previous:flags.append('new_critical_static_findings')
        cases.append({'sha256':sha,'case_id':entry['case_id'],'filename':entry['filename'],
                      'before_status':prior['status'],'after_status':entry['status'],
                      'before_coverage':old_coverage,'after_coverage':new_coverage,'coverage_delta':delta,
                      'headings_changed':a.get('headings')!=b.get('headings'),
                      'before_tables':a.get('tables'),'after_tables':b.get('tables'),
                      'regression_review_flags':flags,'manual_review':'unreviewed'})
    return {'scope':'Development comparison only. Token coverage is not OCR accuracy, reading-order accuracy or WCAG conformance.',
            'summary':{'cases':len(cases),'converted_pairs':sum(c['before_status']==c['after_status']=='converted' for c in cases),
                       'cases_with_coverage_increase_over_10_points':sum(c['coverage_delta'] is not None and c['coverage_delta']>.1 for c in cases),
                       'cases_flagged_for_regression_review':sum(bool(c['regression_review_flags']) for c in cases),
                       'holdout_documents_evaluated':0,'human_reviews_completed':0},'cases':cases}


def report(before_path,after_path,output):
    before_path,after_path,output=Path(before_path).resolve(),Path(after_path).resolve(),Path(output).resolve()
    if output.exists():raise ValueError('Use a new comparison directory.')
    before=json.loads(before_path.read_text());after=json.loads(after_path.read_text())
    result=compare_runs(before,after)
    output.mkdir(parents=True,exist_ok=False)
    (output/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    rows=[]
    for case in sorted(result['cases'],key=lambda c:(not bool(c['regression_review_flags']),-(c['coverage_delta'] or 0),c['case_id'])):
        def link(root,name):return os.path.relpath(root/'cases'/case['case_id']/name,output).replace('\\','/')
        old_root=Path(before.get('artifact_root',before_path.parent));new_root=Path(after.get('artifact_root',after_path.parent))
        links='<a href="'+link(old_root,'original.pdf')+'">Source PDF</a>'
        if case['before_status']=='converted':links+=' | <a href="'+link(old_root,'render/layout.html')+'">Before</a>'
        if case['after_status']=='converted':links+=' | <a href="'+link(new_root,'render/layout.html')+'">After</a>'
        def value(number):return 'Not measured' if number is None else f'{number:.1%}'
        rows.append('<tr><th scope="row">'+escape(case['filename'])+'</th><td>'+escape(case['after_status'])+'</td><td>'+
                    value(case['before_coverage'])+'</td><td>'+value(case['after_coverage'])+'</td><td>'+
                    escape(', '.join(case['regression_review_flags']) or 'No automated regression flags; unreviewed')+'</td><td>'+links+'</td></tr>')
    html='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>Development before-and-after review</title><style>body{background:#f3f5f7;color:#18202a;font:1rem/1.55 Georgia,serif;margin:0}main{max-width:90rem;margin:auto;padding:28px}
h1{color:#183d57}a{color:#124e76}a:focus-visible,[tabindex]:focus-visible{outline:3px solid #12639a;outline-offset:3px}.notice{background:#fff8e8;border-left:4px solid #8a5c00;padding:16px}
.grid{overflow:auto;background:white}table{width:100%;border-collapse:collapse}caption{text-align:left;padding:14px;font-weight:bold}th,td{border:1px solid #cbd5df;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}thead{background:#e8eef3}tbody th{font-weight:normal;min-width:12rem}td:last-child{min-width:12rem}</style></head>
<body><main><h1>Before-and-after development review</h1><div class="notice">These results are not accessibility or accuracy scores. Coverage measures whether source text-layer tokens appear in semantic HTML. The source OCR may itself be wrong. All cases still need human review; no holdout documents were evaluated.</div>'''
    html+='<p>'+escape(json.dumps(result['summary']))+'</p><p><a href="comparison.json">Comparison data</a></p>'
    html+='<div class="grid" role="region" aria-label="Case comparison" tabindex="0"><table><caption>Same source files and limits; source changes are rejected by the runner</caption><thead><tr>'
    html+=''.join('<th scope="col">'+name+'</th>' for name in ['Document','After status','Before text coverage','After text coverage','Review status','Files'])+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></main></body></html>'
    (output/'index.html').write_text(html,encoding='utf-8')
    print(json.dumps(result['summary']),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path);parser.add_argument('after',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();report(args.before,args.after,args.output)
