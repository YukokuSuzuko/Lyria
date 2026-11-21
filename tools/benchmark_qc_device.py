"""Bounded real-device QC probe. Reads a supplied project/video, writes only output."""
import argparse,json,time,statistics,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser();parser.add_argument('project');parser.add_argument('output');args=parser.parse_args()
from lyricsync.project_document import load_document
from lyricsync.vision import Video,make_ocr
from lyricsync.audit_service import AuditClock,refine_cues
from lyricsync.model import Visual
from lyricsync.storage import Cache,file_hash
from lyricsync.qc_ocr_cache import CachedOCR
import urllib.request
urllib.request.OpenerDirector.open=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No network in local probe'))
p=load_document(args.project).project;video=Video(p.inputs['video']);out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
region=p.settings.get('roi') or [0,int(video.info['height']*.7),video.info['width'],video.info['height']]
rows=[m for m in p.mappings if m.start is not None and m.end is not None and m.end>m.start][:2]
stamps=[rows[0].start+i/float(video.info['fps']) for i in range(12)]
frames=[]
for stamp in stamps:
 actual,frame=video.at(stamp);frames.append((actual,frame))
result={'video_sha256':file_hash(video.path),'video_info':video.info,'region':region,'sample_pts':[t for t,_ in frames],
        'bounded_frames':len(frames),'scope':'63-row older project source video, not verified to be user 65-row finished QC input','devices':{}}
x1,y1,x2,y2=region
for device in ('cpu','directml'):
 start=time.perf_counter();entry={};result['devices'][device]=entry
 try:
  provider=make_ocr({'engine':'rapidocr','language':'eng','ocr_device':device,'ocr_threads':2})
  entry.update(initialization_seconds=time.perf_counter()-start,signature=provider.signature)
  durations=[];texts=[]
  for _,frame in frames:
   tick=time.perf_counter();found=provider.recognize(frame[y1:y2,x1:x2]);durations.append(time.perf_counter()-tick);texts.append([d.text for d in found])
  entry.update(seconds=sum(durations),median_frame_ms=1000*statistics.median(durations),recognitions=texts,
               cold_first_frame_seconds=durations[0],frame_seconds=durations,success=True)
 except Exception as exc:entry.update(success=False,error=type(exc).__name__+': '+str(exc))
 entry['total_seconds']=time.perf_counter()-start
 (out/'gpu-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({device:entry},ensure_ascii=False),flush=True)
# Optimized exact-frame cache on changing real pixels, not a static synthetic frame.
settings={'engine':'rapidocr','language':'eng','ocr_device':'cpu','ocr_threads':2}
provider=make_ocr(settings);cache=Cache(out/'probe.cache.sqlite')
cached=CachedOCR(provider,cache,result['video_sha256'],region,settings)
for label in ('cold','warm'):
 tick=time.perf_counter()
 for _,frame in frames:cached.recognize(frame[y1:y2,x1:x2])
 result['devices']['optimized_'+label]={'seconds':time.perf_counter()-tick,'metrics':dict(cached.metrics)}
cache.close()
(out/'gpu-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(result,ensure_ascii=False),flush=True)