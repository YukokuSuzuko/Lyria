import threading
from copy import deepcopy

import pytest

from test_dev5_feedback_navigation import bridge, app
from test_qt_entry_initialization_dev5 import settled
from test_v022_token_editing import tok
from lyricsync.candidate_tracks import capture_source, candidate_from_project, install_candidate, prepare_apply, apply_plan
from lyricsync.editing import History
from lyricsync.model import Mapping, Project, Lyric, Visual
from lyricsync.project_document import ProjectDocument, load_document
from lyricsync.sequence_alignment import _align_observations


@pytest.mark.parametrize("new,expected", [("別の句","unknown"),("元の句","kana")])
def test_ocr_replacement_invalidates_only_changed_offsets(new, expected):
    tokens=[tok(.3,.5)]
    doc=ProjectDocument(project=Project(mappings=[Mapping('m',[],text_override='元の句',text_role='source_ocr',start=.2,end=1.2,alignment_tokens=deepcopy(tokens))]))
    snap=capture_source(doc,'ocr')
    proposal=Project.from_dict(deepcopy(snap['project']))
    proposal.mappings[0].text_override=new; proposal.mappings[0].start=.18
    record=candidate_from_project(snap,proposal)
    install_candidate(doc,record)
    assert doc.main_timeline[0].text_override=='元の句'
    history=History(); apply_plan(doc,history,prepare_apply(doc,record['id'],['m'],apply_text=True))
    token=doc.main_timeline[0].alignment_tokens[0]
    assert token['ownership']==expected
    assert (token['start'],token['end'])==(.3,.5)
    restored,_=history.restore(doc.project,validator=doc.validate_project)
    assert restored.main_timeline[0].alignment_tokens==tokens


def test_timed_repeated_direct_matches_are_consumed_once():
    lyrics=[Lyric('r1','同じサビ',1.,2.),Lyric('mid','別の歌詞',3.,4.),Lyric('r2','同じサビ',6.,7.)]
    visuals=[Visual('v1','同じサビ',1.,2.,[],.98,4),Visual('m','別の歌詞',3.,4.,[],.98,4),Visual('v2','同じサビ',6.,7.,[],.98,4)]
    edges=_align_observations(lyrics,visuals)
    for lid,vid in [('r1','v1'),('r2','v2'),('mid','m')]:
        assert [e['visual_ids'] for e in edges if lid in e['lyric_ids']]==[[vid]]
    assert all(e.get('repeated_instance') for e in edges if set(e['lyric_ids']) & {'r1','r2'})


def test_stale_ocr_settings_releases_owner_only(bridge,monkeypatch):
    import onnxruntime
    running=threading.Event(); release=threading.Event()
    def providers():
        running.set()
        assert release.wait(5)
        return ['CPUExecutionProvider']
    monkeypatch.setattr(onnxruntime,'get_available_providers',providers)
    shown=[]
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings',lambda *a,**kw:shown.append(True))
    bridge._ocr_settings()
    try:
        assert running.wait(3)
        assert bridge._ocr_settings_job and bridge._ocr_settings_pending
        bridge._invalidate_assets()
    finally:
        release.set()
    settled(bridge)
    assert not shown and not bridge._ocr_settings_pending and not bridge._ocr_settings_job
    bridge._ocr_settings(); settled(bridge)
    assert shown==[True] and not bridge._ocr_settings_pending


def test_third_save_as_never_swallows_second(bridge,tmp_path,monkeypatch):
    from lyricsync.project_document import write_document
    wait=threading.Event(); running=threading.Event()
    a,b,c=[tmp_path / (name+'.lyria') for name in 'abc']
    def slow(snapshot,path):
        if str(path)==str(a):
            running.set();assert wait.wait(5)
        return write_document(snapshot,path)
    monkeypatch.setattr('desktop_qt.controller.write_document',slow)
    try:
        bridge._save(True,str(a)); assert running.wait(3)
        bridge._save(True,str(b))
        with pytest.raises(ValueError,match='本次未接受'):
            bridge._save(True,str(c))
    finally:
        wait.set()
    settled(bridge)
    assert load_document(a).id == load_document(b).id == bridge.document.id
    assert not c.exists()
