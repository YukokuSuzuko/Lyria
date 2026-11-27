from lyricsync.model import Lyric,Translation
from lyricsync.translation_alignment import repetition_topology,topology_score,short_length_anomaly,structural_groups

def ls(texts):return [Lyric(str(i),x,None,None) for i,x in enumerate(texts)]
def ts(texts):return [Translation(str(i),x) for i,x in enumerate(texts)]

def test_cross_language_repeated_matrix_uses_instance_rank_and_spacing():
    a=repetition_topology(ls(['intro','chorusA','chorusB','bridge','chorusA','chorusB']))
    b=repetition_topology(ts(['前奏','同一个天空','再一起唱歌','间奏','同一个天空','再一起唱歌']),translation=True)
    assert a['matrix'][1][4] and b['matrix'][1][4]
    good,match=topology_score(a['signatures'][1],b['signatures'][1])
    wrong,_=topology_score(a['signatures'][1],b['signatures'][4])
    assert match and good>0 and wrong<0

def test_paraphrased_repeat_is_not_hard_rejected_or_marked_equal():
    a=repetition_topology(ls(['same','other','same']))
    b=repetition_topology(ts(['天空','其他','仰望天空']),translation=True)
    score,match=topology_score(a['signatures'][0],b['signatures'][0])
    assert score==0 and not match

def test_short_fuzzy_words_do_not_form_a_chorus():
    a=repetition_topology(ls(['abc','abd','abc']))
    assert a['matrix'][0][2] and not a['matrix'][0][1]

def test_extreme_short_length_is_diagnostic_not_mutation():
    a=ls(['啊']);b=ts(['这是一个很长很长的自由意译句子'])
    assert short_length_anomaly(a,b)
    assert not short_length_anomaly(ls(['普通长句文本']),ts(['比原句稍微长一些的翻译']))
    structural_groups(a,b)
    assert a[0].text=='啊' and b[0].text=='这是一个很长很长的自由意译句子'

def test_repetition_reasons_do_not_claim_semantic_anchor():
    groups=structural_groups(ls(['foo','bar','foo','bar']),ts(['甲甲','乙乙','甲甲','乙乙']))
    notes=[s for _,_,_,reasons in groups for s in reasons]
    assert any('自相似矩阵' in s and '非语义硬锚点' in s for s in notes)
    assert all(c<=.5 for _,_,c,_ in groups)
