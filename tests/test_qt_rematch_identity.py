"""Actual Qt command/job import/rematch/history/save regression; no API."""
import os,time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from lyricsync.project_document import load_document


def wait(app,predicate):
    deadline=time.monotonic()+8
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert predicate()


def test_import_rematch_undo_redo_save_reopen_keeps_occurrence_ids(tmp_path):
    app=QApplication.instance() or QApplication([])
    bridge=Bridge(storage_dir=tmp_path/'storage')
    try:
        source=tmp_path/'repeated.txt';source.write_text('same chorus\nsame chorus\n',encoding='utf8')
        assert bridge.command('input.import',{'role':'translation','path':str(source)})
        wait(app,lambda:not bridge._task.get('running'))
        assert len(bridge.document.main_timeline)==2
        ids=[r.id for r in bridge.document.main_timeline]
        assert len(set(ids))==2
        bridge.document.main_timeline[0].lineage_ids=['first-origin']
        bridge.document.main_timeline[1].lineage_ids=['second-origin']
        bridge._changed()
        before=[(r.id,r.translation_ids,r.lineage_ids) for r in bridge.document.main_timeline]
        depth=len(bridge.history.undo_stack)
        assert bridge.command('project.rematch',{})
        wait(app,lambda:len(bridge.document.source_candidates)==1)
        assert [(r.id,r.translation_ids,r.lineage_ids) for r in bridge.document.main_timeline]==before
        assert len(bridge.history.undo_stack)==depth
        assert [r['sentence_id'] for r in bridge.document.source_candidates[0]['rows']]==ids
        assert [(r.id,r.translation_ids,r.lineage_ids) for r in bridge.document.main_timeline]==before
        assert bridge.editor.project is bridge.document.project
        saved=tmp_path/'saved.lyria'
        bridge._save(True,str(saved))
        wait(app,lambda:bridge._save_running is None)
        reopened=load_document(saved)
        assert [(r.id,r.translation_ids,r.lineage_ids) for r in reopened.main_timeline]==before
        assert reopened.source_candidates==bridge.document.source_candidates
        assert bridge.command('project.rematch',{})
        wait(app,lambda:len(bridge.document.source_candidates)==2)
        assert [r.id for r in bridge.document.main_timeline]==ids
    finally:
        bridge.shutdown()
        deadline=time.monotonic()+3
        while bridge.media_controller.worker.thread.is_alive() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
