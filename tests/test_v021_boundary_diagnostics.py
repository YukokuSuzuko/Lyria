from copy import deepcopy
from lyricsync.model import Project,Lyric,Visual,Mapping
from lyricsync.frames import FrameIndex
from lyricsync.pairing_graph import diagnose


def sample(text='abcdefghij',gap=.2,box=None):
    a=Visual('a','abcdefghij',0,1,[0,100,200,130],.95,10)
    b=Visual('b',text,1+gap,2,[0,100,200,130] if box is None else box,.95,10)
    p=Project(lyrics=[Lyric('l','abcdefghij',None,None)],visuals=[a,b],
              mappings=[Mapping('m',[],['l'],['a'],0,1,.95)])
    return p,FrameIndex([i/10 for i in range(30)],3.)


def codes(p,index):return {c for row in diagnose(p,index) for c in row['codes']}


def test_one_to_three_decoded_frame_gap_is_reported_without_mutation():
    for gap in [.1,.2,.3]:
        p,index=sample(gap=gap);before=deepcopy(p.to_dict())
        result=diagnose(p,index)
        assert 'boundary_short_gap' in codes(p,index)
        evidence=result[0]['boundary_evidence'][0]
        assert evidence['gap_frames']==round(gap*10)
        assert p.to_dict()==before and 'confidence' not in evidence


def test_adjacent_same_box_small_text_change_is_only_a_warning():
    p,index=sample('abcdefghik',gap=0)
    assert 'boundary_text_flicker' in codes(p,index)
    assert 'boundary_short_gap' not in codes(p,index)


def test_no_index_no_fabricated_frame_count_and_no_low_text_match_diagnosis():
    p,index=sample('abcdefghik')
    assert not any(c.startswith('boundary_') for c in codes(p,None))
    p.lyrics[0].text='completely unrelated'
    assert not any(c.startswith('boundary_') for c in codes(p,index))


def test_different_slot_overlap_long_gap_and_short_words_do_not_trigger():
    for kwargs in [dict(box=[0,200,200,230]),dict(gap=-.2),dict(gap=.4),dict(text='abc')]:
        p,index=sample(**kwargs)
        assert not any(c.startswith('boundary_') for c in codes(p,index))


def test_both_measured_brackets_retained_without_invented_confidence():
    p,index=sample(gap=.4);p.visuals[0].start_bracket=[0,.12];p.visuals[0].end_bracket=[.9,1.1]
    e=diagnose(p,index)[0]['boundary_evidence']
    assert {x['edge'] for x in e}=={'start','end'}
    assert all('confidence' not in x and 'bracket' in x for x in e)


def test_vfr_counts_decoded_frames_not_average_fps():
    p,_=sample(gap=.3);index=FrameIndex([0,.3,.7,1.,1.01,1.3,1.7,2.],2.2)
    e=diagnose(p,index)[0]['boundary_evidence'][0]
    assert e['gap_frames']==2
