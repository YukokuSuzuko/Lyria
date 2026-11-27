"""Footprint corrections must preserve short lyrics and localise their evidence."""
from copy import deepcopy
from types import SimpleNamespace as Row

import pytest

from lyricsync.ocr_anchor_evidence import clipped_noise, observed_groups
from lyricsync.translation_alignment import interjection


ROI = [20, 100, 1020, 200]
EDGE = {'text': '7x', 'box': [0, 0, 460, 100], 'confidence': .59}
WORD = {'text': '星', 'box': [480, 30, 570, 70], 'confidence': .91}


def track(text, box, start, end, confidence, frames, parts, *, assembled=False):
    observed={'assembly':'whole_spatial_line_rerecognition' if assembled else 'direct',
              'components':deepcopy(parts)}
    return Row(text=text,box=box,start=start,end=end,confidence=confidence,
               evidence={'frame_count':frames,'recognition_confidence':confidence,
                         'components':[{'evidence':observed,'confidence':confidence}]})


def test_reject_only_independently_confirmed_clipped_fragments():
    row=track('7x',[20,100,480,200],1.,3.,.60,20,[EDGE])
    assert clipped_noise(row,ROI)
    assert not clipped_noise(row,None)
    for parts in ([WORD], [None], [], [{'text':None,'box':[1], 'confidence':None}]):
        row.evidence['components'][0]['evidence']['components']=parts
        assert not clipped_noise(row,ROI)
    row.evidence['components'][0]['evidence']['components']=[EDGE]
    row.evidence['recognition_confidence']=.92
    assert not clipped_noise(row,ROI)


@pytest.mark.parametrize('with_reference',[False,True])
def test_video_only_does_not_make_a_lyric_from_proven_crop_noise(with_reference):
    from dataclasses import asdict
    from lyricsync.model import Lyric, Mapping, Project, Visual
    from lyricsync.video_only import build_empty_translations
    def visual(identity,text,box,start,end,confidence,parts):
        obs=track(text,box,start,end,confidence,20,parts)
        return Visual(identity,text,start,end,box,confidence,20,evidence=obs.evidence)
    noise=visual('edge','7x',[20,100,480,200],1.,3.,.60,[EDGE])
    word=visual('word','星',[500,130,590,170],4.,5.,.91,[WORD])
    with_lock=deepcopy(noise);with_lock.id='locked-edge'
    manual=Mapping('manual',[],visual_ids=['locked-edge'],text_override='Keep',start=1.,end=2.,locked=True)
    project=Project(visuals=[noise,word,with_lock],mappings=[manual],settings={'roi':ROI})
    if with_reference:
        project.lyrics=[Lyric('original','星',None,None)];project.inputs['ktv']='original.txt'
        project.settings.update(ocr_multi_region=True,ocr_regions=[dict(id='only',box=ROI,enabled=True)])
    before=[asdict(v) for v in project.visuals]
    proposed=build_empty_translations(project)
    assert [row.visual_ids for row in proposed]==[['word']]
    assert [(row.start,row.end) for row in proposed]==[(4.,5.)]
    assert project.mappings[0] is manual and manual.locked and manual.text_override=='Keep'
    assert [asdict(v) for v in project.visuals]==before

def test_video_only_reuses_evidence_for_an_isolated_correct_reading():
    from copy import deepcopy
    from lyricsync.model import Project, Visual, stable_id
    from lyricsync.video_only import build_empty_translations
    left=track('x星',[20,100,590,200],1.,1.31,.64,9,[EDGE,WORD],assembled=True)
    good=track('星',[500,130,590,170],1.5,1.533,.96,1,[WORD])
    right=deepcopy(left);right.start,right.end=1.7,2.
    raw=[Visual(identity,v.text,v.start,v.end,v.box,v.confidence,1,evidence=v.evidence)
         for identity,v in zip(('a','b','c'),[left,good,right])]
    project=Project(visuals=raw,settings={'roi':ROI})
    first,=build_empty_translations(project)
    assert (first.id,first.start,first.end)==(stable_id('ocr-empty','b'),1.,2.)
    assert first.visual_ids==['b','a','c'] and project.visuals==raw
    assert build_empty_translations(project)==[first]

def test_same_high_scoring_component_on_both_sides_of_single_frame():
    left=track('x星',[20,100,590,200],1.,1.31,.64,9,[EDGE,WORD],assembled=True)
    good=track('星',[500,130,590,170],1.5,1.533,.96,1,[WORD])
    right=deepcopy(left)
    right.start,right.end=1.7,2.
    inputs=[left,good,right]
    assert list(observed_groups(inputs,ROI,3))==[('星',inputs)]
    assert len(list(observed_groups(inputs,None,3)))==3
    right.evidence['roi_configuration']=[{'id':'region1'}, {'id':'region2'}]
    assert len(list(observed_groups(inputs,ROI,3)))==3
    del right.evidence['roi_configuration']
    right.evidence['components'][0]['evidence']['components'][1]['box']=[200,10,260,40]
    assert len(list(observed_groups(inputs,ROI,3)))==3


@pytest.mark.parametrize('text,expected', [('oh yeah!',True),('hey～wow',True),('おー！',False),
    ('お〜いぇい！',True),('星',False),('yeah right',False),('おいで',False),('好き～！',False)])
def test_compound_cheers_need_distinct_closed_vocabulary_tokens(text,expected):
    assert interjection(text) is expected
