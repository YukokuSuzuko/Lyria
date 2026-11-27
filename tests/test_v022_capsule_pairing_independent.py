"""Independent capsule review: real production Bridge/History, offscreen only."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
import pytest
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from desktop_qt.capsule_pairing_dialog import CapsulePairingDialog
from lyricsync.capsule_pairing import operate,state
from tests.test_v022_capsule_pairing import sample
from tools.qt_event_loop_wait import wait_until


def test_real_controller_capsule_history_roundtrip(tmp_path):
    app=QApplication.instance() or QApplication([])
    b=Bridge(storage_dir=tmp_path/'recovery')
    d=None
    try:
        assert b._adopt_project(sample())
        b._entry_mode='edit'; b.selected='m1'; b.stage='review'
        b._changed()
        before=b.document.project.to_dict()
        d=CapsulePairingDialog(b);d.show();app.processEvents()
        assert d.change('source',ids=['m1'])
        grouped=d._draft.to_dict()
        gid=state(d._draft)['groups'][0]['id']
        assert d.change('bind',ids=['t1'],target=gid)
        bound=d._draft.to_dict()
        d.history(False)
        assert d._draft.to_dict()==grouped
        assert b.editor.project is b.document.project
        d.history(False)
        assert d._draft.to_dict()==before
        d.history(True); assert d._draft.to_dict()==grouped
        d.history(True); assert d._draft.to_dict()==bound
        assert d.change('remove',ids=[gid])
        removed=d._draft.to_dict()
        assert state(d._draft)['groups']==[]
        d.history(False); assert d._draft.to_dict()==bound
        d.history(True); assert d._draft.to_dict()==removed
        assert b.editor.project is b.document.project
        assert b.document.project.to_dict()==before
        d.history(False);d.apply()
        assert b.document.project.to_dict()==bound
        b._history(False);assert b.document.project.to_dict()==before
    finally:
        if d is not None:d.close();d.deleteLater()
        b.shutdown()
        wait_until(lambda:not b.media_controller.worker.thread.is_alive(),timeout_ms=3000)
        app.processEvents()


def test_review_repros_are_rejected_or_recoverable():
    p=operate(sample(),'source',ids=['m1']);gid=state(p)['groups'][0]['id']
    p=operate(p,'bind',ids=['t1'],target=gid)
    p.mappings[0].text_override='MANUAL CORRECTION'
    with pytest.raises(ValueError):operate(p,'bind',ids=['t2'],target=gid)
    p=operate(sample(),'source',ids=['m1']);gid=state(p)['groups'][0]['id']
    p=operate(p,'bind',ids=['t1'],target=gid)
    p.mappings[0].reasons.append('LATER_REVIEW_REASON')
    with pytest.raises(ValueError):operate(p,'bind',ids=['t2'],target=gid)
    with pytest.raises(ValueError):operate(p,'remove',ids=[gid])
    del p.mappings[0]
    recovered=operate(p,'remove',ids=[gid])
    assert state(recovered)['groups']==[]
    assert 't1' in recovered.settings['pending_translation_ids']
