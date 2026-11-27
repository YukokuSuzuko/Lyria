"""Nonblocking razor transaction, pending chains and explicit text allocation."""
import copy
from dataclasses import asdict
import pytest
from test_qt_timeline_commands import application, bridge
from lyricsync.editing import mapping_text, pending_text_links, pending_text_ids
from lyricsync.model import Lyric
from lyricsync.project_document import load_document, write_document
from lyricsync.export import validate_project, export_project


def cue_state(project,row):
    # Compare public cue values AND independent edge data, never adapter object identity.
    return {'cue':asdict(row),
            'visual_source':copy.deepcopy(project.alignment_graph.visual_source_edges[row.id]),
            'source_translation':copy.deepcopy(project.alignment_graph.source_translation_edges[row.id])}


def cut(bridge,identity='target',frame=4):
    assert bridge.command('timeline.razor',{'id':identity,'frame':frame}),bridge.state['error']
    p=bridge.document.project
    return [m for m in p.mappings if m.id!='overlap']


def allocate(bridge,left,offset):
    assert bridge.command('textAllocation.begin',{'id':left.id}),bridge.state['error']
    value=bridge.state['textAllocation']
    return bridge.command('textAllocation.apply',{**value,'offsetUtf16':offset})


@pytest.mark.parametrize('frame', [4,5])
def test_cut_exact_frames_preserves_text_provenance_and_one_undo(bridge,frame):
    p=bridge.document.project;p.lyrics=[Lyric('l','source longer than Chinese',None,None)]
    bridge._row('target').lyric_ids=['l'];bridge._row('target').confirmed=True
    before=p.to_dict();revision=bridge.document.revision
    a,b=cut(bridge,frame=frame);idx=bridge.media_controller.index
    assert (a.start,a.end,b.start,b.end)==(.2,idx.stamp(frame),idx.stamp(frame),1.)
    assert (idx.last(a.end),idx.first(b.start))==(frame-1,frame)
    assert mapping_text(p,a)=='前半后半' and mapping_text(p,b)==''
    assert a.lyric_ids==b.lyric_ids==['l'] and p.lyrics[0].text=='source longer than Chinese'
    assert not a.confirmed and not b.confirmed
    assert pending_text_ids(p)=={a.id,b.id}
    assert bridge.document.revision==revision+1 and len(bridge.history.undo_stack)==1
    assert 'frame_markers' not in p.settings
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before and bridge.selected=='target'


@pytest.mark.parametrize('frame',[0,1,2,3,6,8,9,True,1.2,'4'])
def test_invalid_cut_is_nonmutating(bridge,frame):
    before=bridge.document.snapshot()
    assert not bridge.command('timeline.razor',{'id':'target','frame':frame})
    assert bridge.document.snapshot()==before and not bridge.history.undo_stack


def test_single_frame_locked_composition_and_missing_index_reject(bridge):
    p=bridge.document.project
    for kind in ('single','lock','ime','index'):
        bridge._row('target').start=.2;bridge._row('target').end=1.;p.settings.pop('manual_locked_ids',None);bridge._editor_composing=False
        if kind=='single':bridge._row('target').end=.4
        if kind=='lock':p.settings['manual_locked_ids']=['target']
        if kind=='ime':bridge._editor_composing=True
        if kind=='index':bridge.media_controller.index=None
        before=bridge.document.snapshot()
        assert not bridge.command('timeline.razor',{'id':'target','frame':4})
        assert bridge.document.snapshot()==before and not bridge.history.undo_stack


def test_allocation_is_separate_undo_and_cancel_is_read_only(bridge):
    original=bridge.document.project.to_dict();a,b=cut(bridge);after=bridge.document.project.to_dict()
    identities=[m.id for m in bridge.document.project.mappings];times=[(m.start,m.end) for m in bridge.document.project.mappings]
    assert bridge.command('textAllocation.begin',{'id':a.id})
    assert bridge.command('textAllocation.cancel',{})
    assert bridge.document.project.to_dict()==after and len(bridge.history.undo_stack)==1
    assert allocate(bridge,a,2)
    assert [m.id for m in bridge.document.project.mappings]==identities
    assert [(m.start,m.end) for m in bridge.document.project.mappings]==times
    assert (mapping_text(bridge.document.project,a),mapping_text(bridge.document.project,b))==('前半','后半')
    assert not pending_text_links(bridge.document.project)
    assert len(bridge.history.undo_stack)==2 and not bridge.state['textAllocation']
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==after
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==original and bridge.selected=='target'
    assert bridge.command('history.redo',{});assert bridge.command('history.redo',{})
    assert not pending_text_links(bridge.document.project)


@pytest.mark.parametrize('text,offset', [('前👩‍👩‍👧‍👦后',3),('前e\u0301后',2),('前🇨🇳后',3)])
def test_grapheme_interiors_reject_without_mutation(bridge,text,offset):
    bridge._row('target').text_override=text;a,b=cut(bridge)
    assert bridge.command('textAllocation.begin',{'id':a.id})
    before=bridge.document.snapshot();value=bridge.state['textAllocation']
    assert not bridge.command('textAllocation.apply',{**value,'offsetUtf16':offset})
    assert bridge.document.snapshot()==before and len(bridge.history.undo_stack)==1


@pytest.mark.parametrize('text,prefix',[('前👩‍👩‍👧‍👦e\u0301\n后','前👩‍👩‍👧‍👦'),('前\r\n后','前\r\n'),('<b>前</b>\\N后','<b>前</b>')])
def test_unicode_multiline_literal_allocation(bridge,text,prefix):
    bridge._row('target').text_override=text;a,b=cut(bridge)
    assert allocate(bridge,a,len(prefix.replace('\r\n','\n').encode('utf-16-le'))//2)
    assert mapping_text(bridge.document.project,a)==prefix
    assert mapping_text(bridge.document.project,a)+mapping_text(bridge.document.project,b)==text


def chain(bridge):
    row=bridge._row('target');row.start=0;row.end=2.;row.text_override='甲乙丙丁戊己'
    a,b=cut(bridge,frame=5)
    rows=cut(bridge,a.id,3)
    return rows


def test_repeated_cuts_chain_allocation_and_no_guessing(bridge):
    a,b,c=chain(bridge);p=bridge.document.project
    assert [mapping_text(p,m) for m in (a,b,c)]==['甲乙丙丁戊己','','']
    assert pending_text_links(p)==[{'left':b.id,'right':c.id},{'left':a.id,'right':b.id}]
    assert bridge.command('textAllocation.begin',{'id':c.id})
    assert bridge.state['textAllocation']['leftId']==a.id
    assert bridge.state['textAllocation']['rightId']==b.id
    assert [mapping_text(p,m) for m in (a,b,c)]==['甲乙丙丁戊己','','']
    assert allocate(bridge,a,2)
    assert [mapping_text(p,m) for m in (a,b,c)]==['甲乙','丙丁戊己','']
    assert allocate(bridge,b,2)
    assert [mapping_text(p,m) for m in (a,b,c)]==['甲乙','丙丁','戊己']
    assert not pending_text_links(p)
    assert len(bridge.history.undo_stack)==4


def test_cut_empty_segment_keeps_chain(bridge):
    a,b=cut(bridge);rows=cut(bridge,b.id,5);p=bridge.document.project
    assert ''.join(mapping_text(p,m) for m in rows)=='前半后半'
    assert len(pending_text_links(p))==2 and len(pending_text_ids(p))==3


def test_merge_collapses_pending_without_losing_text(bridge):
    a,b,c=chain(bridge)
    assert bridge.command('row.merge',{'ids':[a.id,b.id]})
    merged=bridge._row();p=bridge.document.project
    assert mapping_text(p,merged)=='甲乙丙丁戊己'
    assert pending_text_links(p)==[{'left':merged.id,'right':c.id}]
    assert bridge.command('row.merge',{'ids':[merged.id,c.id]})
    assert not pending_text_links(bridge.document.project)
    assert mapping_text(bridge.document.project,bridge._row())=='甲乙丙丁戊己'


def test_delete_middle_reconnects_and_delete_source_keeps_blank_warning(bridge):
    a,b,c=chain(bridge)
    assert bridge.command('row.delete',{'id':b.id})
    assert pending_text_links(bridge.document.project)==[{'left':a.id,'right':c.id}]
    assert bridge.command('row.delete',{'id':a.id})
    assert pending_text_links(bridge.document.project)==[{'left':c.id,'right':''}]
    assert not bridge.command('textAllocation.markDone',{'id':c.id})
    assert bridge.command('row.edit',{'id':c.id,'text':'人工填写'})
    assert c.id in pending_text_ids(bridge.document.project)
    assert bridge.command('textAllocation.markDone',{'id':c.id})
    assert not pending_text_links(bridge.document.project)


def test_marker_split_retargets_pending_settings(bridge):
    row=bridge._row('target');row.start=0;row.end=2.;a,b=cut(bridge,frame=5)
    bridge.document.project.settings['frame_markers']=[3]
    assert bridge.command('row.split',{'id':a.id,'frame':3,'offsetUtf16':2})
    pieces=[m for m in bridge.document.project.mappings if m.id!='overlap']
    assert pending_text_links(bridge.document.project)==[{'left':pieces[1].id,'right':b.id}]
    assert ''.join(mapping_text(bridge.document.project,m) for m in pieces)=='前半后半'


@pytest.mark.parametrize('change',['text','time','selection','index','undo','locked'])
def test_stale_inline_allocation_rejected(bridge,change):
    a,b=cut(bridge);assert bridge.command('textAllocation.begin',{'id':a.id})
    value=bridge.state['textAllocation']
    if change=='text':bridge.command('row.edit',{'id':a.id,'text':'新文字'})
    elif change=='time':bridge.command('row.setBoundary',{'id':b.id,'edge':'last','frame':6})
    elif change=='selection':bridge.command('timeline.select',{'id':b.id})
    elif change=='index':bridge.media_controller.index=None
    elif change=='undo':bridge.command('history.undo',{})
    else:bridge.command('row.lock',{'id':b.id,'value':True})
    before=bridge.document.snapshot()
    assert not bridge.command('textAllocation.apply',{**value,'offsetUtf16':2})
    assert bridge.document.snapshot()==before


@pytest.mark.parametrize('text_role',['translation','source_ocr','source_asr','source_import'])
def test_pending_warns_without_extra_confirmation_export_gate_and_persists(bridge,tmp_path,text_role):
    p=bridge.document.project
    if text_role!='translation':
        for row in p.mappings:
            row.text_override=mapping_text(p,row)
            row.text_role=text_role;row.translation_ids=[];row.text_segments=[]
        p.translations=[];bridge._changed()
    a,b=cut(bridge);p=bridge.document.project
    assert bridge.command('row.confirm',{'id':a.id,'value':True})
    assert a.id in pending_text_ids(p)
    assert any('待分配' in e for e in bridge.state['export']['compatibilityWarnings'])
    for draft in (True,False):
        problems=validate_project(p,draft=draft)
        assert f'{b.id}：字幕内容为空' in problems
        before_export=bridge.document.snapshot()
        for fmt in ('ass','srt','lrc'):
            output=tmp_path/f'blocked-{draft}.{fmt}'
            with pytest.raises(ValueError,match='字幕内容为空'):
                export_project(p,output,draft=draft)
            assert not output.exists()
        assert bridge.document.snapshot()==before_export
        assert not any('待分配文字的切分片段' in e for e in problems)
    path=tmp_path/'pending.lyria';write_document(bridge.document.snapshot(),path)
    loaded=load_document(path).project
    assert loaded.to_dict()==p.to_dict()
    assert pending_text_ids(loaded)=={a.id,b.id}
    assert all(row.text_role==text_role for row in loaded.mappings)
    for draft in (True,False):
        assert validate_project(loaded,draft=draft)==validate_project(p,draft=draft)
    malformed=copy.deepcopy(bridge.document.snapshot());malformed['project']['settings']['pending_text_splits'][0]['right']='missing'
    with pytest.raises(ValueError,match='待分字'):write_document(malformed,tmp_path/'invalid.lyria')


def test_manual_edit_requires_explicit_done(bridge):
    a,b=cut(bridge)
    assert bridge.command('row.edit',{'id':b.id,'text':'人工后段'})
    assert pending_text_ids(bridge.document.project)=={a.id,b.id}
    assert bridge.command('textAllocation.markDone',{'id':b.id})
    assert not pending_text_ids(bridge.document.project)


@pytest.mark.parametrize('text,character',[('甲e\u0301乙',2),('甲👩\u200d👩乙',2),('甲🇨🇳乙',2),('甲\r\n乙',2)])
def test_direct_editor_allocation_rejects_inside_grapheme(bridge,text,character):
    bridge._row('target').text_override=text;a,b=cut(bridge)
    before=bridge.document.snapshot();history=len(bridge.history.undo_stack)
    with pytest.raises(ValueError,match='完整字符之间切分'):bridge.editor.allocate_text(a.id,b.id,character)
    assert bridge.document.snapshot()==before and len(bridge.history.undo_stack)==history


def test_display_utf16_preview_and_commit_map_crlf_without_loss(bridge):
    bridge._row('target').text_override='甲\r\n乙丙';a,b=cut(bridge)
    assert bridge.command('textAllocation.begin',{'id':a.id})
    data=bridge.state['textAllocation']
    assert data['displayText']=='甲\n乙丙' and data['text']=='甲\r\n乙丙'
    assert bridge.allocationPreview(3)=={'valid':True,'left':'甲\r\n乙','right':'丙','error':''}
    assert bridge.command('textAllocation.apply',{**data,'offsetUtf16':3})
    assert [mapping_text(bridge.document.project,m) for m in (a,b)]==['甲\r\n乙','丙']


def test_mark_done_preserves_existing_confirmations_and_locks(bridge):
    a,b=cut(bridge)
    bridge.editor.set_text(b.id,'人工后段')
    bridge.editor.confirm(a.id);bridge.editor.confirm(b.id)
    before=[cue_state(bridge.document.project,m) for m in (a,b)]
    assert bridge.command('textAllocation.markDone',{'id':a.id})
    assert [cue_state(bridge.document.project,m) for m in (a,b)]==before
    assert not pending_text_ids(bridge.document.project)


def test_ai_structure_retargets_pending_and_save_stays_valid(tmp_path):
    from test_api_migration_alignment import fixture,split,response
    from lyricsync.api_alignment import make_plan,accept_plan
    from lyricsync.editing import Editor,History
    from lyricsync.project_document import ProjectDocument
    p,index=fixture();p.inputs={k:str(tmp_path/v) for k,v in p.inputs.items()};history=History();editor=Editor(p,history)
    a,b=editor.razor_split('m0',4,index)
    before=p.to_dict();proposal=split();proposal['mapping_ids']=[a.id]
    for part in proposal['parts']:
        for span in part['spans']:span['mapping_id']=a.id
    plan=make_plan(p,index,response(proposal),[a.id]);accept_plan(p,plan,index,history)
    assert pending_text_links(p)==[{'left':p.mappings[1].id,'right':b.id}]
    path=tmp_path/'after-ai.lyria';write_document(ProjectDocument(project=p).snapshot(),path)
    assert load_document(path).project.to_dict()==p.to_dict()
    restored,_=history.restore(p)
    assert restored.to_dict()==before


def test_pending_ids_survive_matching_and_ocr_only_rebuild(bridge):
    from lyricsync.matching import match_project
    from lyricsync.video_only import build_empty_translations
    a,b=cut(bridge);before=pending_text_links(bridge.document.project)
    match_project(bridge.document.project)
    assert pending_text_links(bridge.document.project)==before
    assert {a.id,b.id}<={m.id for m in bridge.document.project.mappings}
    build_empty_translations(bridge.document.project)
    assert pending_text_links(bridge.document.project)==before
    assert {a.id,b.id}<={m.id for m in bridge.document.project.mappings}


def test_inserted_middle_row_never_redirects_allocation_to_wrong_cue(bridge):
    a,b=cut(bridge)
    assert bridge.command('row.add',{'after':a.id})
    before=bridge.document.snapshot()
    assert not bridge.command('textAllocation.begin',{'id':a.id})
    assert bridge.document.snapshot()==before and pending_text_links(bridge.document.project)==[{'left':a.id,'right':b.id}]


def test_old_project_without_pending_settings_loads_unchanged(bridge,tmp_path):
    assert 'pending_text_splits' not in bridge.document.project.settings
    path=tmp_path/'legacy.lyria';write_document(bridge.document.snapshot(),path)
    assert load_document(path).project.to_dict()==bridge.document.project.to_dict()


@pytest.mark.parametrize('kind',['duplicate','reverse','dangling','orphan_duplicate','invalid_type'])
def test_corrupt_pending_graph_rejected_without_writing(bridge,tmp_path,kind):
    a,b=cut(bridge);snapshot=bridge.document.snapshot();links=snapshot['project']['settings']['pending_text_splits']
    if kind=='duplicate':links.append(dict(links[0]))
    elif kind=='reverse':links[0]={'left':b.id,'right':a.id}
    elif kind=='dangling':links[0]['right']='missing'
    elif kind=='orphan_duplicate':links.append({'left':b.id,'right':''})
    else:links[0]['right']=True
    path=tmp_path/'invalid-graph.lyria'
    with pytest.raises(ValueError,match='待分字'):write_document(snapshot,path)
    assert not path.exists()


def test_cross_chain_merge_normalizes_only_connected_pending_group(bridge,tmp_path):
    row=bridge._row('target');row.start=0;row.end=2.
    a,b=cut(bridge,frame=6)
    bridge.editor.set_time(a.id,end=.2)
    assert bridge.command('row.add',{'after':a.id})
    middle=bridge._row();bridge.editor.set_time(middle.id,start=.2,end=1.)
    bridge.editor.set_text(middle.id,'新增文字')
    assert bridge.command('timeline.razor',{'id':middle.id,'frame':4})
    rows=bridge.document.project.mappings;left=rows[rows.index(a)+1];right=rows[rows.index(a)+2]
    before=bridge.document.project.to_dict();history=len(bridge.history.undo_stack)
    literal=''.join(mapping_text(bridge.document.project,m) for m in rows)
    untouched={m.id:cue_state(bridge.document.project,m) for m in rows if m.id not in (a.id,left.id)}
    assert bridge.command('row.merge',{'ids':[a.id,left.id]})
    p=bridge.document.project;merged=bridge._row()
    assert pending_text_links(p)==[{'left':merged.id,'right':right.id},{'left':right.id,'right':b.id}]
    assert ''.join(mapping_text(p,m) for m in p.mappings)==literal
    assert {m.id:cue_state(p,m) for m in p.mappings if m.id!=merged.id}==untouched
    assert (merged.start,merged.end)==(a.start,left.end)
    assert len(bridge.history.undo_stack)==history+1
    path=tmp_path/'normalized.lyria';write_document(bridge.document.snapshot(),path)
    assert load_document(path).project.to_dict()==p.to_dict()
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{})
    assert pending_text_links(bridge.document.project)==[{'left':merged.id,'right':right.id},{'left':right.id,'right':b.id}]


def test_unrelated_pending_component_and_filled_orphan_survive_merge(bridge):
    from lyricsync.model import Mapping,Translation
    from lyricsync.editing import merge_mappings
    p=bridge.document.project
    p.translations.extend([Translation('u1','甲乙'),Translation('u2','丙丁'),Translation('u3','手填孤立段')])
    p.mappings.extend([Mapping('u1',['u1'],start=1.,end=1.4),Mapping('u2',['u2'],start=1.4,end=1.8),Mapping('u3',['u3'],start=1.8,end=2.)])
    p.settings['pending_text_splits']=[{'left':'u1','right':'u2'},{'left':'u3','right':''}]
    a,b=cut(bridge)[:2]
    # Completing one entirely merged split must leave the unrelated pair and
    # already filled orphan warning intact; normal editing is not completion.
    merge_mappings(p,[a.id,b.id])
    assert pending_text_links(p)==[{'left':'u1','right':'u2'},{'left':'u3','right':''}]
    assert mapping_text(p,next(m for m in p.mappings if m.id=='u3'))=='手填孤立段'


@pytest.mark.parametrize('merge_ids,expected',[(['a','x'],['merged','b','y']),(['b','y'],['a','x','merged'])])
def test_connected_merge_normalizes_incoming_and_outgoing_only(merge_ids,expected):
    from lyricsync.model import Project,Mapping,Translation
    from lyricsync.editing import merge_mappings
    identities=['a','x','b','y','u','v'];p=Project()
    p.translations=[Translation(i,i.upper()) for i in identities]
    p.mappings=[Mapping(i,[i],start=float(n),end=float(n+1)) for n,i in enumerate(identities)]
    p.settings['pending_text_splits']=[{'left':'a','right':'b'},{'left':'x','right':'y'},{'left':'u','right':'v'}]
    text=''.join(mapping_text(p,m) for m in p.mappings)
    unchanged={m.id:cue_state(p,m) for m in p.mappings if m.id not in merge_ids}
    merged=merge_mappings(p,merge_ids)
    chain=[merged.id if i=='merged' else i for i in expected]
    assert pending_text_links(p)==[{'left':a,'right':b} for a,b in zip(chain,chain[1:])]+[{'left':'u','right':'v'}]
    assert ''.join(mapping_text(p,m) for m in p.mappings)==text
    assert {m.id:cue_state(p,m) for m in p.mappings if m.id!=merged.id}==unchanged
