"""Explicit fake installer/probe and offscreen UI; no network or model inference."""
import json
from pathlib import Path
import pytest
from PySide6.QtWidgets import QMessageBox
from lyricsync import local_runtime_setup as setup
from test_dev9_readings_dialog import app, dialog, wait


def test_oneclick_includes_pinned_reading_dependency():
    assert 'sudachipy==0.6.11' in setup.DEPENDENCIES
    assert 'sudachidict_small==20260116' in setup.DEPENDENCIES
    assert 'pykakasi==2.3.0' not in setup.DEPENDENCIES


def test_repair_runs_only_selected_isolated_interpreter(tmp_path, monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    python=tmp_path/'env'/'Scripts'/'python.exe'
    monkeypatch.setattr(runtime,'validate_runtime_path',lambda value, **kwargs: python)
    commands=[]
    def run(args,*a,**k):
        commands.append(args)
        return json.dumps(dict(tool='pykakasi',version='2.3.0',ready=True)) if '-c' in args else ''
    monkeypatch.setattr(setup,'_run',run)
    result=setup.install_reading_tool(str(python))
    assert result['reading_tool']['ready']
    assert all(cmd[0]==str(python) for cmd in commands)
    install=commands[0]
    assert 'pykakasi==2.3.0' in install and '--only-binary=:all:' in install
    assert 'https://pypi.org/simple' in install and '--isolated' in install
    assert not any('torch' in arg for cmd in commands for arg in cmd)


def test_offline_failure_does_not_claim_ready(monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    monkeypatch.setattr(runtime,'validate_runtime_path',lambda value, **kwargs: Path('fake-python.exe'))
    def failed(*a,**k):raise RuntimeError('offline test')
    monkeypatch.setattr(setup,'_run',failed)
    with pytest.raises(RuntimeError,match='offline'):setup.install_reading_tool('selected')


def test_invalid_probe_does_not_claim_ready(monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    monkeypatch.setattr(runtime,'validate_runtime_path',lambda value, **kwargs: Path('fake-python.exe'))
    monkeypatch.setattr(setup,'_run',lambda *a,**k:'{}')
    with pytest.raises(RuntimeError,match='未能确认'):setup.probe_reading_tool('selected')


def test_custom_range_and_separation_are_accessible_on_settings_page(app,tmp_path):
    d=dialog(tmp_path)
    assert d.settings_scroll.isAncestorOf(d.custom_range) and d.settings_scroll.isAncestorOf(d.separate)
    assert not d.custom_range.isChecked() and d.range_body.isHidden()
    assert d._raw_options()['duration_seconds'] is None
    d.custom_range.setChecked(True)
    assert not d.range_body.isHidden()
    d.custom_range.setChecked(False)
    assert d.range_body.isHidden()


def test_saved_whole_song_options_do_not_reopen_custom_range(app,tmp_path):
    from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
    d=dialog(tmp_path)
    reopened=LocalAlignmentDialog(d.document,initial_options=d._raw_options())
    assert not reopened.custom_range.isChecked()


def test_manual_readings_never_start_generator(app,tmp_path,monkeypatch):
    d=dialog(tmp_path)
    d.reading_tool.setCurrentIndex(d.reading_tool.findData('manual'))
    monkeypatch.setattr(d,'_generate_readings',lambda:pytest.fail('must not generate'))
    d._accept()
    assert '手动补齐' in d.status.text()
    assert not d.reading_button.isEnabled()


def test_probe_state_is_for_selected_runtime_only(app,tmp_path,monkeypatch):
    d=dialog(tmp_path)
    d.runtime.setText('chosen')
    monkeypatch.setattr(setup,'probe_reading_tool',lambda value,**kw:dict(reading_tool=dict(tool='pykakasi',version='2.3.0',ready=True)))
    d._probe_readings();wait(app,lambda:d._task is None)
    assert '已可用' in d.reading_tool_status.text()
    d.runtime.setText('other')
    assert '尚未检测' in d.reading_tool_status.text()


def test_install_decline_leaves_environment_untouched(app,tmp_path,monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    d=dialog(tmp_path)
    monkeypatch.setattr(runtime,'validate_runtime_path',lambda value, **kwargs:Path('selected'))
    monkeypatch.setattr(d,'_question',lambda *a:QMessageBox.StandardButton.No)
    monkeypatch.setattr(setup,'install_reading_tool',lambda *a,**k:pytest.fail('must not install'))
    d._install_readings()
    assert d._task is None


def test_install_confirmation_runs_repair_and_preserves_readings(app,tmp_path,monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    d=dialog(tmp_path);d.runtime.setText('selected')
    d.reading_table.item(0,1).setText('manual sakura')
    monkeypatch.setattr(runtime,'validate_runtime_path',lambda value, **kwargs:Path('selected'))
    monkeypatch.setattr(d,'_question',lambda *a:QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(setup,'install_reading_tool',lambda *a,**k:dict(reading_tool=dict(tool='pykakasi',version='2.3.0',ready=True)))
    d._install_readings();wait(app,lambda:d._task is None)
    assert '已可用' in d.reading_tool_status.text()
    assert d.reading_table.item(0,1).text()=='manual sakura'


def test_stage_diagnostics_counts_and_never_include_text_or_paths():
    from lyricsync.local_alignment import alignment_stage_diagnostics
    record={'rows':[dict(start=1.,end=2.,sentence_id='one'), dict(start=None,end=None,sentence_id=None)]}
    response=dict(duration=4,words=[dict(text='private lyric',start_time=1,end_time=2),dict(text='secret',start_time=None,end_time=None)],chunking={'batches':[{},{}]})
    result=alignment_stage_diagnostics(response,record)
    assert result==dict(raw_words=2,raw_timed_words=1,batches=2,candidate_total=2,candidate_valid=1,candidate_missing=1,bound_rows=1,unbound_rows=1,status='partial')
    assert 'private' not in str(result) and 'secret' not in str(result)


def test_nextfire_stage_diagnostics_counts_rows_and_chunks():
    from lyricsync.local_alignment import alignment_stage_diagnostics
    record={'rows':[dict(start=1.,end=2.,sentence_id=None)]}
    result=alignment_stage_diagnostics(dict(duration=3,rows=[dict(start_time=1,end_time=2)],chunking=dict(chunks=4)),record)
    assert result['raw_words']==result['raw_timed_words']==1 and result['batches']==4


def test_lyric_pitch_decorations_are_not_model_letters():
    from lyricsync.experimental_nextfire_alignment import normalize_reading
    assert normalize_reading('a~↑~↓♪')=='a'
    with pytest.raises(ValueError):normalize_reading('aᐢ')
    with pytest.raises(ValueError):normalize_reading('a2')


def test_unconverted_reading_identifies_line_instead_of_regenerating_loop(app,tmp_path,monkeypatch):
    d=dialog(tmp_path);d.reading_table.item(0,1).setText('sakuraᐢ')
    monkeypatch.setattr(d,'_generate_readings',lambda:pytest.fail('must not loop'))
    d._accept()
    assert '第 1 句' in d.status.text() and '手动修正' in d.status.text()


def test_empty_automatic_reading_is_reported_and_never_loops(app,tmp_path,monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    d=dialog(tmp_path)
    from test_dev9_readings_dialog import enable_fake_reading_tool
    enable_fake_reading_tool(d)
    monkeypatch.setattr(runtime,'validate_runtime_path',lambda path:Path('test-python.exe'))
    monkeypatch.setattr(runtime,'run_worker',lambda request,python,**kw:dict(rows=[dict(row,reading='',reading_origin='automatic_suggestion') for row in request['rows']]))
    d.reading_button.click();wait(app,lambda:d._task is None)
    assert '第 1、2 句' in d.status.text() and '已补齐读音' not in d.status.text()
    monkeypatch.setattr(d,'_generate_readings',lambda:pytest.fail('must not loop'))
    d._accept()
    assert '第 1 句' in d.status.text() and '不会重复' in d.status.text()


def test_disabling_custom_range_also_clears_hidden_sentence_scope(app,tmp_path):
    from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
    from lyricsync.model import Project,Mapping
    from lyricsync.project_document import ProjectDocument
    doc=ProjectDocument(Project(mappings=[Mapping(str(i),[],text_override='row '+str(i)) for i in range(4)]))
    d=LocalAlignmentDialog(doc,initial_options={'cache_root':str(tmp_path),'text_source':'main'})
    d.main_source_confirm.setChecked(True)
    d.custom_range.setChecked(True);d.first.setValue(2);d.last.setValue(3)
    assert len(d._source_rows())==2
    d.custom_range.setChecked(False)
    assert len(d._source_rows())==4
    assert d._raw_options()['sentence_ids']==['0','1','2','3']
    assert d._raw_options()['duration_seconds'] is None
    assert d.range_body.isHidden()


def test_explicit_initial_subset_is_visible_not_silently_hidden(app,tmp_path):
    from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
    from lyricsync.model import Project,Mapping
    from lyricsync.project_document import ProjectDocument
    doc=ProjectDocument(Project(mappings=[Mapping(str(i),[],text_override='row '+str(i)) for i in range(4)]))
    d=LocalAlignmentDialog(doc,initial_options={'cache_root':str(tmp_path),'text_source':'main','sentence_ids':['1','2']})
    d.main_source_confirm.setChecked(True)
    assert d.custom_range.isChecked() and not d.range_body.isHidden()
    assert len(d._source_rows())==2
