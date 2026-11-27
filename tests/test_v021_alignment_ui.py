"""Offscreen task state checks; no model inference is claimed."""
import time
import pytest
from test_dev9_readings_dialog import app, dialog, wait
from lyricsync.local_models import QWEN

@pytest.mark.parametrize('action',['probe','install_runtime','prepare','download','import_folder','verify','reading_probe','reading_install','readings','asr'])
@pytest.mark.parametrize('fail',[False,True])
def test_task_buttons_never_disappear(app,tmp_path,action,fail):
    d=dialog(tmp_path);d.show();app.processEvents()
    buttons=[d.pause_task,d.cancel_task,d.resume_task,d.reading_repair,d.external_button,d.edit_lyrics_button,d.prepare_button,d.asr_download]
    before=[b.isHidden() for b in buttons]
    from PySide6.QtCore import QPoint
    geometry=lambda:[(b.mapTo(d,QPoint(0,0)),b.size()) for b in buttons]
    initial_geometry=geometry()
    released=[]
    def work(cancel,progress):
        while not released and not cancel():time.sleep(.005)
        if fail:raise RuntimeError('controlled test failure')
        return {}
    d._start_task(work,action=action);app.processEvents()
    assert [b.isHidden() for b in buttons]==before
    assert geometry()==initial_geometry
    assert not d.manage_alignment_model.isEnabled()
    assert not d.alignment_model.isEnabled()
    released.append(True);wait(app,lambda:d._task is None);app.processEvents()
    assert [b.isHidden() for b in buttons]==before
    assert geometry()==initial_geometry
    assert d.manage_alignment_model.isEnabled()

def test_qwen_readings_visible_but_not_required(app,tmp_path):
    d=dialog(tmp_path)
    d.alignment_model.setCurrentIndex(d.alignment_model.findData(QWEN.id))
    d._preflight()
    assert not d.reading_group.isHidden()
    assert not d.reading_group.isEnabled()
    assert not d.reading_tool.isEnabled()
    assert not d.reading_repair.isHidden()


def test_long_errors_preserve_management_button_geometry_and_full_text(app,tmp_path):
    from PySide6.QtCore import QPoint
    d=dialog(tmp_path);d.tabs.setCurrentWidget(d.manage_scroll);d.show();app.processEvents()
    buttons=[d.reading_probe,d.reading_install,d.probe_button,d.install_runtime_button,d.cancel_task]
    geometry=lambda:[(b.mapTo(d,QPoint(0,0)),b.size()) for b in buttons]
    before=geometry();heights=(d.runtime_status.height(),d.reading_tool_status.height())
    error='完整错误说明 abc 0123456789\n'*100
    d.runtime_status.setText(error);d.reading_tool_status.setText(error)
    for _ in range(5):app.processEvents()
    assert geometry()==before
    assert heights==(d.runtime_status.height(),d.reading_tool_status.height())
    assert d.runtime_status.text()==error and d.reading_tool_status.text()==error
    assert d.runtime_status.verticalScrollBar().maximum()>0


def test_diagnostic_export_is_explicit_transient_and_cancelable(app,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox,QFileDialog
    from desktop_qt.local_alignment_settings import clean
    d=dialog(tmp_path)
    assert 'diagnostic_export_path' not in d._raw_options()
    monkeypatch.setattr(d,'_question',lambda *a:QMessageBox.StandardButton.Yes)
    chosen=str(tmp_path/'private.json')
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a:(chosen,'JSON'))
    d._choose_diagnostic_export();assert d._raw_options()['diagnostic_export_path']==chosen
    assert 'diagnostic_export_path' not in clean(d._raw_options())
    d._clear_diagnostic_export();assert 'diagnostic_export_path' not in d._raw_options()
