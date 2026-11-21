"""Session/job/revision guard tests. Offline synthetic candidates, not model QA."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from copy import deepcopy
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from lyricsync.model import Project, Mapping
from lyricsync.frames import FrameIndex
from lyricsync.candidate_tracks import capture_source, candidate_from_project, prepare_apply
from lyricsync.project_document import write_document, load_document
from desktop_qt.controller import Bridge
from desktop_qt.candidate_dialog import CandidateDialog, readable_time


@pytest.fixture
def bridge(tmp_path):
    app=QApplication.instance() or QApplication([])
    b=Bridge(storage_dir=tmp_path/'storage')
    yield b
    b.shutdown();b.wait_for_shutdown(app)


def wait(predicate):
    app=QApplication.instance();deadline=time.monotonic()+8
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.003)
    assert predicate()


def ready(b,mode='ocr'):
    assert b.command('project.newEntry',{'mode':mode})
    b.document.project.video={'width':320,'height':180,'duration':4.}
    b.media_controller.index=FrameIndex([n*.1 for n in range(40)],4.)
    b._changed()


def generate(b,*,source='ocr',gate=None,started=None,incomplete=False,cancelled=False):
    detached=deepcopy(b.document);index=deepcopy(b.media_controller.index)
    def work(cancel,progress):
        snapshot=capture_source(detached,source,index)
        if started:started.set()
        if gate:assert gate.wait(5)
        generated=Project.from_dict(detached.project.to_dict())
        if generated.mappings:
            for n,row in enumerate(generated.mappings):row.start=.2+n;row.end=.8+n
        else:
            generated.mappings=[Mapping('occurrence-1',[],text_override='same chorus',start=.2,end=.8),
                                Mapping('occurrence-2',[],text_override='same chorus',start=1.2,end=1.8)]
        if incomplete:generated.mappings[-1].start=generated.mappings[-1].end=None
        return candidate_from_project(snapshot,generated)
    b._start_candidate_job('test local result',source,work,
                           lambda record,error: b._candidate_install(record,initial_complete=not cancelled) if not error else None)


def settled(b):
    wait(lambda:not b.jobs.active and not b._handlers)


@pytest.mark.parametrize('mode,source',[('ocr','ocr'),('audio','vocal')])
def test_new_entry_initial_result_atomic_undo_save_reload_and_later_candidate(bridge,tmp_path,mode,source):
    ready(bridge,mode);before=deepcopy(bridge.document.project.to_dict());depth=len(bridge.history.undo_stack)
    generate(bridge,source=source);settled(bridge)
    assert len(bridge.document.main_timeline)==2
    assert len(bridge.history.undo_stack)==depth+1
    assert bridge._candidate_source=='main' and bridge.stage=='review'
    assert [m.text_override for m in bridge.document.main_timeline]==['same chorus','same chorus']
    assert len({m.id for m in bridge.document.main_timeline})==2
    path=tmp_path/'initialized.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
    loaded=load_document(path)
    assert loaded.project.to_dict()==bridge.document.project.to_dict()
    assert loaded.source_candidates==bridge.document.source_candidates
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before
    # Undo itself never applies anything. A newly requested vocal run on an empty
    # axis is again safe in v0.22; OCR keeps its historical one-session authority.
    generate(bridge,source=source);settled(bridge)
    if source=='vocal':assert len(bridge.document.main_timeline)==2
    else:
        assert not bridge.document.main_timeline
        assert bridge.command('history.redo',{}) and len(bridge.document.main_timeline)==2


def test_imported_pristine_seed_uses_exact_ids_not_repeated_text(bridge,tmp_path):
    ready(bridge,'audio')
    source=tmp_path/'repeat.txt';source.write_text('repeat\nrepeat',encoding='utf-8')
    assert bridge.command('input.import',{'role':'translation','path':str(source)})
    settled(bridge)
    identities=[m.id for m in bridge.document.main_timeline]
    assert len(identities)==2 and len(set(identities))==2
    generate(bridge,source='vocal');settled(bridge)
    assert [m.id for m in bridge.document.main_timeline]==identities
    assert all(m.start is None for m in bridge.document.main_timeline)
    record=bridge.document.source_candidates[-1]
    plan=prepare_apply(bridge.document,record['id'],[r['id'] for r in record['rows']],frame_index=bridge.media_controller.index)
    bridge.candidate_apply(plan)
    assert [m.start for m in bridge.document.main_timeline]==[.2,1.2]


@pytest.mark.parametrize('interruption',['edit-delete','mode','settings','cancel','partial-cancel','different-source'])
def test_interrupted_or_ineligible_results_never_initialize(bridge,interruption):
    ready(bridge)
    gate=threading.Event();started=threading.Event()
    generate(bridge,gate=gate,started=started,incomplete=interruption=='incomplete',
             cancelled=interruption=='partial-cancel',source='vocal' if interruption=='different-source' else 'ocr')
    wait(started.is_set)
    if interruption=='edit-delete':
        bridge.document.project.mappings=[Mapping('manual',[],text_override='my edit')];bridge._changed()
        bridge.document.project.mappings=[];bridge._changed()
    elif interruption=='mode':
        assert not bridge.command('editor.start',{'mode':'audio'})
        assert bridge._entry_mode=='ocr'
        bridge.cancelCandidateJob()  # busy switching is now refused; cancel invalidates late initialization
    elif interruption=='settings':bridge.document.project.settings['sample_fps']=5;bridge._changed()
    elif interruption=='cancel':bridge.cancelCandidateJob()
    before=deepcopy(bridge.document.project.to_dict());gate.set();settled(bridge)
    assert bridge.document.project.to_dict()==before
    assert bridge.document.source_candidates


def test_editor_change_while_initial_verification_runs_is_not_overwritten(bridge,monkeypatch):
    import desktop_qt.entry_initialization as entry
    ready(bridge);gate=threading.Event();started=threading.Event();real=entry.prepare_apply
    def verify(*args,**kwargs):
        started.set();assert gate.wait(5);return real(*args,**kwargs)
    monkeypatch.setattr(entry,'prepare_apply',verify)
    generate(bridge);wait(started.is_set)
    bridge.document.project.mappings=[Mapping('manual',[],text_override='keep me')];bridge._changed()
    before=deepcopy(bridge.document.project.to_dict());gate.set();settled(bridge)
    assert bridge.document.project.to_dict()==before


def test_cancel_initial_verification_does_not_apply(bridge,monkeypatch):
    import desktop_qt.entry_initialization as entry
    ready(bridge);gate=threading.Event();started=threading.Event();real=entry.prepare_apply
    def verify(*args,**kwargs):
        started.set();assert gate.wait(5);return real(*args,**kwargs)
    monkeypatch.setattr(entry,'prepare_apply',verify)
    generate(bridge);wait(started.is_set)
    assert bridge._candidate_job and bridge.state['candidateRunning']
    bridge.cancelCandidateJob();gate.set();settled(bridge)
    assert not bridge.document.main_timeline and bridge.document.source_candidates


def test_explicit_preview_takes_ownership_from_pending_initialization(bridge,monkeypatch):
    import desktop_qt.entry_initialization as entry
    ready(bridge);gate=threading.Event();started=threading.Event();real=entry.prepare_apply
    def verify(*args,**kwargs):
        started.set();assert gate.wait(5);return real(*args,**kwargs)
    monkeypatch.setattr(entry,'prepare_apply',verify)
    generate(bridge);wait(started.is_set)
    record=bridge.document.source_candidates[-1];plans=[]
    bridge.candidate_preview(record['id'],[r['id'] for r in record['rows']],{},True,lambda p,e:plans.append(p))
    wait(lambda:bool(plans));gate.set();settled(bridge)
    assert not bridge.document.main_timeline
    bridge.candidate_apply(plans[0]);assert len(bridge.document.main_timeline)==2


def test_same_candidate_occurrence_cannot_be_added_twice_with_separate_previews(bridge):
    ready(bridge);bridge._entry_session['eligible']=False
    generate(bridge);settled(bridge);record=bridge.document.source_candidates[-1]
    ids=[r['id'] for r in record['rows']]
    first=prepare_apply(bridge.document,record['id'],ids,frame_index=bridge.media_controller.index,add_unmapped=True)
    second=prepare_apply(bridge.document,record['id'],ids,frame_index=bridge.media_controller.index,add_unmapped=True)
    bridge.candidate_apply(first);before=deepcopy(bridge.document.project.to_dict())
    with pytest.raises(ValueError,match='已添加到主轴'):bridge.candidate_apply(second)
    assert bridge.document.project.to_dict()==before
    again=prepare_apply(bridge.document,record['id'],ids,frame_index=bridge.media_controller.index,add_unmapped=True)
    assert all('已加入主轴' in e['blocked'] for e in again['entries'])
    # Repeated text in different immutable candidate occurrences was retained.
    assert len(bridge.document.main_timeline)==2


@pytest.mark.parametrize('change',['source','clock'])
def test_changed_source_or_local_frame_axis_blocks_initialization(bridge,tmp_path,change):
    ready(bridge)
    media=tmp_path/'source.mp4';media.write_bytes(b'controlled fixture')
    bridge.document.project.inputs['video']=str(media)
    gate=threading.Event();started=threading.Event()
    generate(bridge,gate=gate,started=started);wait(started.is_set)
    if change=='source':media.write_bytes(b'changed controlled fixture')
    else:bridge.media_controller.index.stamps[1]=.11
    gate.set();settled(bridge)
    assert not bridge.document.main_timeline and bridge.document.source_candidates


def test_pristine_seed_with_structural_missing_associations_stays_candidate(bridge,tmp_path):
    ready(bridge)
    source=tmp_path/'seed.txt';source.write_text('seed one\nseed two',encoding='utf-8')
    assert bridge.command('input.import',{'role':'translation','path':str(source)});settled(bridge)
    before=deepcopy(bridge.document.project.to_dict())
    snapshot=deepcopy(bridge.document);index=deepcopy(bridge.media_controller.index)
    def work(cancel,progress):
        captured=capture_source(snapshot,'ocr',index)
        generated=Project(mappings=[Mapping('unknown-structure',[],text_override='seed one seed two',start=.2,end=.8)])
        return candidate_from_project(captured,generated)
    bridge._start_candidate_job('structural fixture','ocr',work,lambda r,e:bridge._candidate_install(r))
    settled(bridge)
    assert bridge.document.project.to_dict()==before
    assert bridge.document.source_candidates


def test_opened_empty_document_never_acquires_new_entry_authority(bridge,tmp_path):
    path=tmp_path/'empty.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
    assert bridge.command('project.open',{'path':str(path)});settled(bridge)
    assert bridge.command('editor.start',{'mode':'ocr'})
    bridge.media_controller.index=FrameIndex([n*.1 for n in range(40)],4.)
    generate(bridge);settled(bridge)
    assert not bridge.document.main_timeline


def test_new_entry_unsaved_cancel_and_later_discard(bridge):
    bridge.document.project.mappings=[Mapping('manual',[],text_override='unsaved')];bridge._changed()
    identity=bridge.document.id;before=deepcopy(bridge.document.project.to_dict())
    assert not bridge.command('project.newEntry',{'mode':'ocr'})
    assert bridge.command('close.resolve',{'choice':'cancel'})
    assert bridge.document.id==identity and bridge.document.project.to_dict()==before
    assert not bridge.command('project.newEntry',{'mode':'ocr'})
    assert bridge.command('close.resolve',{'choice':'discard'})
    assert bridge.document.id!=identity and bridge._entry_session['eligible']
    assert bridge._entry_session['mode']=='ocr' and not bridge._candidate_job


def test_no_autorun_from_entry_and_direct_edit_import_is_main(bridge,tmp_path,monkeypatch):
    real=bridge._start_job
    monkeypatch.setattr(bridge,'_start_job',lambda *a,**k:pytest.fail('entry must not start a worker'))
    assert bridge.command('project.newEntry',{'mode':'edit'})
    monkeypatch.setattr(bridge,'_start_job',real)
    source=tmp_path/'lyrics.txt';source.write_text('manual lyric',encoding='utf-8')
    assert bridge.command('input.import',{'role':'translation','path':str(source)});settled(bridge)
    assert len(bridge.document.main_timeline)==1
    assert not bridge.document.source_candidates


@pytest.mark.parametrize('roi',[None,[],[0,0,0,5],[0,0,321,10],[-1,0,10,10],[0,0,float('nan'),10],[0,0,10]])
def test_invalid_roi_rejected_before_worker(bridge,monkeypatch,roi):
    ready(bridge);bridge.document.project.inputs['video']='fixture.mp4';bridge.document.project.settings['roi']=roi
    monkeypatch.setattr(bridge,'_start_job',lambda *a,**k:pytest.fail('invalid ROI started OCR'))
    assert not bridge.command('ocr.start',{})
    assert '请先框选画面中的字幕识别区域' in bridge.state['error']


def test_readable_dialog_keeps_ids_hidden_and_explicit_new_preview_works(bridge,monkeypatch):
    ready(bridge);bridge._entry_session['eligible']=False
    generate(bridge);settled(bridge)
    record=bridge.document.source_candidates[-1]
    before=deepcopy(bridge.document.project.to_dict())
    dialog=CandidateDialog(bridge,record);dialog.show()
    assert dialog.table.columnCount()==4 and 'occurrence-' not in dialog.table.item(0,0).text()
    assert dialog.apply.isEnabled() and dialog.chosen()==[0,1]
    dialog.reject()
    assert bridge.document.project.to_dict()==before
    bridge.switchCandidateSource('ocr')
    dialog=CandidateDialog(bridge,record);dialog.show();dialog.commit()
    wait(lambda:not dialog.alive)
    assert len(bridge.document.main_timeline)==2 and bridge._candidate_source=='main'
    assert readable_time(None)=='未打轴'
    assert readable_time(59.999999999)=='1:00.000'


@pytest.mark.parametrize('draft',[False,True])
def test_vocal_entry_dispatches_confirmed_draft_without_persisting_it(bridge,monkeypatch,draft):
    """Dispatch contract only. The 4B worker/model is owned by the model branch."""
    import sys
    from types import SimpleNamespace
    import lyricsync.local_alignment as alignment
    import desktop_qt.local_alignment_dialog as dialogs
    from desktop_qt import local_alignment_settings
    from PySide6.QtWidgets import QDialog
    ready(bridge,'audio');calls=[]
    options={'runtime_python':'controlled-python'}
    if draft:options['transcript_draft']={'confirmed':True,'text':'test draft, not actual ASR'}
    class Dialog:
        def __init__(self,*a,**k):pass
        def exec(self):return QDialog.Accepted
        def options(self):return options
    def worker(kind):
        def run(document,index,**kwargs):
            calls.append((kind,kwargs['options']))
            generated=Project(mappings=[Mapping('new-draft-id',[],text_override='same chorus',start=.2,end=.8,
                                                method='asr_confirmed_source' if kind=='draft' else 'local_vocal_qwen')])
            return candidate_from_project(capture_source(document,'vocal',index),generated)
        return run
    monkeypatch.setattr(dialogs,'LocalAlignmentDialog',Dialog)
    monkeypatch.setattr(alignment,'generate_vocal_candidate',worker('existing'))
    monkeypatch.setitem(sys.modules,'lyricsync.local_transcription',SimpleNamespace(generate_confirmed_draft_candidate=worker('draft')))
    assert bridge.command('vocal.start',{});settled(bridge)
    assert calls==[('draft' if draft else 'existing',options)]
    assert 'transcript_draft' not in local_alignment_settings.load(bridge.storage_dir)
    assert len(bridge.document.main_timeline)==1
    assert bridge._candidate_source=='main'
    assert bridge.document.main_timeline[0].start==.2
    assert bridge.document.source_candidates[-1]['rows'][0].get('text_role','translation')==('source_asr' if draft else 'translation')


def test_dev6_initial_missing_times_remain_editable_without_discarding_timed_rows(bridge):
    ready(bridge)
    generate(bridge,incomplete=True);settled(bridge)
    rows=bridge.document.main_timeline
    assert len(rows)==2
    assert (rows[0].start,rows[0].end)==(.2,.8)
    assert rows[1].start is None and rows[1].end is None
    assert rows[1].text_override=='same chorus'
    assert bridge.stage=='review' and bridge._candidate_source=='main'
    assert bridge.command('history.undo',{})
    assert not bridge.document.main_timeline
