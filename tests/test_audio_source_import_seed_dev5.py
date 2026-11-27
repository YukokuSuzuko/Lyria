"""Actual original-import entry, never import as translation to make 4A pass."""
from copy import deepcopy
import threading
import pytest

from test_qt_entry_initialization_dev5 import bridge, settled, wait, generate
from lyricsync.editing import mapping_text
from lyricsync.model import Mapping, Translation
from lyricsync.local_alignment import request_rows
from lyricsync.project_document import write_document, load_document
from lyricsync.subtitles import import_original


def original(tmp_path, text='same chorus\nsame chorus\nlast original', suffix='.txt'):
    path=tmp_path/('original'+suffix);path.write_text(text,encoding='utf-8');return path


def import_source(b,path):
    assert b.command('input.import',{'role':'source','path':str(path)})
    settled(b)


def test_audio_source_txt_seed_exact_ids_untimed_single_undo_and_reload(bridge,tmp_path,monkeypatch):
    import lyricsync.local_alignment as alignment
    monkeypatch.setattr(alignment,'generate_vocal_candidate',lambda *a,**k:pytest.fail('import started a model'))
    assert bridge.command('project.newEntry',{'mode':'audio'})
    source=original(tmp_path);expected,_=import_original(source)
    before=deepcopy(bridge.document.project.to_dict());depth=len(bridge.history.undo_stack)
    import_source(bridge,source)
    p=bridge.document.project;rows=p.main_timeline
    assert len(rows)==3 and len({r.id for r in rows})==3
    assert [r.lyric_ids for r in rows]==[[r.id] for r in expected]
    assert [r.text_override for r in rows]==[r.text for r in expected]
    assert all(r.start is None and r.end is None and r.text_role=='source_import' and not r.translation_ids and not r.confirmed for r in rows)
    assert not p.translations and [l.__dict__ for l in p.lyrics]==[l.__dict__ for l in expected]
    assert bridge._entry_session['eligible']
    assert len(bridge.history.undo_stack)==depth+1
    requested=request_rows(bridge.document)
    assert requested==request_rows(bridge.document,text_source='lyrics')
    assert [r['id'] for r in requested]==[r.id for r in rows]
    assert requested[0]['text']==requested[1]['text'] and requested[0]['id']!=requested[1]['id']
    imported=deepcopy(p.to_dict())
    path=tmp_path/'seed.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
    assert load_document(path).project.to_dict()==imported
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{}) and bridge.document.project.to_dict()==imported
    # Undoing/replaying a prior import cannot restore automatic-init authority.
    assert not bridge._entry_session['eligible']


@pytest.mark.parametrize('suffix,text',[('.srt','1\n00:00:01,000 --> 00:00:02,000\nsame chorus\n\n2\n00:00:03,000 --> 00:00:04,000\nsame chorus\n'),
                                      ('.lrc','[00:01.00]same chorus\n[00:03.00]last original')])
def test_source_seed_preserves_imported_times_including_missing_lrc_end(bridge,tmp_path,suffix,text):
    assert bridge.command('project.newEntry',{'mode':'audio'})
    path=original(tmp_path,text,suffix);expected,_=import_original(path)
    import_source(bridge,path)
    assert [(r.text_override,r.start,r.end) for r in bridge.document.main_timeline]==[(l.text,l.start,l.end) for l in expected]
    assert not bridge.document.project.translations


@pytest.mark.parametrize('mode',['ocr','edit',None])
def test_other_entry_source_import_stays_evidence_only(bridge,tmp_path,mode):
    if mode:assert bridge.command('project.newEntry',{'mode':mode})
    import_source(bridge,original(tmp_path))
    assert bridge.document.project.lyrics and not bridge.document.main_timeline
    assert not bridge.document.project.translations


@pytest.mark.parametrize('protected',['edited-delete','existing-source','translation-table','old-document'])
def test_prior_work_cannot_become_new_audio_seed(bridge,tmp_path,protected):
    assert bridge.command('project.newEntry',{'mode':'audio'})
    p=bridge.document.project
    if protected=='edited-delete':
        p.mappings=[Mapping('manual',[],text_override='edited')];bridge._changed()
        p.mappings=[];bridge._changed()
    elif protected=='existing-source':
        p.mappings=[Mapping('existing',[],text_override='keep original',text_role='source_import')];bridge._changed()
    elif protected=='translation-table':
        p.translations=[Translation('real','原有译文')];bridge._entry_accept_import();bridge._changed()
    elif protected=='old-document':
        path=tmp_path/'old-empty.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
        assert bridge.command('project.open',{'path':str(path)});settled(bridge)
    main=deepcopy(bridge.document.project.to_dict()['mappings']);translations=deepcopy(bridge.document.project.to_dict()['translations'])
    import_source(bridge,original(tmp_path))
    assert bridge.document.project.to_dict()['mappings']==main
    assert bridge.document.project.to_dict()['translations']==translations


@pytest.mark.parametrize('change',['switch-away','switch-away-back','edit','settings','cancel'])
def test_async_import_changes_cannot_create_main_seed(bridge,tmp_path,monkeypatch,change):
    import lyricsync.subtitles as subtitles
    assert bridge.command('project.newEntry',{'mode':'audio'})
    gate=threading.Event();started=threading.Event();parse=subtitles.import_original
    def slow(*args,**kwargs):
        result=parse(*args,**kwargs);started.set();assert gate.wait(5);return result
    monkeypatch.setattr(subtitles,'import_original',slow)
    assert bridge.command('input.import',{'role':'source','path':str(original(tmp_path))});wait(started.is_set)
    if change.startswith('switch'):
        assert not bridge.command('editor.start',{'mode':'ocr'})
        if change=='switch-away-back':assert not bridge.command('editor.start',{'mode':'edit'})
        assert bridge._entry_mode=='audio'
        assert bridge.command('task.cancel',{})  # busy switches are blocked; cancellation rejects the late import
    elif change=='edit':
        bridge.document.project.mappings=[Mapping('new-edit',[],text_override='preserve')];bridge._changed()
    elif change=='settings':
        bridge.document.project.settings['sample_fps']=5;bridge._changed()
    elif change=='cancel':
        assert bridge.command('task.cancel',{})
    before=deepcopy(bridge.document.project.to_dict());depth=len(bridge.history.undo_stack)
    gate.set();settled(bridge)
    assert bridge.document.project.to_dict()['mappings']==before['mappings']
    assert bridge.document.project.to_dict()==before and len(bridge.history.undo_stack)==depth


@pytest.mark.parametrize('result',['complete','missing','cancelled','edited'])
def test_source_seed_survives_4a_failure_and_exact_id_timing_application(bridge,tmp_path,result):
    assert bridge.command('project.newEntry',{'mode':'audio'})
    import_source(bridge,original(tmp_path))
    # The default virtual editing grid is the exact local index for this test.
    ids=[r.id for r in bridge.document.main_timeline]
    if result=='edited':assert bridge.command('row.edit',{'id':ids[0],'text':'human original'})
    before=deepcopy(bridge.document.project.to_dict());depth=len(bridge.history.undo_stack)
    generate(bridge,source='vocal',incomplete=result=='missing',cancelled=result=='cancelled');settled(bridge)
    assert [r.id for r in bridge.document.main_timeline]==ids
    assert all(r.text_role=='source_import' for r in bridge.document.main_timeline)
    assert not bridge.document.project.translations
    assert bridge.document.source_candidates
    if result in ('complete','missing'):
        record=bridge.document.source_candidates[-1]
        assert len(record['rows'])==len(ids)
        assert bridge._candidate_source=='main'  # v0.22 safe first result is editable immediately
        assert [r.id for r in bridge.document.main_timeline]==ids
        assert [r.start for r in bridge.document.main_timeline]==([.2,1.2,None] if result=='missing' else [.2,1.2,2.2])
        if result=='missing':
            assert bridge.document.main_timeline[-1].end is None
            assert bridge.stage=='review' and bridge._candidate_source=='main'
        assert [r.text_override for r in bridge.document.main_timeline]==['same chorus','same chorus','last original']
        assert len(bridge.history.undo_stack)==depth+1
        assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    else:
        assert bridge.document.project.to_dict()==before and len(bridge.history.undo_stack)==depth
        assert bridge.document.source_candidates


def test_out_of_range_timed_source_seed_rejected_before_any_mutation(bridge,tmp_path):
    assert bridge.command('project.newEntry',{'mode':'audio'})
    before=deepcopy(bridge.document.project.to_dict());depth=len(bridge.history.undo_stack)
    path=original(tmp_path,'1\n25:00:00,000 --> 25:00:01,000\noriginal\n','.srt')
    import_source(bridge,path)
    assert bridge.document.project.to_dict()==before and len(bridge.history.undo_stack)==depth
    assert '24 小时' in bridge.state['error']
