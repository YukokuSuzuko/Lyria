"""Offscreen buttons/task lifecycle; explicit fake responses are not inference."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from pathlib import Path
import time
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from lyricsync.model import Project, Lyric
from lyricsync.project_document import ProjectDocument
from lyricsync.local_models import NEXTFIRE
from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
from desktop_qt.local_model_tasks import model_tasks


class MemoryClipboard:
    """Python-path clipboard only; never obtains the OS QClipboard object."""
    def __init__(self):self.value=''
    def setText(self,text):self.value=text
    def text(self):return self.value


@pytest.fixture(autouse=True)
def memory_clipboard(monkeypatch):
    clipboard=MemoryClipboard()
    monkeypatch.setattr(QApplication,'clipboard',staticmethod(lambda:clipboard))
    return clipboard


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    service = model_tasks()
    service.shutdown(); app.processEvents()
    for widget in app.topLevelWidgets():
        if isinstance(widget, LocalAlignmentDialog): widget.reject(); widget.deleteLater()
    service.deleteLater(); delattr(app, '_lyria_model_tasks')
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def wait(app, condition):
    deadline = time.monotonic()+5
    while not condition() and time.monotonic()<deadline:
        app.processEvents(); time.sleep(.01)
    assert condition()


def dialog(tmp_path):
    doc = ProjectDocument(Project(lyrics=[Lyric('l1', '桜', None, None), Lyric('l2', '雪', None, None)]))
    result = LocalAlignmentDialog(doc, initial_options={'cache_root':str(tmp_path), 'model_id':NEXTFIRE.id})
    return result


def enable_fake_reading_tool(d):
    # This suite injects a fake worker; model the preceding successful probe explicitly.
    d._reading_ready['pykakasi']=True
    n=d.reading_tool.findData('pykakasi');d.reading_tool.model().item(n).setEnabled(True)
    d.reading_tool.setCurrentIndex(n);d._busy_state()


def test_non_download_task_keeps_pause_visible_but_disabled(app, tmp_path):
    d = dialog(tmp_path)
    def work(cancel, progress):
        while not cancel(): time.sleep(.005)
        raise InterruptedError('cancelled')
    d._start_task(work, action='readings')
    assert not d.pause_task.isHidden() and not d.pause_task.isEnabled()
    d._cancel_task(); wait(app, lambda: d._task is None)


def test_external_source_reopens_from_project_scoped_snapshot(app, tmp_path):
    from desktop_qt.local_alignment_settings import save_source_state
    d = dialog(tmp_path)
    QApplication.clipboard().setText('first\nfirst')
    d.paste_button.click()
    saved = d.source_state()
    doc = deepcopy(d.document)
    assert save_source_state(doc.project, saved)
    d.reject()
    reopened = LocalAlignmentDialog(doc, initial_options={'cache_root':str(tmp_path)})
    assert reopened.text_source.currentData() == 'external'
    assert reopened._source_rows() == saved['external_rows']
    assert reopened._source_rows()[0]['id'] != reopened._source_rows()[1]['id']


def test_reading_outcome_does_not_overwrite_edit_made_while_running(app, tmp_path, monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    d = dialog(tmp_path)
    enable_fake_reading_tool(d)
    monkeypatch.setattr(runtime, 'validate_runtime_path', lambda path: Path('test-python.exe'))
    released = []
    def worker(request, python, **kwargs):
        while not released and not kwargs['cancel'](): time.sleep(.005)
        return {'kind':'reading_rows', 'rows':[dict(row, reading='suggested') for row in request['rows']]}
    monkeypatch.setattr(runtime, 'run_worker', worker)
    d.reading_button.click()
    d.reading_table.item(0,1).setText('manual edit')
    released.append(True)
    wait(app, lambda: d._task is None)
    assert d.reading_table.item(0,1).text() == 'manual edit'
    assert d.reading_table.item(1,1).text() == 'suggested'


def test_nextfire_start_reads_first_then_requires_review(app, tmp_path, monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    d = dialog(tmp_path)
    enable_fake_reading_tool(d)
    monkeypatch.setattr(runtime, 'validate_runtime_path', lambda path: Path('test-python.exe'))
    calls = []
    def worker(request, python, **kwargs):
        calls.append(request)
        return {'kind':'reading_rows', 'rows':[dict(row, reading='sakura') for row in request['rows']]}
    monkeypatch.setattr(runtime, 'run_worker', worker)
    d._accept(); wait(app, lambda: d._task is None)
    assert calls[0]['operation'] == 'readings'
    assert d.result() != d.DialogCode.Accepted
    assert '核对' in d.status.text()


def test_reading_error_exposes_specific_dependency_help(app, tmp_path):
    d = dialog(tmp_path)
    d._outcome(None, '自动注音需要 pykakasi 2.3.0')
    assert not d.reading_repair.isHidden()
    assert 'pykakasi' in d.status.text()


def test_readings_cancel_discards_late_result(app, tmp_path, monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    d=dialog(tmp_path)
    monkeypatch.setattr(runtime,'validate_runtime_path',lambda path:Path('test-python.exe'))
    def worker(request, python, **kwargs):
        while not kwargs['cancel']():time.sleep(.005)
        return {'kind':'reading_rows','rows':[dict(row,reading='late') for row in request['rows']]}
    monkeypatch.setattr(runtime,'run_worker',worker)
    d.reading_button.click();d._cancel_task();wait(app,lambda:d._task is None)
    assert all(not row['reading'] for row in d._current_readings())
    assert '取消' in d.status.text()


def test_switching_source_retains_user_reading_by_id_and_exact_text(app,tmp_path):
    d=dialog(tmp_path)
    d.reading_table.item(0,1).setText('my sakura')
    QApplication.clipboard().setText('別の原詞')
    d.paste_button.click()
    d.text_source.setCurrentIndex(d.text_source.findData('reference'))
    assert d._current_readings()[0]['reading']=='my sakura'


def test_saved_source_survives_project_save_reopen_and_does_not_enter_global_settings(app,tmp_path):
    from desktop_qt.local_alignment_settings import save_source_state, clean
    from lyricsync.project_document import write_document, load_document
    d=dialog(tmp_path)
    QApplication.clipboard().setText('同じ\n同じ');d.paste_button.click()
    d.reading_table.item(0,1).setText('onaji')
    state=d.source_state();doc=deepcopy(d.document)
    assert save_source_state(doc.project,state)
    path=tmp_path/'saved.lyria';write_document(doc.snapshot(explicit_save=True),path)
    reopened=LocalAlignmentDialog(load_document(path),initial_options={'cache_root':str(tmp_path)})
    assert reopened._external_rows==state['external_rows']
    assert reopened._current_readings()[0]['reading']=='onaji'
    assert 'external_rows' not in clean(state)


def test_reading_dependency_missing_and_cancel_are_explicit(monkeypatch):
    import sys
    from lyricsync.japanese_readings import prepare_reading_rows
    monkeypatch.setitem(sys.modules,'pykakasi',None)
    with pytest.raises(RuntimeError,match='pykakasi'):
        prepare_reading_rows([dict(id='one',text='桜')])
    supplied=[dict(id='one',text='桜',reading='sakura')]
    assert prepare_reading_rows(supplied)[0]['reading']=='sakura'
    with pytest.raises(InterruptedError):
        prepare_reading_rows(supplied,cancel=lambda:True)


def test_separation_defaults_to_complete_audio(app,tmp_path):
    from desktop_qt.separation_dialog import SeparationDialog
    d=SeparationDialog(initial_options={'cache_root':str(tmp_path)})
    assert d.options()['duration_seconds'] is None and d.options()['offset_seconds']==0.
    assert not d.duration.isEnabled()
    d.whole_media.setChecked(False);d.duration.setValue(430.)
    assert d.options()['duration_seconds']==430.
    d.reject();d.deleteLater()
