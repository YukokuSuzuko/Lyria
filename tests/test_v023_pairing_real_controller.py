"""Real OCR pairing controller/history integration; private offscreen, no API/model."""
from contextlib import contextmanager
from copy import deepcopy

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QDialog

from desktop_qt.controller import Bridge
from desktop_qt.ocr_capsule_pairing_dialog import OcrCapsulePairingDialog
from lyricsync.editing import mapping_text
from lyricsync.model import Lyric, Project, Translation, Visual
from tools.qt_event_loop_wait import wait_until


def sample():
    return Project(
        visuals=[Visual('v1','原文一',1.,2.,[0,0,80,20],.9,3),
                 Visual('v2','原文二',3.,5.,[0,0,80,20],.9,3)],
        lyrics=[Lyric('reference','参考原文',None,None)],
        translations=[Translation('t1','第一句'),Translation('t2','保留未配译文')])


@contextmanager
def real_pairing(tmp_path):
    app=QApplication.instance() or QApplication([])
    bridge=None;dialogs=[]
    try:
        bridge=Bridge(storage_dir=tmp_path/'isolated-recovery')
        assert bridge._adopt_project(sample())
        bridge._entry_mode='ocr';bridge.stage='review';bridge._changed()
        def open_dialog():
            dialog=OcrCapsulePairingDialog(bridge)
            assert isinstance(dialog,OcrCapsulePairingDialog)
            dialogs.append(dialog);dialog.show();app.processEvents()
            return dialog
        yield bridge,open_dialog
    finally:
        if bridge is not None:
            bridge.shutdown();bridge.wait_for_shutdown(app)
            bridge.media_controller.worker.thread.join(10)
            assert not bridge.media_controller.worker.thread.is_alive()
        for dialog in dialogs:
            dialog.reject();destroyed=[]
            dialog.destroyed.connect(lambda:destroyed.append(True))
            dialog.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            wait_until(lambda:bool(destroyed))
        app.processEvents()


def bind_first(dialog):
    assert dialog.change('source',ids=[dialog.session.anchors[0].id])
    group=dialog.session.data['groups'][0]['id']
    assert dialog.change('bind',ids=[dialog.session.data['pieces'][0]['id']],target=group)


def test_real_ocr_pairing_apply_is_one_history_transaction_and_roundtrips(tmp_path):
    with real_pairing(tmp_path) as (bridge,open_dialog):
        before=deepcopy(bridge.document.project.to_dict())
        depth=len(bridge.history.undo_stack);revision=bridge.document.revision
        dialog=open_dialog();bind_first(dialog)
        assert bridge.document.project.to_dict()==before
        assert len(bridge.history.undo_stack)==depth and bridge.document.revision==revision
        dialog.apply()
        assert dialog.result()==QDialog.DialogCode.Accepted,dialog.status.text()
        after=deepcopy(bridge.document.project.to_dict())
        assert len(bridge.history.undo_stack)==depth+1
        assert bridge.editor.project is bridge.document.project
        assert after['visuals']==before['visuals'] and after['lyrics']==before['lyrics']
        original={item['id']:item for item in before['translations']}
        assert all(item in after['translations'] for item in original.values())
        rows=bridge.document.project.mappings
        assert [mapping_text(bridge.document.project,row) for row in rows]==['第一句','保留未配译文']
        assert (rows[0].start,rows[0].end)==(1.,2.)
        assert rows[1].start is None and rows[1].end is None
        assert bridge.command('history.undo',{})
        assert bridge.document.project.to_dict()==before
        assert len(bridge.history.undo_stack)==depth
        assert bridge.editor.project is bridge.document.project
        assert bridge.command('history.redo',{})
        assert bridge.document.project.to_dict()==after
        assert len(bridge.history.undo_stack)==depth+1
        assert bridge.editor.project is bridge.document.project


def test_real_ocr_pairing_cancel_leaves_document_revision_and_history_unchanged(tmp_path):
    with real_pairing(tmp_path) as (bridge,open_dialog):
        before=bridge.document.snapshot();past=deepcopy(bridge.history.undo_stack)
        future=deepcopy(bridge.history.redo_stack)
        dialog=open_dialog();bind_first(dialog)
        dialog.history(False);dialog.history(True);dialog.reject()
        assert dialog.result()==QDialog.DialogCode.Rejected
        assert bridge.document.snapshot()==before
        assert bridge.history.undo_stack==past and bridge.history.redo_stack==future
        assert bridge.editor.project is bridge.document.project


def test_real_ocr_pairing_locked_derived_origin_cannot_be_rebound(tmp_path):
    with real_pairing(tmp_path) as (bridge,open_dialog):
        first=open_dialog();bind_first(first);first.apply()
        assert first.result()==QDialog.DialogCode.Accepted,first.status.text()
        bridge.document.project.mappings[0].locked=True;bridge._changed()
        before=bridge.document.snapshot();depth=len(bridge.history.undo_stack)
        dialog=open_dialog()
        assert all(part['translation_id']!='t1' for piece in dialog.session.data['pieces'] for part in piece['slices'])
        response={'groups':[{'anchor_ids':[dialog.session.anchors[1].id],
                            'slices':[{'translation_id':'t1','start':0,'end':3}]}]}
        with pytest.raises(ValueError):dialog.session.load_response(response)
        dialog.reject()
        assert bridge.document.snapshot()==before and len(bridge.history.undo_stack)==depth
        assert bridge.document.project.mappings[0].locked
        assert (bridge.document.project.mappings[0].start,bridge.document.project.mappings[0].end)==(1.,2.)
