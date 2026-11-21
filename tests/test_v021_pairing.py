"""v0.21 structural contracts; synthetic fixtures are not song accuracy claims."""
from copy import deepcopy
import pytest
from lyricsync.model import Project,Lyric,Translation,Visual,Mapping
from lyricsync.pairing_graph import correction,graph_snapshot,diagnose
from lyricsync.sequence_alignment import align_observations
from lyricsync.matching import associate_visuals,match_project


def sample(n=5):
    p=Project(lyrics=[Lyric(f'l{i}',f'unique word {i}',None,None) for i in range(n)],
      translations=[Translation(f't{i}',f'translated {i}') for i in range(n)],
      visuals=[Visual(f'v{i}',f'unique word {i}',i*2.,i*2.+1.,[],.99,5) for i in range(n)])
    p.mappings=[Mapping(f'm{i}',[f't{i}'],[f'l{i}'],[f'v{i}'],i*2.,i*2.+1.,.9) for i in range(n)]
    return p


def timing(p):return [(r.id,r.start,r.end,r.visual_ids,r.text_segments) for r in p.mappings]


@pytest.mark.parametrize('column',['source','translation'])
def test_slip_preserves_evidence_times_and_displaced_tail(column):
    p=sample();before=deepcopy(p.to_dict())
    d=correction(p,operation='slip_down',column=column,cue_ids=['m1'])
    assert p.to_dict()==before and timing(d)==timing(p)
    assert d.visuals==p.visuals and d.lyrics==p.lyrics and d.translations==p.translations
    ids=lambda r:r.lyric_ids if column=='source' else r.translation_ids
    assert ids(d.mappings[1])==[] and ids(d.mappings[2])==ids(p.mappings[1])
    assert d.settings['pairing_unbound_tray'][-1]['payload']['ids']==ids(p.mappings[-1])
    restored=correction(d,operation='restore_tray',column=column,cue_ids=['m1'])
    assert ids(restored.mappings[1])==ids(p.mappings[-1])


@pytest.mark.parametrize('protection',['locked','confirmed','human_edited'])
def test_slip_stops_before_manual_boundary(protection):
    p=sample();setattr(p.mappings[3],protection,True)
    d=correction(p,operation='slip_down',cue_ids=['m0'])
    assert d.mappings[3:]==p.mappings[3:]
    assert d.settings['pairing_unbound_tray'][-1]['payload']['ids']==['l2']


def test_explicit_paragraph_is_a_slip_boundary():
    p=sample()
    for i,l in enumerate(p.lyrics):l.subtitle_metadata={'paragraph_index':int(i>=3)}
    d=correction(p,operation='slip_up',cue_ids=['m0'])
    assert d.mappings[3:]==p.mappings[3:]
    assert d.mappings[2].lyric_ids==[]


@pytest.mark.parametrize('operation',['bind','merge_next','slip_down'])
def test_locked_selection_never_changes(operation):
    p=sample();p.mappings[0].locked=True;before=p.to_dict()
    with pytest.raises(ValueError):correction(p,operation=operation,cue_ids=['m0'],node_ids=['l1'])
    assert p.to_dict()==before


def test_two_times_one_source_and_two_sources_one_time_are_explicit():
    p=sample();d=correction(p,operation='bind',cue_ids=['m0','m1'],node_ids=['l0'])
    assert d.mappings[0].lyric_ids==d.mappings[1].lyric_ids==['l0']
    d=correction(p,operation='bind',cue_ids=['m0'],node_ids=['l0','l1'])
    assert d.mappings[0].lyric_ids==['l0','l1'] and timing(d)==timing(p)


def test_graph_roundtrip_and_no_fake_original():
    p=sample();p.lyrics=[]
    for r in p.mappings:r.lyric_ids=[]
    graph=graph_snapshot(p);assert graph['lyric_instances']==[]
    assert all(not e['lyric_ids'] for e in graph['visual_source_edges'])
    p.settings['alignment_graph_v1']=graph
    assert Project.from_dict(p.to_dict()).settings['alignment_graph_v1']==graph


def test_transition_retained_not_matched_or_generated():
    from lyricsync.video_only import build_empty_translations
    p=sample(1);p.visuals[0].evidence={'eligible_for_alignment':False,'observation_status':'transition_noise'}
    assert associate_visuals(p.lyrics,p.visuals)[1]==['v0']
    p.mappings=[];build_empty_translations(p)
    assert len(p.visuals)==1 and not p.mappings


def test_manual_anchor_partitions_and_crossing_stops_proposals():
    p=sample();edges=align_observations(p.lyrics,p.visuals,[dict(lyric_ids=['l2'],visual_ids=['v2'])])
    assert any(e['state']=='manual_locked' and e['lyric_ids']==['l2'] for e in edges)
    edges=align_observations(p.lyrics,p.visuals,[dict(lyric_ids=['l1'],visual_ids=['v3']),dict(lyric_ids=['l3'],visual_ids=['v1'])])
    assert all(e['state'] in ('manual_locked','manual_anchor_conflict') for e in edges)


def test_unique_anchors_contain_local_split():
    p=sample();p.visuals[2].text='wrong observation'
    edges=align_observations(p.lyrics,p.visuals)
    assert any(e['lyric_ids']==['l3'] and e['visual_ids']==['v3'] and e['anchor'] for e in edges)


def test_txt_blank_paragraphs_survive_original_import(tmp_path):
    from lyricsync.subtitles import import_original,import_translation
    path=tmp_path/'text.txt';path.write_text('alpha\nbeta\n\ngamma\n',encoding='utf-8')
    ls,_=import_original(path);ts=import_translation(path)
    assert [l.subtitle_metadata['paragraph_index'] for l in ls]==[0,0,1]
    assert [t.subtitle_metadata['paragraph_index'] for t in ts]==[0,0,1]


def test_workbench_real_bridge_undo_and_stale_guard(bridge,tmp_path):
    from desktop_qt.pairing_workbench import PairingWorkbench
    from lyricsync.project_document import write_document,load_document
    p=sample();bridge._adopt_project(p,'fixture');bridge._changed()
    before=deepcopy(bridge.document.project.to_dict());bench=PairingWorkbench(bridge)
    bench.table.selectRow(0);bench._apply('slip_down')
    after=deepcopy(bridge.document.project.to_dict());assert after!=before
    path=tmp_path/'saved.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
    assert load_document(path).project.to_dict()==after
    bench._undo();assert bridge.document.project.to_dict()==before
    bridge.document.revision+=1
    with pytest.raises(ValueError):bench._guard()
    bench.close()

from test_qt_entry_initialization_dev5 import bridge

def test_partial_shared_observation_groups_do_not_clone_interval():
    from lyricsync.matching import _coalesce_shared_observation_groups
    a,b,c=[Lyric(x,x,None,None) for x in 'abc']
    v=Visual('shared','ab',1.,2.,[],.99,5);w=Visual('independent','c',3.,4.,[],.99,5)
    note='\u5171\u4eab\u6574\u53e5\u89c6\u89c9\u533a\u95f4'
    associations={'a':[(v,.9,[note])],'b':[(v,.9,[note])],'c':[(w,.9,[])]}
    groups=[([a],[Translation('t1','first')],.5,[]),([b,c],[Translation('t2','second')],.5,[])]
    merged=_coalesce_shared_observation_groups(groups,associations)
    assert len(merged)==1 and [x.id for x in merged[0][0]]==['a','b','c']
