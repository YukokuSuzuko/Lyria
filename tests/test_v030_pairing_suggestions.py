from copy import deepcopy
import pytest
from desktop_qt.original_pairing_suggestions import materialize
from lyricsync.capsule_pairing import state
from tests.test_v022_capsule_pairing import sample

def test_ai_uses_only_existing_slices_preserves_private_fields_and_unused_words():
    p=sample();p.mappings[0].speaker_name='Solo'
    before=deepcopy(p.to_dict())
    q=materialize(p,{'groups':[{'cue':'m1','parts':[['t1',0,1],['t2',0,3]]}]})
    assert p.to_dict()==before
    assert q.mappings[0].speaker_name=='Solo'
    assert [(r.start,r.end) for r in q.mappings]==[(r.start,r.end) for r in p.mappings]
    assert q.mappings[0].text_override=='甲\n第二句'
    assert any(x['text']=='👩‍👩‍👧‍👦乙' for x in state(q)['pieces'])
    assert [t.text for t in q.translations[:2]]==[t.text for t in p.translations]

@pytest.mark.parametrize('groups',[
    [{'cue':'missing','parts':[['t1',0,1]]}],
    [{'cue':'m1','parts':[['t1',1,2]]}],
    [{'cue':'m1','parts':[['t2',0,3],['t2',1,2]]}],
    [{'cue':'m1','parts':[['t1',0,1]],'text':'生成的新译文'}],
])
def test_invalid_suggestion_is_atomic(groups):
    p=sample();before=p.to_dict()
    with pytest.raises(ValueError):materialize(p,{'groups':groups})
    assert p.to_dict()==before

def test_locked_original_cannot_be_replaced():
    p=sample();p.mappings[0].locked=True;before=p.to_dict()
    with pytest.raises(ValueError):materialize(p,{'groups':[{'cue':'m1','parts':[['t2',0,1]]}]})
    assert p.to_dict()==before

