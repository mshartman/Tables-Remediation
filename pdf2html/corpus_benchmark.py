"""Prepare a private, read-only PDF benchmark. No conversion or network calls."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time


def rank(value, seed):
    return hashlib.sha256((seed + ':' + value).encode('utf-8')).hexdigest()


def partition(records, seed='pdf-layout-v1', known_reviewed=()):
    """Group exact duplicates BEFORE assignment; previously seen groups stay in dev."""
    known={str(name).casefold() for name in known_reviewed}
    reviewed={r['sha256'] for r in records if Path(r['filename']).stem.casefold() in known}
    for record in records:
        seen=record['sha256'] in reviewed
        record['previously_reviewed']=seen
        record['split']='development' if seen or int(rank(record['sha256'],seed),16)%100<80 else 'holdout'
    return records


def development_sample(records, seed='pdf-layout-v1', count=24):
    unique={}
    for record in sorted(records,key=lambda r:r['filename'].casefold()):
        if record['split']=='development' and not record['previously_reviewed']:
            unique.setdefault(record['sha256'],record)
    ordered=sorted(unique.values(),key=lambda r:(r['bytes'],r['sha256']))
    bins=[[] for _ in range(4)]
    for index,record in enumerate(ordered):
        bins[min(3,index*4//max(1,len(ordered)))].append(record)
    for group in bins:group.sort(key=lambda r:rank(r['sha256'],seed+'-sample'))
    selected=[]
    while len(selected)<count and any(bins):
        for group in bins:
            if group and len(selected)<count:selected.append(group.pop(0))
    return [r['sha256'] for r in selected]


def next_batch(manifest,count=5):
    """Select fresh development hashes without consulting holdout PDF content."""
    if not 1<=count<=200:raise ValueError('Batch size must be between 1 and 200.')
    import copy
    result=copy.deepcopy(manifest)
    used=set(result.get('excluded_prior_sample_sha256',[]))|set(result['development_sample_sha256'])
    available=[r for r in result['records'] if r['sha256'] not in used]
    seed=result['seed']+':next:'+hashlib.sha256(''.join(sorted(used)).encode()).hexdigest()
    sample=development_sample(available,seed,count)
    if len(sample)!=count:raise ValueError('Not enough fresh development groups remain.')
    result.update(development_sample_sha256=sample,excluded_prior_sample_sha256=sorted(used),
                  sample_created_utc=datetime.now(timezone.utc).isoformat(),
                  selection_scope='Fresh size-diverse development sample; no holdout selection or content inspection.')
    return result


def read_record(path):
    try:
        before=path.stat();digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ValueError('File changed during hashing.')
        return {'filename':path.name,'bytes':before.st_size,'sha256':digest.hexdigest()},None
    except (OSError,ValueError) as exc:
        return None,{'filename':path.name,'error_type':type(exc).__name__}


def summarize(records,errors,found):
    counts=Counter(r['sha256'] for r in records)
    return {'files_found':found,'files_hashed':len(records),'source_bytes':sum(r['bytes'] for r in records),
            'unique_sha256':len(counts),'exact_duplicate_copies':sum(n-1 for n in counts.values()),
            'files_by_split':dict(Counter(r['split'] for r in records)),
            'unique_groups_by_split':dict(Counter({r['sha256']:r['split'] for r in records}.values())),
            'previously_reviewed_files':sum(r['previously_reviewed'] for r in records),'errors':len(errors)}


def extend_manifest(manifest_path,source_file,output):
    """Append a newly discovered file in a NEW manifest, preserving old evidence."""
    original=Path(manifest_path).resolve();source_file=Path(source_file).resolve();output=Path(output).resolve()
    data=json.loads(original.read_text(encoding='utf-8'))
    source=Path(data['source_directory']).resolve()
    if output.exists() or output.is_relative_to(source):raise ValueError('Choose a new output directory outside the source.')
    if source_file.parent!=source or any(r['filename']==source_file.name for r in data['records']):
        raise ValueError('Only a new source filename can be appended.')
    record,error=read_record(source_file)
    if error:raise ValueError('The additional source file could not be hashed.')
    data['records'].append(record)
    partition(data['records'],data['seed'],data['known_reviewed_stems'])
    data['summary']=summarize(data['records'],data['errors'],data['summary']['files_found']+1)
    data['development_sample_sha256']=development_sample(data['records'],data['seed'])
    data['extended_from']=str(original)
    data['extended_utc']=datetime.now(timezone.utc).isoformat()
    data['limitations'].append('Manifest extended with a new file hash; existing hashes retain their original observation time.')
    output.mkdir(parents=True,exist_ok=False)
    (output/'manifest.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    (output/'summary.txt').write_text(json.dumps(data['summary'],indent=2)+'\n\n'+'\n'.join(data['limitations']),encoding='utf-8')
    (output/'review-template.json').write_text(json.dumps({'cases':[
        {'sha256':sha,'filename':next(r['filename'] for r in data['records'] if r['sha256']==sha),
         'status':'unreviewed','layout_types':[],
         'checks':{key:None for key in ('reading_order','headings','table_values','table_headers','figures_and_captions','links','keyboard','screen_reader','zoom_and_reflow')},'notes':''}
        for sha in data['development_sample_sha256']]},indent=2),encoding='utf-8')
    return data


def prepare(source, output, seed='pdf-layout-v1', known_reviewed=(), sample_size=24, progress=None, workers=4):
    if not 1<=workers<=4:raise ValueError('Use between one and four readers.')
    progress=progress or (lambda message:print(message,flush=True))
    source,output=Path(source).resolve(),Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('Output must be outside the source PDF collection.')
    if output.exists():
        raise ValueError('Use a new output directory; frozen benchmark manifests are never overwritten.')
    files=sorted((p for p in source.iterdir() if p.name.lower().endswith('.pdf') and p.is_file() and not p.is_symlink()),key=lambda p:p.name.casefold())
    if not files:raise ValueError('No PDFs were found directly in the source directory.')
    records=[];errors=[];started=time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index,(record,error) in enumerate(pool.map(read_record,files),1):
            if error:errors.append(error)
            else:records.append(record)
            if index%100==0 or index==len(files):
                progress(f'Hashed {index}/{len(files)} PDFs ({time.monotonic()-started:.0f}s); unreadable/changed: {len(errors)}')
    partition(records,seed,known_reviewed)
    summary=summarize(records,errors,len(files))
    sample=development_sample(records,seed,sample_size)
    manifest={'schema_version':1,'created_utc':datetime.now(timezone.utc).isoformat(),'source_directory':str(source),
              'seed':seed,'summary':summary,'records':records,'errors':errors,
              'development_sample_sha256':sample,'known_reviewed_stems':sorted(known_reviewed),
              'limitations':[
                  'Exact duplicate grouping only. Rescans, different editions and related articles may still cross splits.',
                  'Group near-duplicates and publisher/article families before claiming independent performance.',
                  'The development sample is diverse by file size, not yet verified by layout type.',
                  'No PDF structure extraction, conversion, model training, AI calls or accessibility certification was performed.',
                  'Filename exclusions identify known examples, not a complete record of all prior exposure.'
              ]}
    output.mkdir(parents=True,exist_ok=False)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (output/'review-template.json').write_text(json.dumps({'cases':[
        {'sha256':sha,'status':'unreviewed','layout_types':[],
         'checks':{key:None for key in ('reading_order','headings','table_values','table_headers','figures_and_captions','links','keyboard','screen_reader','zoom_and_reflow')},
         'notes':''} for sha in sample]},indent=2),encoding='utf-8')
    summary_text='PDF BENCHMARK INVENTORY\n'+json.dumps(summary,indent=2)+'\n\n'+'\n'.join(manifest['limitations'])
    (output/'summary.txt').write_text(summary_text,encoding='utf-8')
    progress(json.dumps(summary))
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--seed',default='pdf-layout-v1')
    parser.add_argument('--known-reviewed',nargs='*',default=[])
    parser.add_argument('--sample-size',type=int,default=24)
    parser.add_argument('--workers',type=int,choices=range(1,5),default=4)
    args=parser.parse_args()
    if not 1<=args.sample_size<=200:parser.error('sample-size must be between 1 and 200.')
    prepare(args.source,args.output,args.seed,args.known_reviewed,args.sample_size,workers=args.workers)


if __name__=='__main__':main()
