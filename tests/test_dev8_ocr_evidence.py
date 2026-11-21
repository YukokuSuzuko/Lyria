"""Synthetic safety contracts; these are not song accuracy measurements."""
from unittest.mock import patch
import pytest
from lyricsync.model import Lyric, Visual, Detection, Project, Translation
from lyricsync.vision import Track, refine_track, text_presence
from lyricsync.matching import associate_visuals, match_project


def track(text='OLD LINE', first=.4, last=.6, box=None):
    return Track(first,last,box or [0,0,100,30],[text],[.99],None,first,last,prior_absent=0)


class Video:
    info = {'fps': 10, 'duration': 3}
    def frames(self,left,right,cancel):
        for i in range(31):
            t=round(i/10,8)
            if left-1e-6 <= t <= right+1e-6:
                if cancel(): raise InterruptedError()
                yield t, t


def test_window_edge_does_not_become_false_boundary():
    with patch('lyricsync.vision.visibility_score',return_value=1):
        a,b,brackets,notes=refine_track(Video(),track(),.2,.62,lambda:False)
    assert a == .4 and b == pytest.approx(.7)
    assert len(notes) == 2
    assert all('暂保留粗扫时间' in n for n in notes)


def test_replacement_text_resolves_both_adjacent_pts():
    calls=[]
    def probe(t,frame):
        calls.append(t)
        return [Detection('OLD LINE' if .3 <= t < .8 else 'NEW WORDS',[0,0,100,30],.99)]
    with patch('lyricsync.vision.visibility_score',return_value=1):
        a,b,brackets,notes=refine_track(Video(),track(),.2,.62,lambda:False,probe)
    assert (a,b) == (.3,.8)
    assert brackets == [[.2,.3],[.7,.8]]
    assert len(calls) <= 24


def test_ocr_silence_does_not_clip_subtitle():
    with patch('lyricsync.vision.visibility_score',return_value=1):
        a,b,_,_=refine_track(Video(),track(),.2,.62,lambda:False,lambda *args:[])
    assert (a,b) == pytest.approx((.4,.7))


def test_simultaneous_text_presence_wins_over_conflict():
    tr=track()
    assert text_presence(tr,[Detection('OLD LINE',tr.box,.99),Detection('NEW WORDS',tr.box,.99)]) is True
    assert text_presence(tr,[Detection('NEW WORDS',[0,100,100,130],.99)]) is None
    assert text_presence(tr,[Detection('NEW WORDS',tr.box,.5)]) is None


def test_independent_overlapping_tracks_are_not_trimmed():
    def probe(t,frame):
        found=[]
        for name,box,left,right in [('OLD LINE',[0,0,100,30],.3,.8),('NEW WORDS',[0,100,100,130],.5,1.)]:
            found.append(Detection(name if left<=t<right else 'DIFFERENT TEXT',box,.99))
        return found
    with patch('lyricsync.vision.visibility_score',return_value=1):
        a=refine_track(Video(),track(),.2,.62,lambda:False,probe)
        b=refine_track(Video(),track('NEW WORDS',.6,.8,[0,100,100,130]),.2,.62,lambda:False,probe)
    assert a[:2] == (.3,.8) and b[:2] == (.5,1.)
    assert a[1] > b[0]


def test_cancellation_during_text_probe_propagates():
    def probe(t,frame): raise InterruptedError('cancelled')
    with patch('lyricsync.vision.visibility_score',return_value=1),pytest.raises(InterruptedError):
        refine_track(Video(),track(),.2,.62,lambda:False,probe)


def lyrics(*texts): return [Lyric(str(i),t,None,None) for i,t in enumerate(texts)]
def visual(text): return Visual('v',text,1,3,[0,0,100,30],.99,5)


def test_unique_exact_split_lines_share_whole_line_evidence():
    ls=lyrics('寝落ち通話の','優しめの声もやばいよ')
    v=visual('寝落ち通話の優しめの声もやばいよ')
    associations,unknown=associate_visuals(ls,[v])
    assert not unknown and all(associations[l.id][0][0] is v for l in ls)
    assert all('独立起止时间' in associations[l.id][0][2][-1] for l in ls)
    p=Project(lyrics=ls,visuals=[v],translations=[Translation('t','译文',reference=v.text)])
    row=match_project(p)[0]
    assert (row.start,row.end,row.visual_ids)==(1,3,['v'])


def test_repeated_joined_occurrence_does_not_share_arbitrarily():
    ls=lyrics('alpha','beta','alpha','beta')
    associations,_=associate_visuals(ls,[visual('alphabeta')])
    assert sum(bool(x) for x in associations.values()) <= 1


def test_fuzzy_and_timed_split_lines_not_automatically_shared():
    ls=lyrics('alpha','beta')
    associations,_=associate_visuals(ls,[visual('alphabeto')])
    assert sum(bool(x) for x in associations.values()) <= 1
    ls[0].start=1;ls[0].end=2
    associations,_=associate_visuals(ls,[visual('alphabeta')])
    assert sum(bool(x) for x in associations.values()) <= 1


def test_count_only_pairing_discloses_no_semantic_basis():
    p=Project(lyrics=lyrics('a','b','c'),translations=[Translation('t','译文')])
    rows=match_project(p)
    assert any('没有跨语言句意依据' in reason for row in rows for reason in row.reasons)
    assert not any(row.confirmed for row in rows)


def test_unresolved_shape_extension_falls_back_to_last_text_observation():
    tr=track(last=.9);tr.last_ocr=.6
    with patch('lyricsync.vision.visibility_score',return_value=1):
        _,end,_,_=refine_track(Video(),tr,.2,.62,lambda:False)
    assert end == pytest.approx(.7)


def test_partial_shared_word_is_not_full_subtitle_presence():
    tr=track()
    assert text_presence(tr,[Detection('LINE',tr.box,.99)]) is None


def test_cancellation_during_last_boundary_inference_is_not_success():
    from lyricsync.vision import refine_text_boundary
    cancelled = [False]
    def probe(t, frame):
        if t == .8:
            cancelled[0] = True
        return [Detection('OLD LINE' if t < .8 else 'NEW WORDS', [0,0,100,30], .99)]
    with pytest.raises(InterruptedError):
        refine_text_boundary(Video(), track(), [(i/10, 1) for i in range(3,10)],
                             .6, 'end', probe, lambda: cancelled[0])


def test_cancel_after_last_track_does_not_cache_complete_scan(tmp_path):
    import numpy as np
    from lyricsync import vision
    from lyricsync.storage import Cache
    cancelled = [False]
    class FakeVideo:
        info = {'fps': 10, 'duration': 1, 'width': 100, 'height': 30}
        def __init__(self, path): self.info = dict(type(self).info)
        def frames(self, left, right, cancel):
            yield .1, np.zeros((30,100,3),dtype=np.uint8)
    class Provider:
        signature = {'name': 'synthetic-cancellation-test'}
        def recognize(self, frame): return [Detection('OLD LINE',[0,0,100,30],.99)]
    def progress(value, message):
        if value == 1:
            cancelled[0] = True
    cache = Cache(tmp_path/'cache.sqlite')
    try:
        with patch.object(vision,'Video',FakeVideo), patch.object(vision,'file_hash',return_value='fixture'), \
             patch.object(vision,'refine_track',return_value=(.1,.2,[[0,.1],[.2,.3]],[])), \
             pytest.raises(InterruptedError):
            vision.scan('fixture',{'sample_fps':3},cache,progress=progress,
                        cancel=lambda:cancelled[0],provider=Provider())
        assert cache.db.execute("SELECT count(*) FROM cache WHERE key LIKE 'scan:%'").fetchone()[0] == 0
        assert cache.db.execute("SELECT count(*) FROM cache WHERE key LIKE 'ocr:%'").fetchone()[0] == 1
    finally:
        cache.db.close()
