from copy import deepcopy
from lyricsync.model import Visual, Project, Translation
from lyricsync.ocr_correspondence import match_visual_project


def test_voted_kana_exclamation_keeps_independent_visual_interval():
    observations = [
        {'text': 'な～いえ～！', 'confidence': .72406},
        {'text': 'な～いえ～！', 'confidence': .72330},
        {'text': 'お〜いえ～！', 'confidence': .74483},
    ]
    first = Visual('cheer', observations[0]['text'], 1., 2., [20,10,150,40], .73, 3,
                   evidence={'components': observations, 'frame_count': 29})
    second = Visual('phrase', 'その手を', 2., 3.7, [20,10,150,40], .93, 5)
    p = Project(visuals=[first,second], translations=[Translation('hand','牵着手')])
    original = deepcopy(p.visuals)
    match_visual_project(p)
    assert [(r.visual_ids,r.translation_ids,r.start,r.end) for r in p.mappings] == [
        (['cheer'],[],1.,2.), (['phrase'],['hand'],2.,3.7)]
    assert p.visuals == original


def test_ordinary_shared_words_can_still_group_two_to_one():
    p = Project(visuals=[
        Visual('a','前半の手',1.,2.,[15,8,180,40],.9,5),
        Visual('b','後半の声',2.,3.,[15,8,180,40],.9,5)],
        translations=[Translation('t','前半後半的手与声')])
    match_visual_project(p)
    assert [(r.visual_ids,r.translation_ids) for r in p.mappings] == [(['a','b'],['t'])]
