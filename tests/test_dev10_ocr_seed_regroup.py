"""Imported text -> actual OCR pipeline -> candidate -> atomic initialization.

Only scan is controlled; these tests do not measure visual recognition accuracy.
"""
from copy import deepcopy
import threading
import pytest
from lyricsync.model import Visual
from lyricsync.project_document import write_document, load_document
from lyricsync.candidate_tracks import can_regroup_imported_seed
from test_qt_entry_initialization_dev5 import bridge, ready, settled, wait


def setup(b,tmp_path):
    ready(b,'ocr')
    video=tmp_path/'video.mp4'; video.write_bytes(b'controlled scan')
    b.document.project.inputs['video']=str(video)
    b.document.project.settings['roi']=[0,0,100,30]
    for role in ('translation','source'):
        path=tmp_path/(role+'.txt');path.write_text('follow the\nlong road',encoding='utf-8')
        assert b.command('input.import',{'role':role,'path':str(path)})
        settled(b)


def run(b,monkeypatch):
    monkeypatch.setattr('lyricsync.pipeline.scan',lambda *a,**k:([Visual('v','follow the long road',1.,3.,[0,0,100,30],.99,6)],
        {'width':320,'height':180,'duration':4.}))
    b._run_ocr();settled(b)


def test_actual_first_ocr_merges_seed_once_with_undo_redo_and_reload(bridge,tmp_path,monkeypatch):
    setup(bridge,tmp_path);before=deepcopy(bridge.document.project.to_dict())
    ids=[r.id for r in bridge.document.main_timeline];depth=len(bridge.history.undo_stack)
    run(bridge,monkeypatch)
    assert bridge.stage=='review'
    assert len(bridge.document.main_timeline)==1
    row=bridge.document.main_timeline[0]
    assert set(row.lineage_ids)==set(ids)
    assert row.text_override=='follow the\nlong road'
    assert (row.start,row.end)==(1.,3.)
    assert bridge.document.project.to_dict()['translations']==before['translations']
    assert len(bridge.history.undo_stack)==depth+1
    after=deepcopy(bridge.document.project.to_dict())
    path=tmp_path/'merged.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
    assert load_document(path).project.to_dict()==after
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{})
    assert bridge.document.project.to_dict()==after


@pytest.mark.parametrize('field,value',[('human_edited',True),('locked',True),('confirmed',True),('ignored',True),('start',.2)])
def test_protected_seed_is_never_structurally_replaced(bridge,tmp_path,monkeypatch,field,value):
    setup(bridge,tmp_path)
    row=bridge.document.main_timeline[0];setattr(row,field,value)
    if field=='start':row.end=.5
    bridge._changed();before=deepcopy(bridge.document.project.to_dict())
    run(bridge,monkeypatch)
    assert bridge.document.project.to_dict()==before


def test_edit_during_regroup_verification_preserves_new_edit(bridge,tmp_path,monkeypatch):
    import desktop_qt.entry_initialization as entry
    setup(bridge,tmp_path);gate=threading.Event();started=threading.Event();real=entry.prepare_apply
    def verify(*a,**kw):
        started.set(); assert gate.wait(5); return real(*a,**kw)
    monkeypatch.setattr(entry,'prepare_apply',verify)
    monkeypatch.setattr('lyricsync.pipeline.scan',lambda *a,**k:([Visual('v','follow the long road',1.,3.,[],.99,6)],{'duration':4.}))
    bridge._run_ocr();wait(started.is_set)
    bridge.document.main_timeline[0].text_override='keep edit';bridge._changed()
    before=deepcopy(bridge.document.project.to_dict());gate.set();settled(bridge)
    assert bridge.document.project.to_dict()==before


@pytest.mark.parametrize('damage',['missing','duplicate','text','evidence'])
def test_exact_translation_partition_required(bridge,tmp_path,monkeypatch,damage):
    setup(bridge,tmp_path)
    before=deepcopy(bridge.document)
    run(bridge,monkeypatch)
    record=deepcopy(bridge.document.source_candidates[-1])
    assert can_regroup_imported_seed(before,record)
    if damage=='missing':record['rows'][0]['provenance']['translation_ids'].pop()
    if damage=='duplicate':record['rows'].append(deepcopy(record['rows'][0]))
    if damage=='text':record['rows'][0]['text']='changed translation'
    if damage=='evidence':record['evidence']['translations'][0]['text']='changed immutable source'
    assert not can_regroup_imported_seed(before,record)


def test_cancel_during_regroup_preserves_original(bridge,tmp_path,monkeypatch):
    import desktop_qt.entry_initialization as entry
    setup(bridge,tmp_path);before=deepcopy(bridge.document.project.to_dict())
    gate=threading.Event();started=threading.Event();real=entry.prepare_apply
    def verify(*a,**kw):
        started.set(); assert gate.wait(5); return real(*a,**kw)
    monkeypatch.setattr(entry,'prepare_apply',verify)
    monkeypatch.setattr('lyricsync.pipeline.scan',lambda *a,**k:([Visual('v','follow the long road',1.,3.,[],.99,6)],{'duration':4.}))
    bridge._run_ocr();wait(started.is_set);bridge.cancelCandidateJob();gate.set();settled(bridge)
    assert bridge.document.project.to_dict()==before


def test_reopened_seed_does_not_gain_first_run_authority(bridge,tmp_path,monkeypatch):
    setup(bridge,tmp_path);before=deepcopy(bridge.document.project.to_dict())
    path=tmp_path/'seed.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
    bridge.document=load_document(path);bridge._entry_reset(new=False)
    bridge._entry_select_mode('ocr');run(bridge,monkeypatch)
    assert bridge.document.project.to_dict()==before


def test_undo_does_not_rearm_regroup(bridge,tmp_path,monkeypatch):
    setup(bridge,tmp_path);before=deepcopy(bridge.document.project.to_dict());run(bridge,monkeypatch)
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before
    run(bridge,monkeypatch)
    assert bridge.document.project.to_dict()==before
