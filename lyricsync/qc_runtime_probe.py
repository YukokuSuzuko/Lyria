"""Explicit packaged-runtime QC probe; never called during ordinary startup."""
from pathlib import Path
import hashlib,json,time
from .audit_service import run_audit
from .storage import atomic_json,protect_output


def run(video,reference,target):
    target=Path(target).resolve();protect_output(target,[video,reference])
    if target.exists():raise FileExistsError('自检报告已存在，请使用新的输出位置。')
    folder=target.parent/target.stem
    if folder.exists():raise FileExistsError('自检目录已存在，请使用新的输出位置。')
    results=[];digests=[]
    for label in ('cold','warm'):
        tick=time.perf_counter()
        report=run_audit(video,reference,folder/label,mode='local',settings={'engine':'rapidocr','ocr_device':'cpu','ocr_threads':2,'sample_fps':3},cache_dir=folder/'cache')
        keys=('id','text','start','end','first_frame','last_frame','confidence','boundary_status','raw_observations','boundary_evidence','warnings')
        evidence=[{key:cue.get(key) for key in keys} for cue in report['cues']]
        digest=hashlib.sha256(json.dumps(evidence,ensure_ascii=False,sort_keys=True).encode()).hexdigest();digests.append(digest)
        results.append({'mode':label,'seconds':time.perf_counter()-tick,'cues':len(evidence),'evidence_sha256':digest,'performance':report['local_performance']})
    ok=bool(results[0]['cues']) and digests[0]==digests[1] and results[1]['performance']['chinese']['ocr_reuse']['model_calls']==0
    atomic_json(target,{'kind':'packaged-local-qc-cache-check','ok':ok,'no_api':True,'runs':results},inputs=[video,reference])
    return 0 if ok else 1
