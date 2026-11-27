"""Real local OCR -> candidate -> editable main -> save/reopen -> subtitle export."""
from pathlib import Path
import os,sys,time,json,hashlib
from copy import deepcopy
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
os.environ.setdefault('OMP_THREAD_LIMIT','2')
from PySide6.QtWidgets import QApplication
from test_qt_import_panel import silent_text_video
from desktop_qt.controller import Bridge
from lyricsync.editing import mapping_text
from lyricsync.project_document import write_document,load_document
from lyricsync.export import export_project
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
app=QApplication.instance() or QApplication([])
video=silent_text_video.__wrapped__(out);before=hashlib.sha256(video.read_bytes()).hexdigest()
translation=out/'translation.txt';translation.write_text('translated line',encoding='utf-8')
original=out/'original.txt';original.write_text('HELLO WORLD',encoding='utf-8')
results=[]
def wait(b,predicate):
    deadline=time.monotonic()+120
    while not predicate() and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
    assert predicate(),b.state.get('error')
def settle(b):wait(b,lambda:not b.jobs.active and not b._handlers)
for source,translated in [(False,False),(False,True),(True,False),(True,True)]:
    label=f'reference-{source}-translation-{translated}'
    b=Bridge(storage_dir=out/label)
    try:
        assert b.command('project.newEntry',{'mode':'ocr'})
        assert b.command('media.open',{'path':str(video)})
        wait(b,lambda:b.media['ready'] and b.media['displayedFrame']==1)
        assert b.command('roi.set',{'x':0.,'y':.3,'width':1.,'height':.4})
        if source:
            assert b.command('input.import',{'role':'source','path':str(original)});settle(b)
        if translated:
            assert b.command('input.import',{'role':'translation','path':str(translation)});settle(b)
        original_before=deepcopy(b.document.project.lyrics)
        b.document.project.settings.update(engine='rapidocr',language='eng',sample_fps=3,api_enabled=True)
        with patch('lyricsync.pipeline.suggest',side_effect=AssertionError('no AI')) as provider:
            assert b.command('ocr.start',{});settle(b)
        assert not provider.called
        project=b.document.project
        assert project.mappings,b.state
        expected='translated line' if translated else 'HELLO WORLD'
        assert [mapping_text(project,row) for row in project.mappings]==[expected]
        assert bool(project.lyrics)==source
        if source:assert project.lyrics==original_before
        assert not any(row.id.startswith('ocr-source-') for row in project.lyrics)
        assert project.visuals and project.visuals[0].text=='HELLO WORLD'
        record=b.document.source_candidates[-1]
        assert bool(record['evidence']['lyrics'])==source
        save=out/(label+'.lyria');write_document(b.document.snapshot(explicit_save=True),save)
        loaded=load_document(save)
        assert loaded.project.to_dict()==project.to_dict()
        exported=out/(label+'.srt');export_project(loaded.project,exported,draft=True)
        assert expected in exported.read_text('utf-8-sig')
        results.append({'case':label,'main_text':expected,'reference_count':len(project.lyrics),'role':project.mappings[0].text_role,'api_calls':provider.call_count,'saved_reopened_exported':True})
    finally:b.shutdown();b.wait_for_shutdown(app)
assert before==hashlib.sha256(video.read_bytes()).hexdigest()
(out/'result.json').write_text(json.dumps({'scope':'real local synthetic English OCR and Qt offscreen Bridge; no frontend or Japanese user acceptance','cases':results,'video_sha256':before},ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
