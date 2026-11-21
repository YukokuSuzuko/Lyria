from copy import deepcopy
from dataclasses import asdict
import pytest
from lyricsync.model import Project, Visual, Translation, Lyric
from lyricsync.ocr_correspondence import source_anchors, match_visual_project
from lyricsync.translation_pairing_draft import make_draft, apply_draft, request_payload
from lyricsync.translation_alignment import interjection, structural_groups


def project():
    p = Project()
    p.visuals = [Visual('v0', 'あ〜↑〜↑〜', 1., 2., [0, 0, 80, 20], .9, 3),
                 Visual('v1', '普通の歌詞', 3., 5., [0, 0, 80, 20], .9, 3)]
    p.translations = [Translation('t1', '普通的歌词')]
    return p


def test_visual_first_no_fake_lyrics_and_cheer_does_not_absorb():
    p = project(); before = deepcopy([asdict(v) for v in p.visuals])
    match_visual_project(p)
    assert p.lyrics == []
    assert [asdict(v) for v in p.visuals] == before
    translated = [m for m in p.mappings if m.translation_ids]
    assert len(translated) == 1 and translated[0].visual_ids == ['v1']
    assert any(m.visual_ids == ['v0'] and not m.translation_ids and m.start == 1 for m in p.mappings)


def test_unmatched_reference_does_not_swallow_visual_time():
    p = project(); p.lyrics = [Lyric('l1', '全く別の参照', None, None)]
    before = deepcopy(p.to_dict()['lyrics'])
    match_visual_project(p)
    assert {v for m in p.mappings for v in m.visual_ids} == {'v0', 'v1'}
    assert p.to_dict()['lyrics'] == before
    assert all(m.start is not None for m in p.mappings if m.visual_ids)


@pytest.mark.parametrize('text', ['あ〜↑〜↑〜', 'れっつごー', "Let's go!", 'YEAH!', 'あ～个～个～'])
def test_known_cheers(text):
    assert interjection(text)


@pytest.mark.parametrize('text', ['君', 'I love you', '明日', 'let us remember'])
def test_short_lyrics_not_blanket_cheers(text):
    assert not interjection(text)


def test_draft_no_text_generation_and_confirmation_staleness():
    p=project(); a=source_anchors(p)[1].id
    response={'groups':[{'anchor_ids':[a], 'slices':[{'translation_id':'t1','start':0,'end':2}]}]}
    before=deepcopy(p.to_dict()); draft=make_draft(p,response)
    assert p.to_dict()==before
    with pytest.raises(ValueError):apply_draft(p,draft)
    result=apply_draft(p,draft,confirmed=True)
    assert p.to_dict()==before
    assert result.visuals==p.visuals and result.lyrics==p.lyrics
    assert next(t.text for t in result.translations if t.id=='t1')=='普通的歌词'
    assert any(m.start is None for m in result.mappings)  # unused suffix retained
    assert all('text' not in s for g in draft['groups'] for s in g['slices'])
    bad=deepcopy(response);bad['groups'][0]['text']='invented'
    with pytest.raises(ValueError):make_draft(p,bad)
    p.translations[0].text='manual change'
    with pytest.raises(ValueError):apply_draft(p,draft,confirmed=True)


def test_grapheme_and_duplicate_slice_rejection():
    p=project();p.translations[0].text='a\u0301b'
    response={'groups':[{'anchor_ids':[], 'slices':[{'translation_id':'t1','start':0,'end':1}]}]}
    with pytest.raises(ValueError):make_draft(p,response)
    response['groups'][0]['slices'][0]['end']=2
    response['groups'].append(deepcopy(response['groups'][0]))
    with pytest.raises(ValueError):make_draft(p,response)


def test_no_group_spans_long_interlude():
    source=[Lyric('a','first',1,2),Lyric('b','second',20,21)]
    target=[Translation('t','第一第二')]
    groups=structural_groups(source,target)
    assert all(len(a)<2 for a,_,_,_ in groups)




def test_explicit_one_to_many_pts_split_keeps_evidence_and_all_text():
    from lyricsync.frames import FrameIndex
    from lyricsync.translation_pairing_draft import estimate_split_frames
    p = project(); p.translations[0].text = 'a\u0301bcd'
    anchor = source_anchors(p)[1].id
    parts = [{'translation_id':'t1','start':0,'end':2},
             {'translation_id':'t1','start':2,'end':5}]
    frames = FrameIndex([0, 3.1, 3.4, 3.55, 4.2, 4.8, 5.1], 6)
    cuts = estimate_split_frames(p, [anchor], parts, frames)
    assert cuts == [4]  # one grapheme versus three, target 3.5, nearest true PTS 3.55
    response = {'groups':[{'anchor_ids':[anchor], 'slices':parts, 'split_frames':cuts}]}
    before = deepcopy(p.to_dict())
    draft = make_draft(p,response,frame_index=frames)
    result = apply_draft(p,draft,confirmed=True,frame_index=frames)
    assert [(m.start,m.end) for m in result.mappings] == [(3,3.55),(3.55,5)]
    assert all(m.visual_ids == ['v1'] for m in result.mappings)
    assert result.visuals == p.visuals and p.to_dict() == before
    assert [next(t.text for t in result.translations if t.id == m.translation_ids[0])
            for m in result.mappings] == ['a\u0301','bcd']
    response['groups'][0]['split_frames'] = [5]  # user adjustment, same frozen anchor
    adjusted = apply_draft(p,make_draft(p,response,frame_index=frames),confirmed=True,frame_index=frames)
    assert [(m.start,m.end) for m in adjusted.mappings] == [(3,4.2),(4.2,5)]
    assert adjusted.visuals == p.visuals
    frames.stamps[3] = 3.6
    with pytest.raises(ValueError): apply_draft(p,draft,confirmed=True,frame_index=frames)


@pytest.mark.parametrize('cuts', [[1], [7], [True], [], [3,4]])
def test_split_rejects_invalid_or_wrong_count_boundaries(cuts):
    from lyricsync.frames import FrameIndex
    p=project(); anchor=source_anchors(p)[1].id
    parts=[{'translation_id':'t1','start':0,'end':1},
           {'translation_id':'t1','start':1,'end':2}]
    response={'groups':[{'anchor_ids':[anchor],'slices':parts,'split_frames':cuts}]}
    with pytest.raises(ValueError): make_draft(p,response,frame_index=FrameIndex([0,3,4,5],6))
    with pytest.raises(ValueError): make_draft(p,response)


def test_split_too_short_rejects_instead_of_inventing_frames():
    from lyricsync.frames import FrameIndex
    from lyricsync.translation_pairing_draft import estimate_split_frames
    p=project(); anchor=source_anchors(p)[1].id
    parts=[{'translation_id':'t1','start':0,'end':1},
           {'translation_id':'t1','start':1,'end':2}]
    with pytest.raises(ValueError): estimate_split_frames(p,[anchor],parts,FrameIndex([0,3,5],6))


def test_simultaneous_anchor_order_uses_spatial_line_evidence_not_hashed_id():
    p=Project()
    p.visuals=[Visual('z-upper','upper line',1,4,[0,0,100,20],.99,5),
               Visual('a-lower','lower line',1,4,[0,40,100,60],.99,5)]
    p.translations=[Translation('t-upper','upper line'),Translation('t-lower','lower line')]
    match_visual_project(p)
    assert len(p.mappings)==2
    assert [m.visual_ids for m in p.mappings]==[['z-upper'],['a-lower']]
    assert p.lyrics==[]


def test_rescan_preserves_explicit_nonchronological_row_order_and_manual_insert():
    from lyricsync.model import Mapping
    p=Project(visuals=[Visual('v1','first',1,2,[],.99,5),Visual('v2','second',3,4,[],.99,5)],
              translations=[Translation('t1','first'),Translation('t2','second')])
    match_visual_project(p)
    first,second=list(p.mappings)
    manual=Mapping('manual',[],start=None,end=None,human_edited=True,text_override='note')
    p.mappings=[second,manual,first]
    before=[m.id for m in p.mappings]
    match_visual_project(p)
    assert [m.id for m in p.mappings]==before
    assert p.mappings[1].text_override=='note' and p.mappings[1].start is None


@pytest.mark.parametrize('replacement',[False,True])
def test_orphan_legacy_ocr_source_never_becomes_reference(replacement):
    from lyricsync.model import stable_id
    from lyricsync.ocr_correspondence import reference_lyrics,evidence_snapshot
    old=stable_id('ocr-source','deleted-visual-id')
    imported=stable_id('lyric','real-imported-event')
    p=Project(lyrics=[Lyric(old,'same line',1,2),Lyric(imported,'real original',None,None),
                      Lyric('ocr-source-custom-user-id','custom original',None,None)])
    if replacement:p.visuals=[Visual('new-visual','same line',1,2,[0,0,60,20],.99,3)]
    before=deepcopy(p.to_dict())
    assert [l.id for l in reference_lyrics(p)]==[imported,'ocr-source-custom-user-id']
    assert old not in {i for anchor in source_anchors(p) for i in anchor.lyric_ids}
    assert all(l['id']!=old for l in evidence_snapshot(p)['reference_lyrics'])
    assert p.to_dict()==before  # historical evidence isn't deleted or rewritten
