"""Generate isolated media and execute real local RapidOCR, never a remote provider."""
from pathlib import Path
import sys, json, os, time, hashlib
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('OMP_THREAD_LIMIT','2')
from test_qt_import_panel import silent_text_video
from lyricsync.pipeline import run
from lyricsync.model import Project
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
video=silent_text_video.__wrapped__(out)
before=hashlib.sha256(video.read_bytes()).hexdigest()
p=Project();p.inputs['video']=str(video)
p.settings.update(roi=[0,54,320,126],language='eng',api_enabled=True)
started=time.monotonic()
with patch('lyricsync.pipeline.suggest',side_effect=AssertionError('No AI allowed')) as provider:
    run(p,out/'ocr-result.json')
assert not provider.called
assert not p.lyrics and p.visuals
assert any('HELLO WORLD' in v.text.upper() for v in p.visuals)
assert all(not row.lyric_ids for row in p.mappings)
assert before==hashlib.sha256(video.read_bytes()).hexdigest()
summary={'scope':'Synthetic English video; real local RapidOCR; no Japanese user material or frontend acceptance',
         'seconds':time.monotonic()-started,'engine':p.settings['engine'],'visuals':[{'text':v.text,'start':v.start,'end':v.end} for v in p.visuals],
         'reference_original_count':len(p.lyrics),'mapping_count':len(p.mappings),'api_calls':provider.call_count,'video_sha256':before,'metrics':p.video.get('ocr_metrics')}
(out/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
