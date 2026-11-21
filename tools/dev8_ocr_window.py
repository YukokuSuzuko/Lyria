"""Fixed-settings short-window A/B, not a full-song accuracy benchmark."""
from pathlib import Path
import argparse,ast,json,sys
from dataclasses import asdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync import vision
from lyricsync.model import Project
from lyricsync.storage import Cache,file_hash
p=argparse.ArgumentParser();p.add_argument('--audit',required=True);p.add_argument('--baseline',required=True);p.add_argument('--out',required=True);a=p.parse_args()
project=Project.from_dict(json.loads(Path(a.audit).read_text(encoding='utf-8')))
settings=dict(project.settings);settings.update(scan_range=[28.8,34.2],ocr_device='directml')
provider=vision.make_ocr(settings,[l.text for l in project.lyrics])
tree=ast.parse(Path(a.baseline).read_text(encoding='utf-8'));nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('refine_track','scan')]
ns=dict(vars(vision));exec(compile(ast.Module(body=nodes,type_ignores=[]),a.baseline,'exec'),ns)
out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
report={'video_sha256_before':file_hash(project.inputs['video']),'settings':settings,'provider':provider.signature,'results':{}}
for name,scan in [('baseline',ns['scan']),('dev8',vision.scan)]:
    cache=Cache(out/(name+'.sqlite'))
    try:visuals,info=scan(project.inputs['video'],settings,cache,[l.text for l in project.lyrics],provider=provider)
    finally:cache.db.close()
    report['results'][name]={'visuals':[asdict(v) for v in visuals],'metrics':info['ocr_metrics']}
report['video_sha256_after']=file_hash(project.inputs['video'])
report['limitations']='Only 28.8-34.2 second window, one same-input/settings comparison. No full-song accuracy, speed or dev6 regression claim. Baseline ran first with same warmed provider; times not a controlled performance comparison.'
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'hash_unchanged':report['video_sha256_before']==report['video_sha256_after'],'results':{k:{'visuals':[(v['start'],v['end'],v['warnings']) for v in r['visuals']],'metrics':r['metrics']} for k,r in report['results'].items()}},ensure_ascii=False,indent=2))
