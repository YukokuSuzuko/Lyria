"""Explicit isolated offline worker validation using a caller-provided fixture."""
import argparse,json,time,hashlib
from pathlib import Path
from lyricsync.local_models import ModelManager,UVR,UVR_HQ1
from lyricsync.local_alignment_runtime import run_worker

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--runtime',required=True);p.add_argument('--audio',required=True);p.add_argument('--hq3-folder',required=True);p.add_argument('--hq1-folder',required=True);p.add_argument('--resources');a=p.parse_args()
    root=Path(a.root);root.mkdir(parents=True,exist_ok=True)
    manager=ModelManager(root/'cache')
    for spec,folder in [(UVR,a.hq3_folder),(UVR_HQ1,a.hq1_folder)]:
        manager.import_folder(folder,spec.id)
    rows=[]
    for spec in (UVR,UVR_HQ1):
        request=dict(operation='separate',audio_wav=a.audio,output_wav=str(root/(spec.id+'.wav')),cache_root=str(manager.root),device='cuda:0',separation_model_id=spec.id)
        (root/(spec.id+'-request.json')).write_text(json.dumps(request,indent=2),encoding='utf-8')
        started=time.monotonic();result=run_worker(request,a.runtime,progress=lambda p,m:print(p,m,flush=True))
        result['wall_seconds']=time.monotonic()-started
        result['output_sha256']=hashlib.sha256(Path(request['output_wav']).read_bytes()).hexdigest()
        (root/(spec.id+'-result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        rows.append(result)
    if a.resources:
        from lyricsync import vision
        from lyricsync.local_separation import prepare_vocals,stem_for_candidate,file_digest
        vision.resources=lambda:Path(a.resources)
        for spec in (UVR,UVR_HQ1):
            result=prepare_vocals(dict(audio_path=a.audio,cache_root=str(manager.root),device='cuda:0',
                runtime_python=a.runtime,offset_seconds=0.,duration_seconds=3.,separation_model_id=spec.id))
            record={'provenance':{'auxiliary_files':{
                role:{'path':value,'sha256':file_digest(value)} for role,value in
                [('alignment_audio',a.audio),('separated_vocals',result['path']),('separation_manifest',result['manifest_path'])]}}}
            assert stem_for_candidate(record)['model_id']==spec.id
            (root/(spec.id+'-orchestrator.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert rows[0]['model_id']!=rows[1]['model_id'] and rows[0]['output_sha256']!=rows[1]['output_sha256']
    assert rows[0]['samples']==rows[1]['samples']
    (root/'summary.json').write_text(json.dumps({'scope':'Actual offline GPU separation on synthetic English fixture, NOT singing quality acceptance or packaged GUI','models':rows},ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
