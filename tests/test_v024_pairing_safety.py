from copy import deepcopy
import pytest
from lyricsync.model import Project,Visual,Translation,Mapping
from lyricsync.ocr_correspondence import source_anchors
from lyricsync.translation_pairing_draft import make_draft,apply_draft
from lyricsync.pairing_session import PairingSession
from lyricsync.capsule_pairing import operate,state
from tests.test_v022_capsule_pairing import sample as old_sample

def sample():
    return Project(visuals=[Visual('v1','one',1.,2.,[0,0,80,20],.9,3),
                           Visual('v2','two',3.,4.,[0,0,80,20],.9,3),
                           Visual('v3','three',5.,6.,[0,0,80,20],.9,3)],
                   translations=[Translation('t1','first'),Translation('t2','second'),Translation('t3','third')],
                   mappings=[Mapping('m1',['t1'],visual_ids=['v1'],start=1.,end=2.),
                             Mapping('m2',['t2'],visual_ids=['v2'],start=3.,end=4.),
                             Mapping('m3',['t3'],visual_ids=['v3'],start=5.,end=6.,locked=True)])

def test_partial_repair_keeps_detached_anchor_and_translation_and_order():
    p=sample(); before=deepcopy(p.to_dict()); anchors=source_anchors(p)
    response={'groups':[{'anchor_ids':[anchors[0].id],'slices':[{'translation_id':'t2','start':0,'end':6}]}]}
    q=apply_draft(p,make_draft(p,response),confirmed=True)
    assert p.to_dict()==before
    assert {v for row in q.mappings for v in row.visual_ids}=={'v1','v2','v3'}
    assert [r.start for r in q.mappings]==[1.,3.,5.,None]
    assert q.mappings[-1].translation_ids==['t1']
    assert q.mappings[2].locked
    session=PairingSession(q)
    assert session.data['groups'] and not session.reserved_anchors.intersection([anchors[0].id])
    # Ordinary generated rows remain editable; genuine later edits stay protected.
    q.mappings[0].human_edited=True
    assert anchors[0].id in PairingSession(q).reserved_anchors

def test_legacy_capsules_open_without_guessing_offsets_and_explicit_detach_keeps_edit():
    p=operate(old_sample(),'source',ids=['m1']); gid=state(p)['groups'][0]['id']
    p=operate(p,'bind',ids=['t1'],target=gid)
    session=PairingSession(p)
    assert not any(x['translation_id']=='t1' for x in session.available)
    p.mappings[0].text_override='MANUAL TEXT'; before=deepcopy(p.mappings[0])
    q=operate(p,'detach_keep_text',ids=[gid])
    assert q.mappings[0]==before and state(q)['groups']==[]
    assert p.mappings[0].text_override=='MANUAL TEXT'

def test_ai_split_keeps_all_appearance_and_conflicting_merge_fails_without_history():
    from tests.test_api_migration_alignment import fixture, split, merge, response
    from lyricsync.api_alignment import make_plan, accept_plan
    from lyricsync.editing import History
    p,index=fixture()
    original=p.mappings[0]
    original.speaker_name='actor'; original.speaker_style={'color':'#123456'}
    original.style_id='style'; original.style_overrides={'font_size':28}
    attrs={key:deepcopy(getattr(original,key)) for key in ('speaker_name','speaker_style','style_id','style_overrides')}
    plan=make_plan(p,index,response(split()))
    accept_plan(p,plan,index,History())
    for row in p.mappings[:2]:
        assert all(getattr(row,key)==value for key,value in attrs.items())
    p,index=fixture();p.mappings[1].speaker_name='actor A';p.mappings[2].speaker_name='actor B'
    plan=make_plan(p,index,response(merge())); before=deepcopy(p.to_dict()); history=History()
    with pytest.raises(ValueError,match='说话人或样式'):
        accept_plan(p,plan,index,history)
    assert p.to_dict()==before and not history.undo_stack
