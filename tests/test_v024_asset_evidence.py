import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_qt_entry_initialization_dev5 import bridge
from lyricsync.model import Lyric, Visual, Mapping
from lyricsync.editing import mapping_text
from desktop_qt.timeline_views import current_candidate, archives


def seed(b):
    p=b.document.project
    p.inputs['video']='old.mp4'
    p.video.update(width=100,height=100,duration=5,content_hash='old')
    p.lyrics=[Lyric('ocr-source-old','OCR word',1,2)]
    p.visuals=[Visual('v','OCR word',1,2,[0,0,50,20],.9,3)]
    p.mappings=[Mapping('m',[],lyric_ids=['ocr-source-old'],visual_ids=['v'],text_override='manual',text_role='source_ocr',start=1,end=2,human_edited=True)]
    p.settings.update(ocr_multi_region=True,ocr_regions=[dict(id='r',box=[0,0,50,20],enabled=True)],
                      translation_capsules_v022={'old':'draft'},cue_rescans={'old':'evidence'})
    b.document.source_candidates=[dict(id='old',source='ocr')]
    return p


@pytest.mark.parametrize('choice',['keep','discard'])
def test_replace_isolates_all_old_visual_evidence_and_undo(bridge,tmp_path,monkeypatch,choice):
    b=bridge;p=seed(b);before=deepcopy(p.to_dict())
    new=tmp_path/'new.mp4';new.write_bytes(b'fixture')
    monkeypatch.setattr(b,'_confirm_media_timing_reuse',lambda:choice)
    monkeypatch.setattr(b.media_controller,'open',lambda *a:None)
    b._open_video({'path':str(new)})
    assert not p.visuals and not p.lyrics and not p.mappings[0].visual_ids
    assert mapping_text(p,p.mappings[0])=='manual'
    assert p.mappings[0].start==(1 if choice=='keep' else None)
    assert all(key not in p.settings for key in ('ocr_regions','ocr_multi_region','translation_capsules_v022','cue_rescans'))
    assert archives(p)[-1]['project']['visuals'][0]['id']=='v'
    assert not current_candidate(b.document,b.document.source_candidates[0])
    b.document.validate_project(p)
    b._history(False)
    assert b.document.project.to_dict()==before


def test_replace_full_archive_refuses_before_mutation(bridge,tmp_path,monkeypatch):
    b=bridge;p=seed(b)
    from desktop_qt.timeline_views import archive_main
    for n in range(8):archive_main(p,str(n))
    before=deepcopy(p.to_dict());history=len(b.history.undo_stack)
    new=tmp_path/'new.mp4';new.write_bytes(b'fixture')
    monkeypatch.setattr(b,'_confirm_media_timing_reuse',lambda:'discard')
    monkeypatch.setattr(b.media_controller,'open',lambda *a:pytest.fail('unexpected open'))
    with pytest.raises(ValueError,match='另存副本不会释放'):
        b._open_video({'path':str(new)})
    assert p.to_dict()==before and len(b.history.undo_stack)==history


def test_empty_mode_switch_preserves_visual_only_state(bridge,monkeypatch):
    from desktop_qt import dialogs
    b=bridge;p=seed(b);p.mappings=[];b._has_workspace=True
    before=deepcopy(p.to_dict());candidates=deepcopy(b.document.source_candidates)
    monkeypatch.setattr(dialogs,'confirm_mode_switch',lambda:pytest.fail('Empty, lossless switch'))
    assert b._start_editor({'mode':'audio'})
    assert p.to_dict()==before and b.document.source_candidates==candidates
    assert b.document.entry_mode=='audio' and not archives(p)


def test_manual_mode_detach_preserves_main_and_archives(bridge,monkeypatch):
    from desktop_qt import dialogs
    b=bridge;p=seed(b)
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
    assert b._start_editor({'mode':'audio'})
    assert b._entry_mode=='edit' and mapping_text(p,p.mappings[0])=='manual'
    assert p.mappings[0].start==1 and not p.visuals
    assert archives(p)


def test_clear_media_removes_multi_roi_and_pseudo_lyrics(bridge,monkeypatch):
    from desktop_qt import dialogs
    b=bridge;p=seed(b)
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
    b._clear_input({'role':'media'})
    assert not p.visuals and not p.lyrics and 'ocr_regions' not in p.settings
    assert mapping_text(p,p.mappings[0])=='manual'
    b.document.validate_project(p)


def test_clear_source_without_ktv_also_clears_saved_alignment_source(bridge):
    b=bridge;p=b.document.project
    p.settings['local_alignment_source']={'version':1,'text_source':'external','external_rows':[]}
    before=deepcopy(p.to_dict())
    b._clear_input({'role':'source'})
    assert 'local_alignment_source' not in p.settings
    b._history(False)
    assert b.document.project.to_dict()==before


def test_vocal_analysis_ignores_historical_candidates(bridge):
    b=bridge
    b.document.source_candidates=[dict(id='fresh',source='vocal'),dict(id='old',source='vocal')]
    b.document.project.settings['historical_asset_candidate_ids']=['old']
    assert b._vocal_analysis_record()['id']=='fresh'
    b.document.project.settings['historical_vocal_candidate_ids']=['fresh']
    assert b._vocal_analysis_record() is None


def test_rematch_ignores_historical_latest_candidate(bridge,monkeypatch):
    import lyricsync.candidate_tracks as candidates
    import lyricsync.matching as matching
    b=bridge
    b.document.source_candidates=[dict(id=identity,source='ocr',rows=[dict(start=1.,end=2.)]) for identity in ('fresh','old')]
    b.document.source_candidates.append(dict(id='empty',source='ocr',rows=[]))
    b.document.project.settings['historical_asset_candidate_ids']=['old']
    seen=[];jobs=[]
    monkeypatch.setattr(candidates,'capture_source',lambda *a,**k:{'provenance':{}})
    monkeypatch.setattr(candidates,'verify_candidate_source',lambda *a,**k:None)
    monkeypatch.setattr(candidates,'candidate_project',lambda record,project:(seen.append(record['id']) or project))
    monkeypatch.setattr(candidates,'candidate_from_project',lambda *a:{})
    monkeypatch.setattr(matching,'match_project',lambda *a:None)
    monkeypatch.setattr(b,'_start_job',lambda *a,**k:jobs.append(a))
    b._rematch();jobs[0][1](lambda:False,lambda *a:None)
    assert seen==['fresh']
