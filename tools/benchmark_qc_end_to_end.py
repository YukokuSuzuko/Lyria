"""Bounded end-to-end QC comparison using a private synthetic finished clip.
The supplied original is only read. This is not the user's final output.
"""
import argparse,json,time,subprocess,sys,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyricsync.project_document import load_document,write_document,ProjectDocument
from lyricsync.editing import mapping_text
from lyricsync.vision import Video
from lyricsync.audit_service import run_audit,ffmpeg_executable
from lyricsync.storage import file_hash
parser=argparse.ArgumentParser();parser.add_argument('project');parser.add_argument('output');parser.add_argument('--mode',choices=('prepare','cpu','directml','sentence'),required=True);args=parser.parse_args()
root=Path.cwd();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
meta=out/'fixture.json';clip=out/'private-synthetic-finished.mp4';ass=out/'chinese-reference.ass';ref=out/'reference.lyria'
urllib.request.OpenerDirector.open=lambda *a,**k:(_ for _ in ()).throw(AssertionError('no network'))
if args.mode=='prepare':
 if any(p.exists() for p in (meta,clip,ass,ref)):raise FileExistsError('Use a new empty fixture directory; do not overwrite prior measurements')
 p=load_document(args.project).project;source=p.inputs['video'];start=35;duration=20
 rows=[m for m in p.mappings if m.start is not None and m.end is not None and m.start<start+duration and m.end>start]
 header='[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Chinese,Microsoft YaHei,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,190,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
 def stamp(n):
  cs=round(n*100);return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'
 for m in rows:
  text=mapping_text(p,m).replace('\\','\\\\').replace('{','\\{').replace('}','\\}').replace('\n','\\N')
  m.start=max(0,m.start-start);m.end=min(duration,m.end-start)
  header+=f'Dialogue: 0,{stamp(m.start)},{stamp(m.end)},Chinese,,0,0,0,,{text}\n'
 ass.write_text(header,encoding='utf8')
 before=file_hash(source);tick=time.perf_counter()
 command=[ffmpeg_executable(),'-hide_banner','-loglevel','error','-nostdin','-ss',str(start),'-i',source,'-t',str(duration),'-an','-vf','subtitles=chinese-reference.ass','-c:v','libx264','-preset','ultrafast','-crf','18','-fps_mode','passthrough','-n',str(clip)]
 subprocess.run(command,cwd=out,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=120)
 p.settings.pop('pending_text_splits',None);p.mappings=rows;p.inputs={'video':str(clip),'translation':str(ass)};p.video={}
 write_document(ProjectDocument(p).snapshot(),ref)
 v=Video(clip);actual,frame=v.at(4)
 import cv2
 ok,encoded=cv2.imencode('.png',frame);(out/'actual-frame.png').write_bytes(encoded.tobytes())
 record={'original_sha256_before':before,'original_sha256_after':file_hash(source),'window_start':start,'duration':duration,
 'clip_sha256':file_hash(clip),'preparation_seconds':time.perf_counter()-tick,'rows':len(rows),
 'scope':'20-second real moving original background with old-project Chinese synthetically burned; not user final video',
 'video':v.info,'screenshot_seconds':actual}
 meta.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(record,ensure_ascii=False));raise SystemExit()
options={'engine':'rapidocr','ocr_device':'directml' if args.mode=='directml' else 'cpu','ocr_threads':2,'sample_fps':3,
 'chinese_language':'chi_sim','source_language':'eng','audit_use_current_project':True}
if args.mode=='sentence':options['audit_strategy']='sentence'
updates=[];tick=time.perf_counter()
def progress(p,m):
 updates.append({'elapsed':time.perf_counter()-tick,'progress':p,'message':m})
 if not len(updates)%30:print(f'{args.mode} {p:.0%} {m}',flush=True)
report=run_audit(clip,ass,out/args.mode,project_path=ref,settings=options,
 chinese_roi={'box':[0,780,1920,930],'origin':'user','confirmed':True},
 source_roi={'box':[201,951,1680,1080],'origin':'user','confirmed':True},progress=progress)
result={'mode':args.mode,'total_seconds':time.perf_counter()-tick,'rows':len(report['cues']),
 'coverage':report['coverage'],'local_performance':report.get('local_performance'),'performance':report.get('performance'),
 'progress':updates,'fixture':json.loads(meta.read_text(encoding='utf8'))}
(out/(args.mode+'-timing.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in result.items() if k!='progress'},ensure_ascii=False),flush=True)