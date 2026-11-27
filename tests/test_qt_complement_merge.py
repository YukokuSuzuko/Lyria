"""Explicit text/time pairing preserves the timed clip and unrelated rows."""
import pytest
from copy import deepcopy
from dataclasses import asdict
from test_qt_timeline_commands import application, bridge
from lyricsync.model import Mapping, Translation
from lyricsync.editing import mapping_text
from lyricsync.project_document import write_document, load_document


@pytest.mark.parametrize('reverse', [False, True])
def test_complementary_nonadjacent_pair_keeps_timed_identity_and_undo(bridge, tmp_path, reverse):
    p=bridge.document.project
    p.translations=[Translation('text','完整中文'),Translation('other','其他字幕')]
    text=Mapping('text-row',['text'])
    timed=Mapping('timed-row',[],start=.2,end=.7,text_override='')
    other=Mapping('other-row',['other'],start=.7,end=1.)
    p.mappings=[timed,other,text] if reverse else [text,other,timed]
    bridge.selected=text.id;bridge._changed()
    before=bridge.document.project.to_dict()
    untouched=(asdict(other),deepcopy(p.alignment_graph.visual_source_edges[other.id]),deepcopy(p.alignment_graph.source_translation_edges[other.id]))
    assert bridge.command('row.merge',{'id':text.id,'ids':[text.id,timed.id]})
    p=bridge.document.project
    assert [m.id for m in p.mappings]==(['timed-row','other-row'] if reverse else ['other-row','timed-row'])
    merged=bridge._row('timed-row')
    assert (merged.start,merged.end,mapping_text(p,merged))==(.2,.7,'完整中文')
    assert bridge.selected=='timed-row'
    assert (asdict(bridge._row('other-row')),p.alignment_graph.visual_source_edges['other-row'],p.alignment_graph.source_translation_edges['other-row'])==untouched
    after=p.to_dict()
    path=tmp_path/'paired.lyria';write_document(bridge.document.snapshot(),path)
    assert load_document(path).project.to_dict()==after
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{}) and bridge.document.project.to_dict()==after


def test_nonadjacent_nonempty_rows_still_reject_without_loss(bridge):
    p=bridge.document.project
    p.mappings.insert(1,Mapping('between',[],start=.1,end=.2,text_override='保留'))
    before=p.to_dict()
    assert not bridge.command('row.merge',{'id':'target','ids':['overlap','target']})
    assert p.to_dict()==before and not bridge.history.undo_stack
