"""Explicit source-text roles, not a translation or a model-inference test."""
from copy import deepcopy
import pytest

from test_qt_entry_initialization_dev5 import bridge, ready, settled
from lyricsync.model import Mapping, Project, Translation, Lyric, Visual, stable_id
from lyricsync.editing import History, Editor, mapping_text, split_mapping, merge_mappings
from lyricsync.candidate_tracks import (capture_source, candidate_from_project, install_candidate,
    candidate_project, prepare_apply, apply_plan, read_candidates)
from lyricsync.project_document import ProjectDocument, load_document, write_document
from lyricsync.video_only import build_empty_translations
from lyricsync.export import export_project, validate_project


def ocr_record(document, index=None, texts=('same chorus', 'same chorus')):
    generated=Project()
    for n,text in enumerate(texts):
        identity='visual-'+str(n)
        generated.visuals.append(Visual(identity,text,.2+n,.8+n,[0,0,100,20],.9,2))
        generated.lyrics.append(Lyric(stable_id('ocr-source',identity),text,.2+n,.8+n))
    build_empty_translations(generated)
    return candidate_from_project(capture_source(document,'ocr',index),generated)


def start_initial(b):
    detached=deepcopy(b.document);index=deepcopy(b.media_controller.index)
    b._start_candidate_job('controlled OCR evidence','ocr',lambda c,p:ocr_record(detached,index),
                           lambda record,error:b._candidate_install(record) if not error else None)
    settled(b)


def test_ocr_initial_text_is_editable_source_evidence_is_immutable(bridge,tmp_path):
    ready(bridge);before=deepcopy(bridge.document.project.to_dict());depth=len(bridge.history.undo_stack)
    start_initial(bridge)
    p=bridge.document.project;rows=p.main_timeline
    assert [mapping_text(p,m) for m in rows]==['same chorus','same chorus']
    assert all(m.text_role=='source_ocr' and not m.confirmed for m in rows)
    assert len({m.id for m in rows})==2
    assert all(t.text=='' for t in p.translations)
    assert [v.text for v in p.visuals]==['same chorus','same chorus']
    initialized=deepcopy(p.to_dict());evidence={k:initialized[k] for k in ('translations','lyrics','visuals')}
    assert len(bridge.history.undo_stack)==depth+1
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{}) and bridge.document.project.to_dict()==initialized
    identity=bridge.document.main_timeline[0].id
    assert bridge.command('row.edit',{'id':identity,'text':'edited original, not Chinese'})
    edited=deepcopy(bridge.document.project.to_dict())
    assert edited['mappings'][0]['text_role']=='source_ocr'
    assert {k:edited[k] for k in evidence}==evidence
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==initialized
    assert bridge.command('history.redo',{}) and bridge.document.project.to_dict()==edited
    assert not bridge.command('project.rematch',{})
    assert bridge.document.project.to_dict()==edited
    path=tmp_path/'source.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
    loaded=load_document(path)
    assert loaded.project.to_dict()==edited
    assert loaded.source_candidates==bridge.document.source_candidates
    for fmt in ('ass','srt','lrc'):
        output=tmp_path/('source.'+fmt)
        export_project(loaded.project,output,draft=True,frame_index=bridge.media_controller.index)
        text=output.read_text(encoding='utf-8-sig')
        assert 'edited original, not Chinese' in text and 'same chorus' in text
    assert validate_project(loaded.project,draft=False)  # Still unreviewed.


def test_ocr_text_is_opt_in_only_and_needs_exact_nonempty_visual_evidence():
    doc=ProjectDocument();record=ocr_record(doc);install_candidate(doc,record)
    ids=[r['id'] for r in record['rows']]
    normal=prepare_apply(doc,record['id'],ids,add_unmapped=True)
    assert all(e['after']['text']=='' and e['after']['text_role']=='translation' for e in normal['entries'])
    initial=prepare_apply(doc,record['id'],ids,add_unmapped=True,initial_ocr_text=True)
    assert all(e['after']['text']=='same chorus' and e['after']['text_role']=='source_ocr' for e in initial['entries'])
    doc2=ProjectDocument();empty=ocr_record(doc2,texts=('',));install_candidate(doc2,empty)
    plan=prepare_apply(doc2,empty['id'],[empty['rows'][0]['id']],add_unmapped=True,initial_ocr_text=True)
    with pytest.raises(ValueError):apply_plan(doc2,History(),plan)
    assert not doc2.main_timeline
    doc.project.translations=[Translation('real','真正译文')]
    with pytest.raises(ValueError):prepare_apply(doc,record['id'],ids,add_unmapped=True,initial_ocr_text=True)


@pytest.mark.parametrize('role',['source_ocr','source_asr','source_import'])
def test_source_edit_split_merge_insert_preserve_role_and_evidence(role,tmp_path):
    p=Project(mappings=[Mapping('source',[],text_override='HELLO WORLD',text_role=role,start=0,end=2)])
    doc=ProjectDocument(project=p);history=History();editor=Editor(p,history)
    a,b=split_mapping(p,'source',5,1)
    assert [m.text_role for m in p.mappings]==[role,role]
    assert ''.join(mapping_text(p,m) for m in p.mappings)=='HELLO WORLD'
    combined=merge_mappings(p,[a.id,b.id])
    assert combined.text_role==role and combined.text_override=='HELLO WORLD'
    blank=editor.insert_blank(after=combined.id)
    assert blank.text_role==role and not p.translations and not blank.translation_ids
    p.mappings.remove(blank)
    for fmt in ('ass','srt','lrc'):
        output=tmp_path/('no-translation.'+fmt);export_project(p,output,draft=True)
        assert 'HELLO WORLD' in output.read_text(encoding='utf-8-sig')
    p.translations.append(Translation('t','实际译文'))
    translated=Mapping('translation',['t'],start=2,end=3);p.mappings.append(translated)
    old=deepcopy(p.to_dict())
    with pytest.raises(ValueError,match='演唱原文与译文行不能直接合并'):merge_mappings(p,[combined.id,translated.id])
    assert p.to_dict()==old
    from lyricsync.matching import match_project
    match_project(p)
    assert next(m for m in p.mappings if m.id==combined.id).text_role==role


def test_asr_confirmed_source_contract_and_time_only_protect_translation():
    p=Project(translations=[Translation('t','人工译文')],mappings=[Mapping('main',['t'],start=0,end=1)])
    doc=ProjectDocument(project=p)
    generated=Project(mappings=[Mapping('asr-occurrence',[],text_override='confirmed original',
                                       start=.2,end=.8,method='asr_confirmed_source')])
    record=candidate_from_project(capture_source(doc,'vocal'),generated)
    assert record['rows'][0]['text_role']=='source_asr'
    assert not record['evidence']['translations']
    assert candidate_project(record).mappings[0].text_role=='source_asr'
    install_candidate(doc,record)
    bindings={'asr-occurrence':'main'}
    blocked=prepare_apply(doc,record['id'],['asr-occurrence'],bindings=bindings,apply_text=True)
    with pytest.raises(ValueError,match='原文与译文类型不同'):apply_plan(doc,History(),blocked)
    plan=prepare_apply(doc,record['id'],['asr-occurrence'],bindings=bindings)
    apply_plan(doc,History(),plan)
    row=doc.main_timeline[0]
    assert row.text_role=='translation' and mapping_text(doc.project,row)=='人工译文'
    assert doc.project.translations[0].text=='人工译文'
    assert row.start==.2
    record=candidate_from_project(capture_source(doc,'vocal'),generated)
    install_candidate(doc,record)
    added=prepare_apply(doc,record['id'],['asr-occurrence'],add_unmapped=True)
    apply_plan(doc,History(),added)
    assert doc.main_timeline[-1].text_role=='source_asr'
    assert mapping_text(doc.project,doc.main_timeline[-1])=='confirmed original'


def test_old_project_and_candidates_default_to_translation_and_invalid_roles_rejected(tmp_path):
    legacy=Project(translations=[Translation('t','保留译文')],mappings=[Mapping('m',['t'])]).to_dict()
    legacy.pop('alignment_graph')  # actual pre-graph legacy file
    legacy['mappings'][0].pop('text_role')
    restored=Project.from_dict(legacy).validate_timeline()
    assert restored.mappings[0].text_role=='translation'
    doc=ProjectDocument(project=restored);record=candidate_from_project(capture_source(doc,'ocr'),restored)
    assert 'text_role' not in record['rows'][0]
    assert read_candidates([record])==([record],'')
    record['rows'][0]['text_role']='untrusted_role'
    assert read_candidates([record])[1]
    for role in ('',None,[],{},'Chinese'):
        restored.mappings[0].text_role=role
        with pytest.raises(ValueError):doc.validate_project(restored)


@pytest.mark.parametrize('source,role',[('ocr','source_asr'),('vocal','source_ocr')])
def test_text_origin_is_independent_of_timing_source_and_role_change_invalidates(source,role):
    p=Project(mappings=[Mapping('original',[],text_override='source text',text_role=role,start=0,end=1)])
    doc=ProjectDocument(project=p)
    generated=Project.from_dict(p.to_dict());generated.mappings[0].start=.2
    record=candidate_from_project(capture_source(doc,source),generated)
    install_candidate(doc,record)
    plan=prepare_apply(doc,record['id'],['original'])
    assert not plan['entries'][0]['blocked']
    assert plan['entries'][0]['after']['text_role']==role
    p.mappings[0].text_role='translation'
    with pytest.raises(ValueError,match='主轴目标句已被修改'):apply_plan(doc,History(),plan)


def test_empty_confirmed_asr_contract_cannot_create_blank_source_main():
    doc=ProjectDocument();generated=Project(mappings=[Mapping('empty',[],text_override='',start=0,end=1,
                                                              method='asr_confirmed_source')])
    record=candidate_from_project(capture_source(doc,'vocal'),generated);install_candidate(doc,record)
    plan=prepare_apply(doc,record['id'],['empty'],add_unmapped=True)
    with pytest.raises(ValueError,match='识别结果文字为空'):apply_plan(doc,History(),plan)
    assert not doc.main_timeline
