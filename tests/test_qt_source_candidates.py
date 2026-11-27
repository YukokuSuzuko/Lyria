"""Real Qt bridge lifecycle for source-only proposals, without model/API calls."""
import os,time,threading
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
import pytest
from copy import deepcopy
from PySide6.QtWidgets import QApplication,QMessageBox
from desktop_qt.controller import Bridge
from desktop_qt.candidate_dialog import CandidateDialog
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping,Project
from lyricsync.candidate_tracks import capture_source,candidate_from_project,prepare_apply
from lyricsync.project_document import load_document

@pytest.fixture
def bridge(tmp_path):
    app=QApplication.instance() or QApplication([])
    obj=Bridge(storage_dir=tmp_path/'storage');obj.document.project.video={'duration':4.0}
    obj.document.project.mappings=[Mapping('a',[],text_override='chorus',start=.2,end=1.),Mapping('b',[],text_override='chorus',start=1.2,end=2.)]
    obj.media_controller.index=FrameIndex([n*.1 for n in range(40)],4.)
    obj.selected='b';obj._changed()
    yield obj
    obj.shutdown();obj.wait_for_shutdown(app)

def wait(predicate):
    app=QApplication.instance();end=time.monotonic()+8
    while not predicate() and time.monotonic()<end:app.processEvents();time.sleep(.003)
    assert predicate()

def candidate(bridge):
    captured=capture_source(deepcopy(bridge.document),'ocr',bridge.media_controller.index)
    generated=Project.from_dict(bridge.document.project.to_dict());generated.mappings[0].start=.4;generated.mappings[1].start=1.4
    record=candidate_from_project(captured,generated);bridge._candidate_install(record);return record

def test_source_switch_is_read_only_and_keeps_playhead_selection_and_empty_gaps(bridge):
    record=candidate(bridge);before=bridge.document.project.to_dict();media=bridge.media_controller.snapshot()
    bridge.switchCandidateSource('ocr')
    assert bridge.selected=='b' and bridge.state['candidateReadOnly']
    assert bridge._display_project().mappings[0].start==.4
    assert bridge.media_controller.snapshot()['positionSeconds']==media['positionSeconds']
    for command,payload in [('row.edit',{'id':'a','text':'bad'}),('timeline.move',{'id':'a','delta':1}),('history.undo',{}),('export.ass',{}),('settings.set',{'key':'font_size','value':80})]:
        assert not bridge.command(command,payload)
    assert bridge.document.project.to_dict()==before
    bridge.switchCandidateSource('vocal')
    assert not bridge.state['candidateAvailable']
    assert all(r['first']==0 and r['last']==0 for r in bridge.model.items)
    assert bridge._preview_texts==[]
    bridge.switchCandidateSource('main');assert bridge.selected=='b'
    assert bridge.document.project.to_dict()==before

def test_ocr_runs_concurrently_and_only_installs_candidate(bridge,tmp_path,monkeypatch):
    import lyricsync.pipeline as pipeline
    media=tmp_path/'source.mp4';media.write_bytes(b'fixture bytes')
    bridge.document.project.inputs['video']=str(media);bridge.document.project.settings['roi']=[0,0,10,10]
    bridge.document.project.video.update(width=320,height=180)
    entered=threading.Event();release=threading.Event()
    from lyricsync.model import Translation, Visual
    bridge.document.project.translations=[Translation('t','chorus')]
    bridge.document.project.mappings[0].translation_ids=['t']
    def run(project,*args,**kwargs):
        assert not project.mappings
        entered.set();assert release.wait(5)
        visual = Visual('seen', 'chorus', .5, 1., [1,1,9,8], .94, 3)
        project.visuals = [visual]
        project.mappings=[Mapping('new',['t'],visual_ids=[visual.id],text_override='chorus',start=.5,end=1.)]
    monkeypatch.setattr(pipeline,'run',run)
    assert bridge.command('ocr.start',{});wait(entered.is_set)
    assert not bridge.state['task']['running'] and bridge.state['candidateRunning']
    assert bridge.command('row.edit',{'id':'a','text':'edited during OCR'})
    before=bridge.document.project.to_dict();release.set();wait(lambda:bridge._candidate_job is None and not bridge._task['running'] and not getattr(bridge,'_ocr_settings_pending',False))
    assert bridge.document.project.to_dict()==before
    record=bridge.document.source_candidates[0]
    plan=prepare_apply(bridge.document,record['id'],['a'],frame_index=bridge.media_controller.index)
    assert plan['entries'][0]['blocked']
    bridge.switchCandidateSource('ocr');assert '过期' in bridge.state['candidateStatus']

def test_atomic_apply_ignore_undo_redo_save_and_reopen(bridge,tmp_path):
    record=candidate(bridge);before=bridge.document.project.to_dict()
    bridge.document.project.settings['manual_locked_ids']=['b']
    plan=prepare_apply(bridge.document,record['id'],['a','b'],frame_index=bridge.media_controller.index)
    with pytest.raises(ValueError):bridge.candidate_apply(plan)
    assert bridge.document.project.mappings[0].start==.2
    bridge.document.project.settings.pop('manual_locked_ids')
    plan=prepare_apply(bridge.document,record['id'],['a','b'],frame_index=bridge.media_controller.index)
    depth=len(bridge.history.undo_stack);bridge.candidate_apply(plan)
    assert len(bridge.history.undo_stack)==depth+1
    assert [r.start for r in bridge.document.main_timeline]==[.4,1.4]
    assert bridge.command('history.undo',{});assert bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{})
    bridge.candidate_ignore(record['id'],['a'])
    assert bridge.document.project.settings['candidate_review_decisions']
    assert bridge.command('history.undo',{});assert not bridge.document.project.settings.get('candidate_review_decisions')
    assert bridge.command('history.redo',{})
    path=tmp_path/'project.lyria';bridge._save(True,str(path));wait(lambda:bridge._save_running is None)
    loaded=load_document(path);assert loaded.source_candidates==bridge.document.source_candidates
    assert loaded.project.settings['candidate_review_decisions']

def test_dialog_explicit_rebind_preview_and_confirm_cancel(bridge,monkeypatch):
    record=candidate(bridge)
    record['rows'][0]['text']='new candidate wording'
    # Keep the installed immutable record identical to the test source.
    bridge.document.source_candidates[0]['rows'][0]['text']='new candidate wording'
    dialog=CandidateDialog(bridge,record);dialog.show();dialog.table.selectRow(0)
    dialog.check_visible(False)
    dialog.target.setCurrentIndex(dialog.target.findData('b'));dialog.bind_selected()
    assert dialog.chosen()==[0] and dialog._bindings[record['rows'][0]['id']]=='b'
    dialog.replace_text.setChecked(True)
    before=bridge.document.project.to_dict()
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.No)
    dialog.commit();wait(lambda:dialog.request is None)
    assert bridge.document.project.to_dict()==before
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    dialog.commit();wait(lambda:not dialog.alive)
    assert bridge.document.main_timeline[1].text_override=='new candidate wording'
    dialog.reject()

def test_continuous_vfr_bridge_uses_pts_not_average_fps(bridge):
    bridge.media_controller.index=FrameIndex([0,.04,.12,.17],.3)
    assert bridge.timelineFrameSeconds(5)==.3
    assert bridge.timelineSecondsFrame(.08)==pytest.approx(2.5)
    assert bridge.timelineSecondsFrame(.235)==pytest.approx(4.5)

def test_audio_large_arrays_are_not_copied_into_ui_state(bridge):
    bridge._audio_analysis.update(spectrum=[[1]*128]*2048,waveform=[1]*2048);bridge._refresh(False)
    assert 'spectrum' not in bridge.uiState['audioAnalysis']
    assert len(bridge.audioAnalysis['spectrum'])==2048


def test_history_capacity_cleanup_requires_confirmation_and_preserves_pending(bridge,monkeypatch):
    for _ in range(12):candidate(bridge)
    before=bridge.document.project.to_dict()
    captured=capture_source(deepcopy(bridge.document),'ocr',bridge.media_controller.index)
    record=candidate_from_project(captured,bridge.document.project)
    with pytest.raises(ValueError):bridge._candidate_install(record)
    assert bridge._candidate_pending['id']==record['id']
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.No)
    bridge.clearOldCandidates();assert len(bridge.document.source_candidates)==12
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    bridge.clearOldCandidates()
    assert len(bridge.document.source_candidates)==2
    assert bridge.document.source_candidates[-1]['id']==record['id']
    assert bridge._candidate_pending is None
    assert bridge.document.project.to_dict()==before


def test_large_dialog_has_one_searchable_target_model(bridge):
    from PySide6.QtWidgets import QComboBox
    bridge.document.project.mappings=[Mapping(str(i),[],text_override='chorus',start=i*.01,end=i*.01+.01) for i in range(3000)]
    bridge.media_controller.index=None;bridge.document.project.video={};bridge.document.touch()
    record=candidate_from_project(capture_source(deepcopy(bridge.document),'ocr'),bridge.document.project)
    dialog=CandidateDialog(bridge,record)
    assert dialog.table.rowCount()==3000
    assert len([combo for combo in dialog.findChildren(QComboBox) if combo.isEditable()])==1
    assert dialog.filter.findData('unmapped')>=0 and dialog.table.columnCount()==4
    assert dialog.target.count()==3001
    dialog.reject()


def test_documented_vocal_fixture_load_is_explicit_and_keeps_main(bridge,monkeypatch):
    from pathlib import Path
    fixture=Path(__file__).resolve().parents[1]/'examples'/'phase3_vocal_fixture.json'
    monkeypatch.setattr(bridge,'_path',lambda *args,**kwargs:str(fixture))
    before=bridge.document.project.to_dict();bridge.loadCandidateFixture()
    wait(lambda:bool(bridge.document.source_candidates))
    record=bridge.document.source_candidates[-1]
    assert record['source']=='vocal' and len(record['rows'])==3
    assert bridge.document.project.to_dict()==before
    bridge.switchCandidateSource('vocal');assert bridge.state['candidateAvailable']


def test_explicit_candidate_apply_allows_existing_human_protection(bridge):
    assert bridge.command('row.edit',{'id':'a','text':'human protected'})
    assert bridge.document.main_timeline[0].locked
    record=candidate(bridge)
    plan=prepare_apply(bridge.document,record['id'],['a'],frame_index=bridge.media_controller.index)
    assert not plan['entries'][0]['blocked']
    bridge.candidate_apply(plan)
    assert bridge.document.main_timeline[0].start==.4
    assert bridge.document.main_timeline[0].text_override=='human protected'


@pytest.fixture(autouse=True)
def accepted_settings_for_downstream_ocr_contracts(monkeypatch):
    # These older suites exercise work AFTER a user's settings acceptance.
    # Actual modal acceptance/cancel/reentrancy is covered in dev8 entry tests.
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings', lambda *args, **kwargs: True)
