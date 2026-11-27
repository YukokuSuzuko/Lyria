"""Offscreen real dialog regression; worker output is an explicit fake."""
from copy import deepcopy
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import Qt
from test_dev9_readings_dialog import app, dialog, memory_clipboard, enable_fake_reading_tool
from lyricsync.bundled_reading import BUNDLED_RUNTIME

def test_reset_transaction_success_and_undo(app,tmp_path,monkeypatch):
    d=dialog(tmp_path)
    enable_fake_reading_tool(d)
    d.reading_table.item(0,1).setText('sakura')
    d.reading_table.item(0,3).setCheckState(Qt.CheckState.Checked)
    before=d._current_readings()
    captured=[]
    monkeypatch.setattr(d,'_reading_python',lambda:BUNDLED_RUNTIME)
    monkeypatch.setattr(d,'_question',lambda *a:QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(d,'_start_task',lambda work,action:captured.append(work))
    def worker(request,path,**kwargs):
        assert request['reset_readings'] is True
        rows=deepcopy(request['rows'])
        for row in rows:
            row.update(reading='yuki',reading_origin='automatic_suggestion')
            row.pop('reading_confirmed_for',None)
        return {'rows':rows}
    monkeypatch.setattr('lyricsync.local_alignment_runtime.run_worker',worker)
    d._reset_readings()
    assert d._current_readings()==before
    result=captured[0](lambda:False,lambda *a:None)
    d._service.action='readings'
    d._outcome(result,None)
    assert d.reading_table.item(0,1).text()=='yuki'
    assert d.reading_table.item(0,3).checkState()==Qt.CheckState.Unchecked
    d._undo_reading_reset()
    assert d._current_readings()==before

def test_reset_worker_error_preserves_manual(app,tmp_path,monkeypatch):
    d=dialog(tmp_path)
    enable_fake_reading_tool(d)
    d.reading_table.item(0,1).setText('sakura')
    before=d._current_readings()
    monkeypatch.setattr(d,'_reading_python',lambda:BUNDLED_RUNTIME)
    monkeypatch.setattr(d,'_question',lambda *a:QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(d,'_start_task',lambda *a,**kw:None)
    d._reset_readings()
    d._service.action='readings'
    d._outcome(None,'fake worker failure')
    assert d._current_readings()==before

def test_bundled_probe_through_real_service_context(app,tmp_path,monkeypatch):
    from test_dev9_readings_dialog import wait
    d=dialog(tmp_path)
    monkeypatch.setattr(d,'_reading_python',lambda:BUNDLED_RUNTIME)
    monkeypatch.setattr(d,'_maybe_generate_readings',lambda:None)
    d._reading_ready.clear()
    d._start_task(lambda cancel,progress:{'reading_runtime_python':BUNDLED_RUNTIME,
        'reading_tool':{'tool':'sudachi','ready':True,'version':'0.6.11'}},action='reading_probe')
    wait(app,lambda:d._task is None)
    assert d._service.context['effective_reading_python']==BUNDLED_RUNTIME
    assert d._reading_ready['sudachi'] is True
    assert '旧探测' not in d.status.text()
