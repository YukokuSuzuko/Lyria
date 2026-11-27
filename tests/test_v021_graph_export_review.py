"""Independent export/history review of the authoritative graph projection."""
from copy import deepcopy
from dataclasses import asdict
import pytest
from lyricsync.model import Project, Mapping, Lyric, Translation, Visual
from lyricsync.export import export_project, render_project_ass, validate_project
from lyricsync.editing import History


def sample():
    p=Project(lyrics=[Lyric('l','original evidence',0.,1.)],
        translations=[Translation('t','export target')],
        visuals=[Visual('v','raw OCR',0.,1.,[],.9,4)],
        mappings=[Mapping('a',['t'],['l'],['v'],0.,1.,.9,confirmed=True)])
    p.video={'width':1920,'height':1080,'duration':20}
    g=p.alignment_graph
    g.cue_specs['a']['start']=2.25;g.cue_specs['a']['end']=3.5
    g.visual_source_edges['a'].update(state='manual_locked',confidence=.73,reasons=['visual decision'])
    g.source_translation_edges['a'].update(state='uncertain',confidence=.31,reasons=['translation decision'])
    return p


@pytest.mark.parametrize('suffix,stamp',[('.ass','0:00:02.25'),('.srt','00:00:02,250'),('.lrc','[00:02.25]')])
def test_export_reads_graph_and_preserves_all_evidence(tmp_path,suffix,stamp):
    p=sample();before=deepcopy(p.to_dict());out=tmp_path/('subtitle'+suffix)
    export_project(p,out)
    text=out.read_text(encoding='utf-8-sig')
    assert stamp in text and 'export target' in text
    assert p.to_dict()==before
    assert 'raw OCR' not in text and 'original evidence' not in text


def test_styled_ass_and_qc_validation_are_read_only_graph_consumers():
    p=sample();before=deepcopy(p.to_dict())
    assert '0:00:02.25' in render_project_ass(p)
    assert validate_project(p)==[]
    assert p.to_dict()==before
    p.alignment_graph.cue_specs['a']['end']=21.
    assert validate_project(p)
    assert p.visuals[0].end==1. and p.lyrics[0].end==1.


def test_history_transaction_undo_redo_retains_independent_edges():
    p=sample();before=deepcopy(p.to_dict());h=History()
    with h.transaction(p,'move') as draft:
        draft.mappings[0].start=2.5
    after=deepcopy(p.to_dict())
    assert after['alignment_graph']['visual_source_edges']==before['alignment_graph']['visual_source_edges']
    assert after['alignment_graph']['source_translation_edges']==before['alignment_graph']['source_translation_edges']
    q,label=h.restore(p);assert label=='move' and q.to_dict()==before
    r,label=h.restore(q,redo=True);assert r.to_dict()==after
    r.mappings[0].start=2.75
    assert r.alignment_graph.cue_specs['a']['start']==2.75
    assert q.mappings[0].start==2.25


def test_history_failed_transaction_leaves_graph_and_stacks_intact():
    p=sample();before=deepcopy(p.to_dict());h=History()
    with pytest.raises(RuntimeError):
        with h.transaction(p,'abort') as draft:
            draft.alignment_graph.source_translation_edges['a']['reasons']=['changed']
            raise RuntimeError('cancel')
    assert p.to_dict()==before and not h.undo_stack and not h.redo_stack


def test_mapping_asdict_serializes_projection_not_binding():
    p=sample();d=asdict(p.mappings[0])
    assert d['start']==2.25 and '_graph_binding' not in d
    d['translation_ids'].clear()
    assert p.mappings[0].translation_ids==['t']


def test_qc_current_project_uses_graph_projection_without_rewriting_edges(tmp_path,monkeypatch):
    from lyricsync import audit
    p=sample();before=deepcopy(p.to_dict())
    video=tmp_path/'mock-video.mp4';video.write_bytes(b'not decoded in this unit test')
    translation=tmp_path/'translation.srt';translation.write_text('1\n00:00:00,000 --> 00:00:01,000\nexport target\n',encoding='utf-8')
    monkeypatch.setattr(audit,'Video',lambda path:type('VideoInfo',(),{'info':{'duration':20,'width':1920,'height':1080,'fps':25}})())
    monkeypatch.setattr(audit,'_audit_project',lambda path:(p,{'format':'unit-test'}))
    report=audit.local_audit(video,translation,tmp_path/'audit',settings={'audit_use_current_project':True},_observed=[])
    assert report['expected_cues'][0]['start']==2.25
    assert report['expected_cues'][0]['end']==3.5
    assert report['expected_cues'][0]['text']=='export target'
    assert p.to_dict()==before
