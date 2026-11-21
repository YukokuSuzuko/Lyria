"""Private, read-only-input boundary probe. Reports locally, never uploads media."""
from pathlib import Path
import argparse, ast, json, sys, time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync import vision
from lyricsync.model import Project
from lyricsync.storage import file_hash

parser=argparse.ArgumentParser()
parser.add_argument('--audit',required=True)
parser.add_argument('--baseline',required=True)
parser.add_argument('--out',required=True)
a=parser.parse_args()
raw=json.loads(Path(a.audit).read_text(encoding='utf-8'))
p=Project.from_dict(raw)
video=vision.Video(p.inputs['video'])
# Preserve the original resource/device/ROI/language selection; no model downloads.
settings=dict(p.settings)
settings['ocr_device']='directml'
provider=vision.make_ocr(settings,[l.text for l in p.lyrics])
roi=settings['roi'];x1,y1,x2,y2=roi
memo={};calls=[]
def probe(stamp,frame):
    key=round(stamp,6)
    if key not in memo:
        start=time.monotonic();found=provider.recognize(frame[y1:y2,x1:x2])
        for d in found:d.box=[d.box[0]+x1,d.box[1]+y1,d.box[2]+x1,d.box[3]+y1]
        memo[key]=found;calls.append({'pts':stamp,'seconds':time.monotonic()-start,'detections':[{'text':d.text,'confidence':d.confidence,'box':d.box} for d in found]})
    return memo[key]
# Re-run the frozen implementation on identical reconstructed tracks.
tree=ast.parse(Path(a.baseline).read_text(encoding='utf-8'))
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='refine_track')
ns=dict(vars(vision));exec(compile(ast.Module(body=[fn],type_ignores=[]),a.baseline,'exec'),ns)
report={'video_sha256':file_hash(p.inputs['video']),'roi':roi,'settings':settings,'ocr_signature':provider.signature,'results':[]}
for v in p.visuals:
    if not (28<v.start<32):continue
    e=v.evidence;step=1/video.info['fps'];interval=1/settings.get('sample_fps',3)
    cadence=e['samples'][1][0]-e['samples'][0][0]
    _,frame=video.at(e['seed_time'])
    tr=vision.Track(e['coarse_first'],e['coarse_last'],v.box,[v.text],[v.confidence],vision.edge_mask(vision.patch(frame,v.box)),e['seed_time'],e['coarse_last'],prior_absent=e['coarse_first']-cadence,next_absent=e['coarse_last']+cadence,samples=e['samples'])
    before=ns['refine_track'](video,tr,interval,.62,lambda:False)
    fallback=vision.refine_track(video,tr,interval,.62,lambda:False)
    after=vision.refine_track(video,tr,interval,.62,lambda:False,probe)
    report['results'].append({'id':v.id,'text':v.text,'saved':[v.start,v.end],'baseline':before,'coarse_fallback':fallback,'with_text':after})
report['probes']=calls
report['limitations']='Two reconstructed tracks only. No full-song rescan or device/version A/B; no ASS/audio truth. Read-only source video. This tests the known 31.353744 switch only.'
out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'report':str(out),'calls':len(calls),'seconds':sum(c['seconds'] for c in calls),'results':report['results']},ensure_ascii=False,indent=2))
