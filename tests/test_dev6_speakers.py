from copy import deepcopy
import pytest
from lyricsync.model import Project, Mapping
from lyricsync.editing import History, split_mapping, merge_mappings
from lyricsync.speakers import assign_speaker,clear_speaker,default_style,validate_speakers
from lyricsync.export import project_layout_rows,render_project_ass,export_project
from lyricsync.project_document import ProjectDocument,write_document,load_document


def project():
    return Project(mappings=[Mapping('a',[],start=0,end=2,text_override='A {literal}',text_role='source_import',confirmed=True),
                             Mapping('b',[],start=1,end=3,text_override='B',text_role='source_import',confirmed=True)])


def test_assign_scope_frozen_style_and_undo():
    p=project();h=History();before=p.to_dict();style=default_style(p,'lilac')
    identity=assign_speaker(p,h,['a'],'Singer A',style)
    assert p.mappings[1].speaker_id=='' and not p.mappings[1].speaker_style
    style2=default_style(p,'cyan');assign_speaker(p,h,['b'],'Singer A',style2,speaker_id=identity)
    assert p.mappings[0].speaker_style['color']=='#D6B5FF'
    assign_speaker(p,h,['a'],'Singer A',style2,speaker_id=identity)
    assert p.mappings[0].speaker_style['color']=='#D6B5FF'
    assign_speaker(p,h,['a'],'Singer A',style2,speaker_id=identity,replace_styles=True)
    assert p.mappings[0].speaker_style['color']=='#9BE7FF'
    p,_=h.restore(p);assert p.mappings[0].speaker_style['color']=='#D6B5FF'
    while h.undo_stack:p,_=h.restore(p)
    assert p.to_dict()==before


def test_roundtrip_and_shared_ass_style(tmp_path):
    p=project();h=History();style=default_style(p,'cyan');style.update(bold=True,italic=True,size=40)
    assign_speaker(p,h,['a'],'A,tag{\\x}',style)
    doc=ProjectDocument(p);path=tmp_path/'p.lyria';write_document(doc.snapshot(),path)
    loaded=load_document(path).project
    assert loaded.to_dict()==p.to_dict()
    layout=project_layout_rows(loaded);assert layout[0]['style']==style
    target=tmp_path/'result.ass';export_project(loaded,target,draft=True)
    text=target.read_text(encoding='utf-8-sig')
    assert '&H00FFE79B' in text and ',40,' in text and ',-1,-1,0,0,' in text
    assert 'A，tag｛＼x｝' in text
    assert len([line for line in text.splitlines() if line.startswith('Dialogue:')])==2


def test_split_inherits_and_conflicting_merge_refuses():
    p=project();h=History();assign_speaker(p,h,['a'],'A',default_style(p))
    with pytest.raises(ValueError,match='说话人'):merge_mappings(p,['a','b'])
    a,b=split_mapping(p,'a',2,1)
    assert a.speaker_id==b.speaker_id and a.speaker_style==b.speaker_style


def test_locked_batch_is_atomic_and_clear_retains_manual_style():
    p=project();h=History();p.settings['manual_locked_ids']=['b'];before=p.to_dict()
    with pytest.raises(ValueError):assign_speaker(p,h,['a','b'],'A',default_style(p))
    assert p.to_dict()==before and not h.undo_stack
    assign_speaker(p,h,['a'],'A',default_style(p))
    clear_speaker(p,h,['a']);assert p.mappings[0].speaker_id=='' and p.mappings[0].speaker_style


@pytest.mark.parametrize('key,value',[('font','Arial\nStyle: inject'),('font','X,Y'),('color','red'),('size',True),('bold',1)])
def test_unsafe_style_rejected_atomically(key,value):
    p=project();h=History();style=default_style(p);style[key]=value
    with pytest.raises(ValueError):assign_speaker(p,h,['a'],'A',style)
    assert not h.undo_stack and not p.mappings[0].speaker_id


def test_corrupt_saved_speaker_reference_rejected():
    p=project();p.mappings[0].speaker_id='missing'
    with pytest.raises(ValueError):p.validate_timeline()


def test_legacy_no_speaker_assignment_is_unchanged():
    p=project();raw=p.to_dict();raw.pop('alignment_graph')  # actual pre-graph legacy file
    for row in raw['mappings']:row.pop('speaker_id');row.pop('speaker_style')
    loaded=Project.from_dict(raw).validate_timeline()
    assert not any(r.speaker_id or r.speaker_style for r in loaded.mappings)
