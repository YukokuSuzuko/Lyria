"""Explicit production separation-preview validation on an authorized real clip."""
from pathlib import Path
import argparse,json,hashlib,sys,time

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--source-tree',required=True);p.add_argument('--fixture',required=True)
    p.add_argument('--runtime',required=True);p.add_argument('--evidence',required=True)
    p.add_argument('--hq3-folder',required=True);p.add_argument('--hq1-folder',required=True);args=p.parse_args()
    source_tree=Path(args.source_tree).resolve();sys.path.insert(0,str(source_tree))
    from lyricsync import separation_preview,local_separation
    from lyricsync.local_models import ModelManager,UVR,UVR_HQ1
    root=Path(args.evidence).resolve();root.mkdir(parents=True,exist_ok=True)
    fixture=json.loads(Path(args.fixture).read_text(encoding='utf-8'))
    source=Path(fixture['source']);before=digest(source);assert before==fixture['source_sha256']
    watched=['lyricsync/separation_preview.py','lyricsync/local_separation.py','lyricsync/local_alignment_worker.py','lyricsync/local_models.py']
    code_before={name:digest(source_tree/name) for name in watched}
    manager=ModelManager(root/'cache')
    for spec,folder in ((UVR,args.hq3_folder),(UVR_HQ1,args.hq1_folder)):manager.import_folder(folder,spec.id)
    observed=[];original=local_separation.run_worker
    def recorded(request,*a,**kw):
        observed.append(dict(operation=request.get('operation'),model=request.get('separation_model_id'),contains_lyrics='rows' in request))
        return original(request,*a,**kw)
    local_separation.run_worker=recorded
    results=[];clip=fixture['clips'][0]
    for spec in (UVR,UVR_HQ1):
        options=dict(audio_path=str(source),runtime_python=args.runtime,cache_root=str(manager.root),
                     device='cuda:0',offset_seconds=clip['offset_seconds'],duration_seconds=clip['duration_seconds'],separation_model_id=spec.id)
        started=time.perf_counter()
        result=separation_preview.generate_separation_preview(options=options,
            progress=lambda p,m:print(json.dumps(dict(model=spec.id,percent=p,message=m),ensure_ascii=True),flush=True))
        result['wall_seconds']=time.perf_counter()-started
        assert result['kind']=='separation_preview'
        assert result['source_sha256']==before
        assert result['samples']==round(clip['duration_seconds']*44100)
        assert result['sample_rate']==44100 and result['offset_seconds']==clip['offset_seconds']
        assert result['latency_samples']==0 and result['padding_removed'] is True
        assert result['runtime']['actual_matrix_provider']=='CUDAExecutionProvider'
        assert result['runtime']['providers'][0]=='CUDAExecutionProvider'
        assert result['runtime']['cpu_ep_fallback_disabled'] is True
        assert digest(result['stem_path'])==result['sha256']
        assert json.loads(Path(result['manifest_path']).read_text(encoding='utf-8'))['sha256']==result['sha256']
        (root/(spec.id+'-preview.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');results.append(result)
    assert results[0]['sha256']!=results[1]['sha256']
    assert len(observed)==2 and all(x['operation']=='separate' and not x['contains_lyrics'] for x in observed)
    assert digest(source)==before
    assert code_before=={name:digest(source_tree/name) for name in watched}
    summary=dict(scope='Actual new lyrics-free separation_preview API on current owner Japanese video; not listening quality, GUI audition or portable-package acceptance.',source_filename=source.name,source_sha256=before,source_unchanged=True,code_sha256=code_before,models=[{k:v for k,v in x.items() if k not in ('path','stem_path','manifest_path','source_path','source_stamp','stem_stamp','id')} for x in results],observed_worker_requests=observed,outputs_differ=True,model_code_unchanged_during_run=True)
    (root/'preview-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True))

if __name__=='__main__':main()