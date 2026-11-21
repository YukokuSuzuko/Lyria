"""Canonical graph/write-through migration contracts, not semantic accuracy."""
from copy import copy,deepcopy
from dataclasses import asdict
import pytest
from lyricsync.model import Project,Mapping,Lyric,Translation,Visual


def sample():
 return Project(lyrics=[Lyric('l','source',None,None)],translations=[Translation('t','target')],visuals=[Visual('v','source',1.,2.,[],.9,4)],mappings=[Mapping('a',['t'],['l'],['v'],1.,2.),Mapping('b',[],start=3.,end=4.)])


def test_fields_and_nested_links_write_to_authority():
 p=sample();r=p.mappings[0];r.start=1.2;r.lyric_ids.append('another');r.style_overrides['bold']=True
 assert p.alignment_graph.cue_specs['a']['start']==1.2
 assert p.alignment_graph.visual_source_edges['a']['lyric_ids']==['l','another']
 assert p.alignment_graph.cue_specs['a']['style_overrides']['bold']
 assert set(r.__dict__)=={'_graph_binding'}
 p.alignment_graph.visual_source_edges['a']['visual_ids']=['changed']
 assert r.visual_ids==['changed']


def test_detached_projection_and_cross_project_are_isolated():
 p=sample();rows=p.project_timeline(detached=True);rows[0].start=5
 assert p.mappings[0].start==1
 q=Project(mappings=list(p.mappings));q.mappings[0].start=6
 assert p.mappings[0].start==1 and q.mappings[0] is not p.mappings[0]
 for q in (copy(p),deepcopy(p)):
  q.mappings[0].translation_ids.clear();assert p.mappings[0].translation_ids==['t']


def test_atomic_sequence_and_identity():
 p=sample();a,b=p.mappings;p.mappings.reverse();assert list(p.mappings)==[b,a]
 p.mappings.sort(key=lambda r:r.id);assert p.mappings[0] is a
 before=p.to_dict()
 with pytest.raises(ValueError):p.mappings.extend([Mapping('c',[]),Mapping('a',[])])
 assert p.to_dict()==before
 p.mappings[1:2]=[Mapping('c',[]),Mapping('d',[])]
 assert [r.id for r in p.mappings]==['a','c','d'] and p.mappings[0] is a
 b.start=8;assert 'b' not in p.alignment_graph.cue_specs


def test_rekey_is_atomic():
 p=sample();r=p.mappings[0]
 with pytest.raises(ValueError):r.id='b'
 assert r.id=='a'
 r.id='new';assert p.alignment_graph.cue_order==['new','b'] and p.mappings[0] is r
 assert p.to_dict()['alignment_graph']['visual_source_edges'][0]['cue_id']=='new'


def test_roundtrip_legacy_graph_only_and_conflict():
 p=sample();value=p.to_dict();assert Project.from_dict(value).to_dict()==value
 old=deepcopy(value);old.pop('alignment_graph');assert Project.from_dict(old).to_dict()==value
 graph_only=deepcopy(value);graph_only.pop('mappings');assert Project.from_dict(graph_only).to_dict()==value
 damaged=deepcopy(value);damaged['mappings'][0]['start']=9
 with pytest.raises(ValueError,match='disagree'):Project.from_dict(damaged)
 damaged=deepcopy(value);damaged['alignment_graph']['version']=99
 with pytest.raises(ValueError,match='version'):Project.from_dict(damaged)


def test_time_only_replacement_preserves_independent_edge_metadata():
 p=sample();p.alignment_graph.visual_source_edges['a']['state']='manual_locked'
 p.alignment_graph.source_translation_edges['a']['reasons']=['independent translation decision']
 rows=p.project_timeline(detached=True);rows[0].start=1.1;p.replace_timeline(rows)
 assert p.alignment_graph.visual_source_edges['a']['state']=='manual_locked'
 assert p.alignment_graph.source_translation_edges['a']['reasons']==['independent translation decision']
 assert Project.from_dict(p.to_dict()).to_dict()==p.to_dict()


def test_unbound_entities_are_explicit_and_not_dropped():
 p=sample();p.lyrics.append(Lyric('unseen','unseen source',None,None));p.translations.append(Translation('extra','extra'))
 value=p.to_dict();edges=value['alignment_graph']['unbound_edges']
 assert any(e['lyric_ids']==['unseen'] and not e['visual_ids'] for e in edges)
 assert any(e['translation_ids']==['extra'] for e in edges)
 assert Project.from_dict(value).to_dict()==value


def test_graph_originated_change_projects_into_export():
 from lyricsync.export import validate_project
 p=sample();p.alignment_graph.cue_specs['a']['start']=1.3
 assert p.project_timeline()[0].start==1.3
 assert p.to_dict()['mappings'][0]['start']==1.3
 # Structural absence is legal for draft editing but export remains protected.
 p.alignment_graph.visual_source_edges['a']['visual_ids']=['missing']
 assert any('a' in str(x) for x in validate_project(p,draft=True))

def test_unbound_manual_relationship_survives_serialization():
 p=Project(lyrics=[Lyric('l1','one',None,None),Lyric('l2','two',None,None)])
 p.alignment_graph.unbound_edges=[dict(id='manual-skip',visual_ids=[],lyric_ids=['l1','l2'],translation_ids=[],state='manual_locked')]
 first=p.to_dict();second=Project.from_dict(first).to_dict()
 assert first==second
 assert second['alignment_graph']['unbound_edges']==p.alignment_graph.unbound_edges


@pytest.mark.parametrize('damage',[
 lambda g:g.update(version=True),lambda g:g.update(cue_order=[[]]),
 lambda g:g['cue_specs'][0].update(id=[]),
 lambda g:g['visual_source_edges'][0].update(visual_ids=[{}]),
 lambda g:g['cue_specs'][0].update(unexpected='field'),
])
def test_malformed_graph_is_consistent_value_error(damage):
 p=sample();value=p.to_dict();damage(value['alignment_graph'])
 with pytest.raises(ValueError):Project.from_dict(value)


def test_visual_group_block_has_explicit_many_to_many_and_state(bridge):
 from desktop_qt.pairing_workbench import PairingWorkbench
 from lyricsync.pairing_graph import visual_groups
 p=sample();p.mappings[1].lyric_ids=['l'];p.mappings[1].translation_ids=['t'];p.mappings[0].locked=True
 groups=visual_groups(p);assert len(groups)==1 and len(groups[0]['cue_ids'])==2
 assert groups[0]['lyric_ids']==['l'] and 'manual_locked' in groups[0]['states']
 bridge._adopt_project(p,'graph fixture');bridge._changed();w=PairingWorkbench(bridge)
 item=w.groups.topLevelItem(0)
 assert '2 ' in item.text(0) and item.childCount()==4
 assert 'a, b' in item.child(0).text(0)
 w._select_group(item,0);assert set(w._chosen())=={'a','b'};w.close()

from test_qt_entry_initialization_dev5 import bridge

def test_explicit_independent_edge_anchor_survives_rematch():
 from lyricsync.matching import match_project
 p=sample();row=p.mappings[0];p.alignment_graph.visual_source_edges['a']['state']='manual_locked'
 before=asdict(row);match_project(p)
 assert p.mappings[0] is row and asdict(row)==before
 assert p.alignment_graph.visual_source_edges['a']['state']=='manual_locked'


def test_confidence_state_updates_without_erasing_independent_lock():
 p=sample();r=p.mappings[0];r.confidence=.95
 assert p.alignment_graph.source_translation_edges['a']['state']=='auto_high'
 p.alignment_graph.visual_source_edges['a']['state']='manual_locked';r.confidence=.2
 assert p.alignment_graph.source_translation_edges['a']['state']=='uncertain'
 assert p.alignment_graph.visual_source_edges['a']['state']=='manual_locked'
 r.reasons=['edit'];r.reasons.append('nested')
 assert p.alignment_graph.source_translation_edges['a']['reasons']==['edit','nested']

def test_unbound_source_without_cue_is_in_uncertainty_tour(bridge):
 from desktop_qt.pairing_workbench import PairingWorkbench
 p=Project(lyrics=[Lyric('not-on-screen','Unlocated original',None,None)])
 bridge._adopt_project(p,'unbound');bridge._changed();w=PairingWorkbench(bridge)
 assert len(w.unbound_items)==1
 w._next_doubt();assert w.groups.currentItem() is w.unbound_items[0]
 assert 'not-on-screen' in w.status.text();w.close()

def test_independent_edge_reasons_do_not_mutate_cue_or_other_edge():
 p=sample();row=p.mappings[0];row.reasons=['shared default']
 p.alignment_graph.visual_source_edges['a']['reasons'].append('visual only')
 assert row.reasons==['shared default']
 assert p.alignment_graph.source_translation_edges['a']['reasons']==['shared default']
 row.reasons.append('cue edit')
 assert p.alignment_graph.source_translation_edges['a']['reasons']==['shared default','cue edit']
 assert p.alignment_graph.visual_source_edges['a']['reasons']==['shared default','visual only']
 assert Project.from_dict(p.to_dict()).to_dict()==p.to_dict()

def test_removed_nested_alias_cannot_mutate_reused_identity():
 p=sample();old=p.mappings[0];ids=old.lyric_ids;reasons=old.reasons
 p.mappings.pop(0);p.mappings.insert(0,Mapping('a',['t'],['l'],['v'],1.,2.))
 ids.clear();reasons.append('stale')
 assert p.mappings[0].lyric_ids==['l'] and p.mappings[0].reasons==[]
 assert p.alignment_graph.visual_source_edges['a']['reasons']==[]


def test_detached_replacement_detaches_prior_nested_references():
 p=sample();old=p.mappings[0];ids=old.lyric_ids;reasons=old.reasons
 p.replace_timeline(p.project_timeline(detached=True))
 ids.clear();reasons.append('stale')
 assert p.mappings[0].lyric_ids==['l'] and p.mappings[0].reasons==[]
 assert old is not p.mappings[0]

@pytest.mark.parametrize('namespace,node',[('lyric_ids','l'),('visual_ids','v'),('translation_ids','t')])
def test_bound_and_unbound_same_node_rejected_without_mutating_input(namespace,node):
 p=sample();value=p.to_dict()
 edge=dict(id='conflicting-manual-skip',visual_ids=[],lyric_ids=[],translation_ids=[],state='manual_locked');edge[namespace]=[node]
 value['alignment_graph']['unbound_edges'].append(edge);before=deepcopy(value)
 with pytest.raises(ValueError,match='Conflicting'):Project.from_dict(value)
 assert value==before


def test_duplicate_unbound_node_relationship_rejected():
 p=Project(lyrics=[Lyric('skipped','skip me',None,None)]);value=p.to_dict()
 edge=deepcopy(value['alignment_graph']['unbound_edges'][0]);edge['id']='second-skip';edge['state']='manual_locked'
 value['alignment_graph']['unbound_edges'].append(edge);before=deepcopy(value)
 with pytest.raises(ValueError,match='duplicate'):Project.from_dict(value)
 assert value==before

@pytest.mark.parametrize('confidence,state',[(.2,'auto_high'),(.95,'uncertain')])
def test_independent_edge_uncertainty_is_reviewed_without_mutation(confidence,state):
 from lyricsync.pairing_graph import diagnose,graph_snapshot
 p=sample();p.mappings[0].confidence=.95;p.mappings[0].confirmed=True
 p.alignment_graph.visual_source_edges['a']['state']='manual_locked'
 edge=p.alignment_graph.source_translation_edges['a'];edge['confidence']=confidence;edge['state']=state
 before=p.to_dict();diagnostics=diagnose(p);snapshot=graph_snapshot(p)
 issue=next(d for d in diagnostics if d['cue_id']=='a')
 assert 'low_confidence' in issue['codes'] or 'uncertain_graph_edge' in issue['codes']
 cue=next(r for r in snapshot['cues'] if r['id']=='a')
 assert cue['state']=='uncertain' and cue['review_confidence']==min(.95,confidence)
 assert p.to_dict()==before and p.mappings[0].confidence==.95
