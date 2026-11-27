"""Dev10 OCR grouping/timing regressions. Synthetic evidence, not song accuracy."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from lyricsync.model import Lyric, Translation, Visual, Project, Mapping
from lyricsync.matching import match_project
from lyricsync.pipeline import run
from lyricsync.frames import FrameIndex


def test_one_ocr_sentence_does_not_duplicate_shared_range_for_imported_half_lines():
    p=Project(lyrics=[Lyric('a','follow the',None,None),Lyric('b','long road',None,None)],
              translations=[Translation('ta','follow the'),Translation('tb','long road')],
              visuals=[Visual('v','follow the long road',1.,4.,[],.99,6)])
    before=deepcopy(p.visuals)
    match_project(p)
    assert len(p.mappings)==1
    assert p.mappings[0].translation_ids==['ta','tb']
    assert p.mappings[0].lyric_ids==['a','b']
    assert (p.mappings[0].start,p.mappings[0].end)==(1.,4.)
    assert p.visuals==before


def test_real_newline_creates_two_source_rows_without_inventing_times(tmp_path):
    video=tmp_path/'input.mp4';video.write_bytes(b'scan is controlled')
    p=Project(inputs={'video':str(video)})
    visual=Visual('v','upper line\nlower line',1.,4.,[1,2,100,80],.99,6)
    before=deepcopy(visual)
    with patch('lyricsync.pipeline.scan',return_value=([visual],{'duration':5.})):
        run(p,tmp_path/'project.json')
    assert [v.text for v in p.visuals]==['upper line','lower line']
    assert len(p.mappings)==2
    assert len({r.visual_ids[0] for r in p.mappings})==2
    assert {(r.start,r.end) for r in p.mappings}=={(1.,4.)}
    assert visual==before
    assert all(v.evidence['multiline_parent_id']=='v' for v in p.visuals)


def test_zero_seconds_visual_does_not_make_timed_mapping():
    p=Project(lyrics=[Lyric('l','moonlight',None,None)],
              translations=[Translation('t','moonlight')],
              visuals=[Visual('v','moonlight',2.,2.,[],.99,1)])
    match_project(p)
    assert p.mappings[0].start is None and p.mappings[0].end is None
    assert p.visuals[0].start==p.visuals[0].end==2.


def test_equal_inclusive_frame_numbers_are_one_frame_not_zero_seconds():
    index=FrameIndex([n/30 for n in range(2100)],70.)
    start,end=index.interval(2001,2001)
    p=Project(lyrics=[Lyric('l','moonlight',None,None)],
              translations=[Translation('t','moonlight')],
              visuals=[Visual('v','moonlight',start,end,[],.99,1)])
    match_project(p)
    row=p.mappings[0]
    assert row.end>row.start
    assert (index.first(row.start),index.last(row.end))==(2001,2001)
    assert (row.start,row.end)==(start,end)


def test_independent_simultaneous_lines_remain_two_rows():
    p=Project(lyrics=[Lyric('a','upper line',None,None),Lyric('b','lower line',None,None)],
              translations=[Translation('ta','upper line'),Translation('tb','lower line')],
              visuals=[Visual('va','upper line',1.,4.,[],.99,6),Visual('vb','lower line',1.,4.,[],.99,6)])
    match_project(p)
    assert len(p.mappings)==2
    assert [r.visual_ids for r in p.mappings]==[['va'],['vb']]


def test_manual_shared_evidence_is_never_coalesced():
    manual=Mapping('manual',['ta'],['a'],['v'],1.,3.,human_edited=True)
    p=Project(lyrics=[Lyric('a','follow the',None,None),Lyric('b','long road',None,None)],
              translations=[Translation('ta','follow the'),Translation('tb','long road')],
              visuals=[Visual('v','follow the long road',1.,4.,[],.99,6)],mappings=[manual])
    match_project(p)
    assert manual in p.mappings and (manual.start,manual.end)==(1.,3.)


def test_line_split_is_idempotent_and_keeps_original_evidence():
    from lyricsync.ocr_observations import split_visual_lines
    raw=Visual('block','first line\r\nsecond line',1.,4.,[1,2,100,80],.9,5,
               evidence={'samples':[[1.,'ocr',.9]]})
    children=split_visual_lines([raw])
    assert split_visual_lines(children)==children
    assert children[0].evidence['multiline_original_text']==raw.text
    assert children[0].box==children[1].box==raw.box
    children[0].evidence['samples'][0][0]=9.
    assert raw.evidence['samples'][0][0]==children[1].evidence['samples'][0][0]==1.


def test_translated_pipeline_splits_newline_without_fake_original(tmp_path):
    video=tmp_path/'input.mp4';video.write_bytes(b'scan is controlled')
    translation=tmp_path/'translation.txt'
    translation.write_text('upper line\nlower line',encoding='utf-8')
    p=Project(inputs={'video':str(video),'translation':str(translation)})
    visual=Visual('v','upper line\nlower line',1.,4.,[1,2,100,80],.99,6)
    with patch('lyricsync.pipeline.scan',return_value=([visual],{'duration':5.})), \
         patch('lyricsync.pipeline.suggest',side_effect=AssertionError('AI must not run')):
        run(p,tmp_path/'project.json')
    assert p.lyrics==[]
    assert len(p.mappings)==2
    assert all(not row.lyric_ids for row in p.mappings)
    assert {(row.start,row.end) for row in p.mappings}=={(1.,4.)}


def test_shared_group_identity_stable_after_rematch():
    p=Project(lyrics=[Lyric('a','follow the',None,None),Lyric('b','long road',None,None)],
              translations=[Translation('ta','follow the'),Translation('tb','long road')],
              visuals=[Visual('v','follow the long road',1.,4.,[],.99,6)])
    match_project(p)
    identity=p.mappings[0].id
    match_project(p)
    assert len(p.mappings)==1 and p.mappings[0].id==identity


@pytest.mark.parametrize('start,end',[(3.,2.),(float('nan'),2.),(1.,float('inf'))])
def test_invalid_observation_times_are_retained_but_not_applied(start,end):
    p=Project(lyrics=[Lyric('l','moonlight',None,None)],
              translations=[Translation('t','moonlight')],
              visuals=[Visual('v','moonlight',start,end,[],.99,1)])
    match_project(p)
    assert p.mappings[0].start is None and p.mappings[0].end is None
    assert any('OCR 无效时间证据' in warning for warning in p.warnings)


def test_split_children_do_not_duplicate_protected_old_block_after_rescan():
    from lyricsync.ocr_observations import split_visual_lines
    from lyricsync.video_only import build_empty_translations
    raw=Visual('block','first line\nsecond line',1.,4.,[1,2,100,80],.9,5)
    manual=Mapping('manual',['t'],visual_ids=['block'],start=2.,end=5.,human_edited=True)
    p=Project(translations=[Translation('t','manual text')],
              visuals=split_visual_lines([raw])+[raw],mappings=[manual])
    build_empty_translations(p)
    assert p.mappings==[manual] and (manual.start,manual.end)==(2.,5.)


def test_confirmed_unlocked_half_line_is_not_coalesced_or_retimed():
    confirmed=Mapping('confirmed',['ta'],['a'],['v'],1.5,3.5,confirmed=True)
    p=Project(lyrics=[Lyric('a','follow the',None,None),Lyric('b','long road',None,None)],
              translations=[Translation('ta','follow the'),Translation('tb','long road')],
              visuals=[Visual('v','follow the long road',1.,4.,[],.99,6)],mappings=[confirmed])
    before=deepcopy(confirmed)
    match_project(p)
    assert any(row is confirmed for row in p.mappings)
    assert confirmed==before
    assert sum('ta' in row.translation_ids for row in p.mappings)==1


def test_pipeline_retains_confirmed_rows_and_their_old_evidence(tmp_path):
    video=tmp_path/'input.mp4';video.write_bytes(b'scan is controlled')
    translation=tmp_path/'translation.txt';translation.write_text('new translation',encoding='utf-8')
    original=tmp_path/'original.txt';original.write_text('new original',encoding='utf-8')
    old_visual=Visual('old-v','old words',1.,4.,[],.9,5)
    confirmed=Mapping('confirmed',['old-t'],['old-l'],['old-v'],1.5,3.5,confirmed=True)
    p=Project(inputs={'video':str(video),'translation':str(translation),'ktv':str(original)},
              lyrics=[Lyric('old-l','old words',None,None)],translations=[Translation('old-t','saved words')],
              visuals=[old_visual],mappings=[confirmed])
    before=deepcopy(confirmed)
    with patch('lyricsync.pipeline.scan',return_value=([Visual('new-v','new original',4.,6.,[],.9,5)],{'duration':7.})):
        run(p,tmp_path/'project.json')
    assert next(row for row in p.mappings if row.id=='confirmed')==before
    assert any(v.id=='old-v' for v in p.visuals)
    assert any(l.id=='old-l' for l in p.lyrics)
    assert any(t.id=='old-t' for t in p.translations)
