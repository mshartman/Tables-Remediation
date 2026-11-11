"""Bounded offline development baseline. Never evaluates the holdout implicitly."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata

ROOT=Path(__file__).resolve().parent
ENGINE_FILES=('corpus_evaluate.py','layout_compare.py','layout_presentation.py','layout_structure.py',
              'layout_tables.py','layout_raster.py','layout_annotations.py','layout_pipeline.py','pdf_reader_extract.py','reader_tables.py',
              'reader_frontmatter.py','reader_structure.py','reader_cleanup.py','reader_figures.py',
              'reader_audit.py','image_descriptions.py','experimental_layout/__init__.py',
              'experimental_layout/iris_layout.py','requirements-layout.txt')


def selected_records(manifest):
    records=manifest['records'];sample=manifest['development_sample_sha256']
    dev={r['sha256'] for r in records if r['split']=='development'}
    hold={r['sha256'] for r in records if r['split']=='holdout'}
    if dev & hold or len(set(sample))!=len(sample) or not set(sample)<=dev:
        raise ValueError('Invalid split: holdout overlap, duplicate samples, or non-development selection.')
    selected=[]
    for sha in sample:
        entry=next(r for r in records if r['sha256']==sha)
        if entry.get('previously_reviewed'):raise ValueError('The fresh baseline sample contains a known reviewed case.')
        if Path(entry['filename']).name!=entry['filename']:raise ValueError('Unsafe source filename.')
        selected.append(entry)
    return selected


def tokens(text):
    text=unicodedata.normalize('NFKC',text).casefold()
    text=re.sub(r'(?<=\w)[-\u00ad]\s*\n\s*(?=\w)','',text)
    return Counter(re.findall(r'\w+',text))


def profile_pdf(doc,limit):
    indices=list(range(len(doc))) if len(doc)<=limit else sorted({0,min(1,len(doc)-1),len(doc)//2,len(doc)-1})
    result={'pages':len(doc),'profiled_pages':[i+1 for i in indices],
            'scope':'full_document' if len(doc)<=limit else 'sampled_pages_only',
            'scan_candidates':[],'column_candidates':[],'table_caption_pages':[],
            'math_candidates':[],'rotated_text_pages':[],'landscape_pages':[],'bitmap_images':0}
    result['large_page_candidates']=[]
    source_text=[];first_page=''
    for index in indices:
        page=doc[index];text=page.get_text();source_text.append(text)
        if index==0:first_page=text
        blocks=page.get_text('dict',flags=0)['blocks'];images=page.get_image_info()
        result['bitmap_images']+=len(images)
        image_area=sum(max(0,min(x['bbox'][2],page.rect.width)-max(0,x['bbox'][0]))*
                       max(0,min(x['bbox'][3],page.rect.height)-max(0,x['bbox'][1])) for x in images)
        if len(text.strip())<50 and image_area>page.rect.width*page.rect.height*.5:result['scan_candidates'].append(index+1)
        prose=[b for b in blocks if sum(len(s['text']) for line in b.get('lines',[]) for s in line['spans'])>80
               and b['bbox'][2]-b['bbox'][0]<page.rect.width*.6]
        if any(b['bbox'][0]<page.rect.width*.45 for b in prose) and any(b['bbox'][0]>page.rect.width*.45 for b in prose):
            result['column_candidates'].append(index+1)
        if re.search(r'(?mi)^\s*Table\s+\d+[.:]',text):result['table_caption_pages'].append(index+1)
        if re.search('[=\u2211\u222b\u2264\u2265]',text):result['math_candidates'].append(index+1)
        if any(abs(line.get('dir',(1,0))[0]-1)>.01 or abs(line.get('dir',(1,0))[1])>.01
               for b in blocks for line in b.get('lines',[])):result['rotated_text_pages'].append(index+1)
        if page.rect.width>page.rect.height:result['landscape_pages'].append(index+1)
        if page.rect.width*page.rect.height>4_000_000:result['large_page_candidates'].append(index+1)
    normalized=' '.join(unicodedata.normalize('NFKC',first_page).casefold().split())
    result['first_page_text_sha256']=hashlib.sha256(normalized.encode()).hexdigest() if len(normalized)>300 else None
    result['tags']=[label for key,label in (
        ('scan_candidates','scan candidate'),('column_candidates','multiple-column candidate'),
        ('table_caption_pages','table captions'),('math_candidates','math candidate'),
        ('rotated_text_pages','rotated text'),('landscape_pages','landscape')) if result[key]]
    if result['bitmap_images']:result['tags'].append('bitmap images')
    return result,'\n'.join(source_text)


def output_metrics(path,source_text):
    from bs4 import BeautifulSoup
    from layout_presentation import reading_label
    soup=BeautifulSoup(path.read_text(encoding='utf-8'),'html.parser')
    headings=[{'level':h.name,'text':reading_label(h)} for h in soup.select('main h1,main h2,main h3,main h4,main h5,main h6')]
    main=BeautifulSoup(str(soup.select_one('main')),'html.parser')
    for node in main.select('[aria-hidden="true"], [data-reader-image-notice], [data-layout-scan-warning], .reader-source-scan, script, style'):node.decompose()
    source=tokens(source_text);rendered=tokens(semantic_text(main))
    missing=source-rendered;total=sum(source.values())
    review=json.loads(path.with_name('layout-review.json').read_text(encoding='utf-8'))
    return {'rendered_pages':len(soup.select('main > .page')),'headings':headings,'tables':len(soup.select('table')),
            'table_findings':review.get('local_table_findings',[]),'fallback_pages':review.get('fallback_pages',[]),
            'composite_text_pages':review.get('composite_text_pages',[]),
            'margin_note_pages':[{'page':p['page'],'notes':len(p['notes'])} for p in review.get('margin_note_pages',[])],
            'static_findings':review['static_audit']['findings'],
            'source_token_count':total,'matched_source_token_fraction':round(1-sum(missing.values())/total,4) if total else None,
            'unmatched_source_token_count':sum(missing.values()),
            'coverage_caveat':'Heuristic only: running headers, figure text, hyphenation and transformations affect coverage. This is not accuracy or proof of content loss.'}


def semantic_text(node):
    """Separate blocks/cells, but do not split formulae at inline sub/sup tags."""
    from bs4 import NavigableString
    if isinstance(node,NavigableString):return str(node)
    boundary=getattr(node,'name',None) in {'p','div','section','aside','li','ul','ol','table','tr','td','th','caption',
                                         'h1','h2','h3','h4','h5','h6','br','summary','details'}
    text=''.join(semantic_text(child) for child in node.children)
    return '\n'+text+'\n' if boundary else text


def remeasure(baseline_path,output):
    """Measure frozen artifacts with the new text metric; never rerender them."""
    from layout_compare import pymupdf
    baseline_path=Path(baseline_path).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(baseline_path.parent):raise ValueError('Use a new measurement directory outside the frozen run.')
    run=json.loads(baseline_path.read_text(encoding='utf-8'))
    if run['status']!='complete':raise ValueError('Only complete baselines can be remeasured.')
    artifact_root=Path(run.get('artifact_root',baseline_path.parent))
    for result in run['results']:
        if result['status']!='converted':continue
        case=artifact_root/'cases'/result['case_id'];pdf=case/'original.pdf'
        if hashlib.sha256(pdf.read_bytes()).hexdigest()!=result['sha256']:raise ValueError('Frozen source bytes changed.')
        with pymupdf.open(pdf) as doc:text='\n'.join(p.get_text() for p in doc)
        result['metrics']=output_metrics(case/result['reader'],text)
        result['review_flags']=review_flags(result)
    run.update(measurement_version=2,artifact_root=str(artifact_root),remeasured_from=str(baseline_path),
               measurement_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    output.mkdir(parents=True,exist_ok=False);write_report(output,run)
    return run


def block_network():
    def blocked(*args,**kwargs):raise RuntimeError('Network requests are disabled for corpus evaluation.')
    socket.create_connection=blocked
    socket.socket.connect=blocked
    socket.socket.connect_ex=blocked


def worker(config_path):
    block_network()
    config=json.loads(Path(config_path).read_text(encoding='utf-8'))
    case=Path(config['case_directory']);entry=config['record'];started=time.monotonic()
    result={'case_id':case.name,'filename':entry['filename'],'sha256':entry['sha256'],
            'status':'failed','manual_review':'unreviewed','model_calls':0}
    try:
        from layout_compare import compare,pymupdf
        source=Path(config['source_directory']).resolve()
        path=(source/entry['filename']).resolve()
        if path.parent!=source:raise ValueError('Source path escapes the collection.')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=entry['sha256']:
            result['status']='source_changed';raise ValueError('Source hash no longer matches the frozen manifest.')
        pdf=case/'original.pdf';pdf.write_bytes(data);del data
        with pymupdf.open(pdf) as doc:
            result['runtime']={'python':sys.version.split()[0],'pymupdf':str(pymupdf.version)}
            if doc.needs_pass:
                result['status']='password_required';raise ValueError('Document requires a password.')
            if not len(doc):raise ValueError('Document contains no pages.')
            profile,text=profile_pdf(doc,config['max_pages']);result['profile']=profile
            result['pdf_title']=doc.metadata.get('title') or ''
            scale=min(.8,1200/max(doc[0].rect.width,doc[0].rect.height))
            doc[0].get_pixmap(matrix=pymupdf.Matrix(scale,scale)).save(case/'source-first-page.png')
        if profile['pages']>config['max_pages']:
            result['status']='skipped_page_limit'
        elif profile['large_page_candidates']:
            result['status']='skipped_geometry_limit'
        else:
            rendered=compare(pdf,case/'render',geometry_only=True,title=result['pdf_title'] or Path(entry['filename']).stem)
            result['metrics']=output_metrics(rendered,text)
            result['status']='converted'
            result['reader']='render/layout.html'
            result['review_flags']=review_flags(result)
    except Exception as exc:
        result['error_type']=type(exc).__name__;result['error']=str(exc)[:500]
    result['elapsed_seconds']=round(time.monotonic()-started,3)
    (case/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


def review_flags(result):
    flags=[];metrics=result.get('metrics',{});profile=result.get('profile',{})
    if result['status']!='converted':return [result['status']]
    if metrics['rendered_pages']!=profile['pages']:flags.append('page_count_mismatch')
    if any(f['status']=='needs_review' for f in metrics['table_findings']):flags.append('unresolved_tables')
    if profile.get('table_caption_pages') and not metrics['tables']:flags.append('table_captions_without_tables')
    if profile.get('scan_candidates'):flags.append('scan_or_ocr_review')
    if metrics['fallback_pages']:flags.append('fallback_pages')
    if metrics.get('composite_text_pages'):flags.append('source_page_image_review')
    if profile.get('column_candidates'):flags.append('column_order_review')
    if profile.get('pages',0)>2 and not metrics['headings']:flags.append('no_detected_section_headings')
    coverage=metrics['matched_source_token_fraction']
    if coverage is not None and coverage<.85:flags.append('text_preservation_review')
    flags.extend(sorted({f['code'] for f in metrics['static_findings']
                         if f['code'] in {'duplicate_ids','broken_fragment','invalid_table_headers','broken_aria_reference'}}))
    return flags


def child_environment(snapshot):
    env={k:v for k,v in os.environ.items() if not k.upper().startswith(('AWS','OPENAI','OLLAMA','CAMPUS'))}
    env.update(PYTHONPATH=os.pathsep.join(map(str,[snapshot,ROOT/'.layout-deps',ROOT/'.reader-deps'])),
               PYTHONIOENCODING='utf-8',AWS_EC2_METADATA_DISABLED='true',CAMPUS_VISION_ENABLED='false',OLLAMA_ENABLED='false')
    return env


def evaluate_case(config,snapshot,timeout):
    case=Path(config['case_directory']);case.mkdir(parents=True,exist_ok=False)
    config_path=case/'config.json';config_path.write_text(json.dumps(config,indent=2),encoding='utf-8')
    try:
        with (case/'worker.log').open('wb') as log:
            subprocess.run([sys.executable,str(snapshot/'corpus_evaluate.py'),'--worker',str(config_path)],
                           env=child_environment(snapshot),cwd=snapshot,stdout=log,stderr=subprocess.STDOUT,
                           timeout=timeout,check=True)
        return json.loads((case/'result.json').read_text(encoding='utf-8'))
    except (subprocess.TimeoutExpired,subprocess.CalledProcessError,OSError,ValueError) as exc:
        result={'case_id':case.name,'filename':config['record']['filename'],'sha256':config['record']['sha256'],
                'status':'timeout' if isinstance(exc,subprocess.TimeoutExpired) else 'worker_failed',
                'error_type':type(exc).__name__,'manual_review':'unreviewed','model_calls':0}
        (case/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        return result


def write_report(output,run):
    results=run['results'];statuses=Counter(r['status'] for r in results)
    flags=Counter(flag for r in results for flag in review_flags(r))
    run['summary']={'cases':len(results),'statuses':dict(statuses),'review_flags':dict(flags),
                    'holdout_documents_evaluated':0,'model_calls':0,'manual_reviews_completed':0}
    fingerprints=defaultdict(list)
    for r in results:
        key=r.get('profile',{}).get('first_page_text_sha256')
        if key:fingerprints[key].append(r['case_id'])
    run['possible_duplicate_first_pages']=[v for v in fingerprints.values() if len(v)>1]
    (output/'baseline.json').write_text(json.dumps(run,indent=2),encoding='utf-8')
    rows=[]
    for r in sorted(results,key=lambda r:(r['status']=='converted',-len(review_flags(r)),r['case_id'])):
        artifact_root=Path(run.get('artifact_root',output))
        case=artifact_root/'cases'/r['case_id']
        folder=os.path.relpath(case,output).replace('\\','/')+'/'
        links=[]
        if r.get('reader'):links.append('<a href="'+folder+r['reader']+'">HTML reader</a>')
        if (case/'original.pdf').exists():links.append('<a href="'+folder+'original.pdf">Source PDF</a>')
        links.append('<a href="'+folder+'result.json">Observations</a>')
        rows.append('<tr><th scope="row">'+escape(r['filename'])+'</th><td>'+escape(r['status'])+'</td><td>'+
                    str(r.get('profile',{}).get('pages','Unknown'))+'</td><td>'+escape(', '.join(r.get('profile',{}).get('tags',[])) or 'Unclassified')+
                    '</td><td>'+escape(', '.join(review_flags(r)) or 'No automated priority flags; still unreviewed')+'</td><td>'+ ' | '.join(links)+'</td></tr>')
    html='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'">
<title>Development baseline review</title><style>
body{margin:0;background:#f3f5f7;color:#18202a;font:1rem/1.55 Georgia,serif}main{max-width:90rem;margin:auto;padding:28px}
h1{color:#183d57}a{color:#124e76}a:focus-visible,[tabindex]:focus-visible{outline:3px solid #12639a;outline-offset:3px}
.notice{background:#fff8e8;border-left:4px solid #8a5c00;padding:16px}.grid{overflow:auto;background:white}table{width:100%;border-collapse:collapse}
caption{text-align:left;font-weight:bold;padding:14px}th,td{border:1px solid #cbd5df;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
thead{background:#e8eef3}tbody th{font-weight:normal;min-width:12rem}td:last-child{min-width:10rem}
</style></head><body><main><h1>Development baseline review</h1><p>Offline evaluation of fresh development examples.</p>
<div class="notice"><strong>All cases require human review.</strong> Converted means the program completed, not that the document is correct or WCAG compliant.
Profile categories and text-coverage flags are heuristics. No holdout documents, AWS services or AI models were used.</div>'''
    html+='<p>Run: '+escape(run['status'])+'. '+str(len(results))+' / '+str(run['expected_cases'])+' cases recorded.</p>'
    html+='<p>'+escape(json.dumps(run['summary']['statuses']))+'</p><p><a href="baseline.json">Complete baseline data</a></p>'
    html+='<div class="grid" tabindex="0" role="region" aria-label="Baseline cases"><table><caption>Development cases, with review priorities first</caption><thead><tr>'
    html+=''.join('<th scope="col">'+v+'</th>' for v in ['Document','Run status','Pages','Profile candidates','Review flags','Files'])+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    html+='<h2>Review protocol</h2><p>Compare source and HTML for reading order, heading meaning, table values and headers, figures, citations, keyboard and screen-reader behavior. '
    html+='Do not treat counts or fewer warnings as accuracy. Record findings in human-review.json; null means unreviewed.</p></main></body></html>'
    (output/'index.html').write_text(html,encoding='utf-8')


def evaluate(manifest_path,output,max_pages=40,timeout=90,workers=2):
    if not 1<=max_pages<=100 or not 5<=timeout<=300 or not 1<=workers<=2:raise ValueError('Unsafe evaluation limits.')
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'));records=selected_records(manifest)
    output=Path(output).resolve();source=Path(manifest['source_directory']).resolve()
    if output.exists() or output.is_relative_to(source):raise ValueError('Use a new private output directory outside the PDF collection.')
    output.mkdir(parents=True,exist_ok=False);snapshot=output/'code-snapshot';snapshot.mkdir()
    code_hashes={}
    for name in ENGINE_FILES:
        original=ROOT/name;data=original.read_bytes();target=snapshot/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data);code_hashes[name]=hashlib.sha256(data).hexdigest()
    run={'schema_version':1,'measurement_version':2,'status':'running','expected_cases':len(records),'created_utc':datetime.now(timezone.utc).isoformat(),
         'manifest_sha256':hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
         'code_sha256':code_hashes,'limits':{'max_pages':max_pages,'timeout_seconds':timeout,'workers':workers},
         'scope':'Development only; static metrics and profiles are not accuracy or WCAG certification.','results':[]}
    write_report(output,run)
    jobs=[{'case_directory':str(output/'cases'/f'{i:02d}-{r["sha256"][:12]}'),
           'record':r,'source_directory':str(source),'max_pages':max_pages} for i,r in enumerate(records,1)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(evaluate_case,job,snapshot,timeout):job for job in jobs}
        for future in as_completed(pending):
            result=future.result();run['results'].append(result);write_report(output,run)
            print(f'{len(run["results"])}/{len(jobs)}: {result["case_id"]} - {result["status"]}',flush=True)
    run['status']='complete';run['completed_utc']=datetime.now(timezone.utc).isoformat();write_report(output,run)
    (output/'human-review.json').write_text(json.dumps({'cases':[
        {'case_id':r['case_id'],'sha256':r['sha256'],'status':'unreviewed',
         'checks':{k:None for k in ('reading_order','headings','tables','figures','links','keyboard','screen_reader','zoom')},'notes':''}
        for r in run['results']]},indent=2),encoding='utf-8')
    print(json.dumps(run['summary']),flush=True)
    return run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--remeasure',type=Path)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--max-pages',type=int,default=40)
    parser.add_argument('--timeout',type=int,default=90)
    args=parser.parse_args()
    if args.worker:worker(args.worker)
    elif args.remeasure and args.output:remeasure(args.remeasure,args.output)
    elif args.manifest and args.output:evaluate(args.manifest,args.output,args.max_pages,args.timeout)
    else:parser.error('Provide --manifest and --output.')


if __name__=='__main__':main()
