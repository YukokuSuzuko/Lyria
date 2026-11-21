"""Actual local ONNX OCR through Qt Bridge, then blank-row editing/save/export.

Synthetic fixture only; no claim of semantic correctness or Windows UI fidelity.
No live provider calls. Filled Chinese is explicitly test text, not a translation.
Run: python tests/run_qt_video_only.py OUTPUT_DIRECTORY
"""
from pathlib import Path
import hashlib
import json
import os
import sys
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(folder):
    out=Path(folder).resolve();out.mkdir(parents=True,exist_ok=True)
    (out/'cache').mkdir(exist_ok=True)
    os.environ['XDG_CACHE_HOME']=str(out/'cache')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtMultimedia import QVideoSink
    from desktop_qt.controller import Bridge
    from lyricsync.editing import mapping_text
    from lyricsync.project_document import load_document
    from lyricsync.storage import Cache
    from lyricsync.subtitles import parse_ass
    video=ROOT/'examples'/'synthetic_mv.mp4'
    source_hash=hashlib.sha256(video.read_bytes()).hexdigest()
    app=QApplication.instance() or QApplication([])
    b=Bridge(storage_dir=out/'private-recovery');sink=QVideoSink();b.attachVideoSink(sink)
    def wait(predicate,timeout=180):
        until=time.monotonic()+timeout
        while not predicate() and time.monotonic()<until:
            app.processEvents();time.sleep(.005)
        assert predicate(), 'Qt operation timed out: '+str(b.state)
    started=time.monotonic()
    try:
        assert b.command('media.open',{'path':str(video)})
        wait(lambda:b.media['displayedFrame']==1 and not b._task['running'])
        # The product deliberately has no default OCR region. Prove that the
        # first request is rejected, then explicitly select only this known
        # synthetic fixture's subtitle area through the public command.
        assert b.document.project.settings.get('roi') is None, b.state
        assert not b.command('ocr.start',{}), b.state
        missing_roi_error=b.state['error']
        assert missing_roi_error=='请先自动选择或手动框选 OCR 区域。', b.state
        assert not b._task['running'], b.state
        assert (b.document.project.video['width'],b.document.project.video['height'])==(960,540)
        fixture_roi=[0,300,960,500]
        assert b.command('roi.set',{'x':0.,'y':300/540,'width':1.,'height':200/540}), b.state
        assert b.document.project.settings['roi']==fixture_roi, b.state
        assert b.state['error']=='', b.state
        b.document.project.settings.update(engine='rapidocr',language='jpn+eng',sample_fps=3,api_enabled=True)
        # A fabricated test ledger demonstrates OCR does not reset spent calls.
        seed=Cache(out/'synthetic-budget.sqlite')
        seed.reserve_call('synthetic-already-spent',50)
        records=seed.records();seed.close()
        b.document.project.api_records=records
        with patch('lyricsync.pipeline.suggest',side_effect=AssertionError('OCR must not call a provider')) as suggest:
            assert b.command('ocr.start',{}), b.state
            wait(lambda:not b._task['running'])
            suggest.assert_not_called()
        project=b.document.project
        assert project.mappings and b.stage=='review', b.state
        assert set(project.inputs)=={'video'}
        assert project.api_records==records
        assert all(mapping_text(project,m)=='' and not m.confirmed for m in project.mappings)
        observations=[{'id':m.id,'start':m.start,'end':m.end,'visual_ids':m.visual_ids,
                       'original_translation_empty':True} for m in project.mappings]
        # Test-only authoring, never substitute these strings for real lyrics.
        for i,m in enumerate(list(project.mappings),1):
            assert b.command('row.edit',{'id':m.id,'text':f'合成流程测试中文第{i}句'})
            assert not m.confirmed
        assert all(t.text=='' for t in project.translations)
        target=out/'synthetic-video-only.lyria'
        assert b.command('project.saveAs',{'path':str(target)})
        wait(lambda:target.exists() and b.document.saved_revision==b.document.revision)
        restored=load_document(target)
        assert [mapping_text(restored.project,m) for m in restored.project.mappings]==[
            mapping_text(project,m) for m in project.mappings]
        assert restored.project.api_records==records
        ass=out/'synthetic-video-only-DRAFT.ass'
        assert b.command('export.ass',{'path':str(ass),'draft':True})
        wait(lambda:not b._task['running'])
        assert ass.is_file(), b.state
        assert 'UNREVIEWED DRAFT' in ass.read_text(encoding='utf-8-sig')
        assert not any(m.confirmed for m in project.mappings)
        assert len(parse_ass(ass).events)==len(project.mappings)
        assert hashlib.sha256(video.read_bytes()).hexdigest()==source_hash
        result={'scope':'actual local OCR + Qt Bridge synthetic video-only flow; no QML/native Windows/API acceptance',
                'elapsed_seconds':round(time.monotonic()-started,3),'observed_rows':len(observations),
                'observations':observations,'blank_before_edit':True,'no_auto_confirmation':True,
                'test_text_saved_and_reopened':True,'draft_exported':True,'api_not_called':True,
                'budget_retained':True,'source_video_unchanged':True,
                'missing_roi_rejected':True,'missing_roi_error':missing_roi_error,
                'explicit_fixture_roi':fixture_roi}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        b.shutdown()
        until=time.monotonic()+3
        while b.media_controller.worker.thread.is_alive() and time.monotonic()<until:
            app.processEvents();time.sleep(.01)


if __name__=='__main__':main(sys.argv[1])
