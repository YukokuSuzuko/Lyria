"""Independent graph safety review. Failures are findings, not waived acceptance."""
from copy import copy,deepcopy
import pytest
from lyricsync.model import Project,Mapping,Lyric,Translation,Visual
from lyricsync.alignment_graph import AlignmentGraph


def sample():
    return Project(lyrics=[Lyric('l','source',None,None)],translations=[Translation('t','translation')],
        visuals=[Visual('v','source',0,1,[0,0,100,20],.9,4)],
        mappings=[Mapping('a',['t'],['l'],['v'],0,1,.9),Mapping('b',[],[],[],1,2,.3)])


def test_nested_reasons_write_through_to_both_legacy_link_metadata():
    p=sample();p.mappings[0].reasons.append('user-visible reason')
    assert p.alignment_graph.visual_source_edges['a']['reasons']==p.mappings[0].reasons
    assert p.alignment_graph.source_translation_edges['a']['reasons']==p.mappings[0].reasons


def test_reorder_preserves_held_nested_reference():
    p=sample();a=p.mappings[0];links=a.lyric_ids
    p.mappings.reverse();links.clear()
    assert a.lyric_ids==[]


def test_removed_view_detaches_but_remaining_view_stays_live():
    p=sample();a=p.mappings[0];b=p.mappings[1];del p.mappings[0]
    a.start=99;b.start=3
    assert a.start==99 and 'a' not in p.alignment_graph.cue_specs
    assert p.alignment_graph.cue_specs['b']['start']==3


def test_copies_do_not_share_graph_or_nested_payload():
    p=sample()
    for q in [copy(p),deepcopy(p)]:
        q.mappings[0].lyric_ids.clear();q.mappings[0].speaker_style['x']={'y':1}
        assert p.mappings[0].lyric_ids==['l'] and p.mappings[0].speaker_style=={}
    a=copy(p.mappings[0]);a.lyric_ids.clear();assert p.mappings[0].lyric_ids==['l']


def test_mismatched_mirror_and_unknown_graph_fields_rejected():
    p=sample();d=p.to_dict();d['mappings'][0]['start']=99
    with pytest.raises(ValueError):Project.from_dict(d)
    d=p.to_dict();d['alignment_graph']['extra']='bad'
    with pytest.raises(ValueError):Project.from_dict(d)


def test_dangling_occurrence_reference_rejected_on_project_load():
    p=sample();p.mappings[0].visual_ids=['missing']
    with pytest.raises(ValueError):Project.from_dict(p.to_dict())


def test_nonfinite_and_out_of_range_edge_confidence_rejected():
    for bad in [float('inf'),-1.,2.,True]:
        d=sample().to_dict();d['alignment_graph']['visual_source_edges'][0]['confidence']=bad
        with pytest.raises(ValueError):Project.from_dict(d)


def test_independent_edge_metadata_survives_reverse_and_roundtrip():
    p=sample();g=p.alignment_graph;g.visual_source_edges['a']['state']='manual_locked';g.source_translation_edges['a']['state']='uncertain'
    p.mappings.reverse();q=Project.from_dict(p.to_dict())
    assert q.alignment_graph.visual_source_edges['a']['state']=='manual_locked'
    assert q.alignment_graph.source_translation_edges['a']['state']=='uncertain'


def test_nested_payload_writes_are_graph_authoritative():
    p=sample();a=p.mappings[0];a.text_segments.append({'start':0,'end':1,'text':'x'});a.text_segments[0]['text']='y'
    a.style_overrides['outline']={'width':2};a.style_overrides['outline']['width']=3
    assert p.alignment_graph.cue_specs['a']['text_segments'][0]['text']=='y'
    assert p.alignment_graph.cue_specs['a']['style_overrides']['outline']['width']==3
