"""Synthetic/offline workflow contracts; not OCR quality or remote AI acceptance."""
from copy import deepcopy
from pathlib import Path
import pytest
from test_qt_entry_initialization_dev5 import bridge, ready, settled
from lyricsync.model import Mapping, Project, Translation, Lyric, Visual, stable_id
from lyricsync.editing import mapping_text
from lyricsync.candidate_tracks import capture_source, candidate_from_project, prepare_apply, install_candidate
from lyricsync.project_document import ProjectDocument, write_document, load_document
from desktop_qt.entry_initialization import bind_imported_ocr_rows


def test_binding_uses_immutable_ids_not_text_order():
    original=Project(mappings=[Mapping('one',['t1']),Mapping('two',['t2'])])
    generated=Project(mappings=[Mapping('new2',['t2']),Mapping('new1',['t1'])])
    bind_imported_ocr_rows(original,generated)
    assert [r.id for r in generated.mappings]==['two','one']
    for rows in ([Mapping('a',['t1']),Mapping('b',['t1'])], [Mapping('c',['t1','t2'])], [Mapping('d',[],text_override='same')]):
        generated=Project(mappings=rows);before=deepcopy(generated.to_dict())
        bind_imported_ocr_rows(original,generated)
        assert generated.to_dict()==before


def setup_ocr(b,tmp_path):
    ready(b)
    video=tmp_path/'input.mp4';video.write_bytes(b'not decoded: controlled pipeline')
    b.document.project.inputs['video']=str(video)
    b.document.project.settings['roi']=[0,0,100,30]
    source=tmp_path/'translation.txt';source.write_text('first translation\nsecond translation',encoding='utf-8')
    assert b.command('input.import',{'role':'translation','path':str(source)})
    settled(b)


def fake_run(project,*args,**kwargs):
    project.visuals=[Visual(f'v{n}',f'recognized {n}',.2+n,.8+n,[0,0,100,20],.9,2)
                     for n in range(len(project.translations))]
    project.mappings=[Mapping('generated-'+str(n),[t.id],visual_ids=[project.visuals[n].id],start=.2+n,end=.8+n)
                      for n,t in enumerate(project.translations)]
    project.stage='review'
    return project


@pytest.mark.parametrize('auto',[False,True])
def test_ocr_local_only_and_translation_identity_preserved(bridge,tmp_path,monkeypatch,auto):
    setup_ocr(bridge,tmp_path)
    original=deepcopy(bridge.document.project.to_dict());ids=[r.id for r in bridge.document.main_timeline]
    calls=[]
    monkeypatch.setattr('lyricsync.pipeline.run',fake_run)
    monkeypatch.setattr(bridge,'_api_check',lambda:None)
    monkeypatch.setattr(bridge,'_api_alignment',lambda payload:calls.append(payload))
    assert bridge.command('ocr.start',{'autoAi':auto})
    assert bridge.stage=='import'
    settled(bridge)
    assert bridge.stage=='review'
    assert [r.id for r in bridge.document.main_timeline]==ids
    assert [mapping_text(bridge.document.project,r) for r in bridge.document.main_timeline]==['first translation','second translation']
    assert bridge.document.project.to_dict()['translations']==original['translations']
    assert calls==[]  # F47 supersedes optional automatic AI.
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==original


@pytest.mark.parametrize('failure',['cancel','error','empty','missing_time'])
def test_ocr_failure_or_unusable_result_does_not_send_ai_or_navigate(bridge,tmp_path,monkeypatch,failure):
    setup_ocr(bridge,tmp_path);calls=[];original=deepcopy(bridge.document.project.to_dict())
    def run(project,*args,**kwargs):
        if failure=='cancel':raise InterruptedError('controlled')
        if failure=='error':raise ValueError('controlled')
        fake_run(project)
        if failure=='empty':project.mappings=[]
        if failure=='missing_time':project.mappings[0].start=project.mappings[0].end=None
    monkeypatch.setattr('lyricsync.pipeline.run',run)
    monkeypatch.setattr(bridge,'_api_check',lambda:None)
    monkeypatch.setattr(bridge,'_api_alignment',lambda payload:calls.append(payload))
    assert bridge.command('ocr.start',{'autoAi':True});settled(bridge)
    assert not calls
    if failure=='missing_time':
        assert bridge.stage=='review'
        assert len(bridge.document.main_timeline)==2
        assert bridge.document.main_timeline[0].start is None
        assert bridge.document.main_timeline[1].start==1.2
        assert bridge.command('history.undo',{})
    else:
        assert bridge.stage=='import'
    assert bridge.document.project.to_dict()==original


def test_source_origin_is_not_independent_reference(bridge):
    ready(bridge)
    p=bridge.document.project
    p.lyrics=[Lyric(stable_id('ocr-source','v'),'recognized',0.,1.)]
    p.visuals=[Visual('v','recognized',0.,1.,[0,0,10,10],.9,1)]
    p.mappings=[Mapping('m',[],lyric_ids=[p.lyrics[0].id],visual_ids=['v'],start=0.,end=1.)]
    bridge._changed()
    assert bridge.state['rows'][0]['sourceFromOcr'] is True
    p.lyrics=[Lyric('imported','reference',0.,1.)];p.mappings[0].lyric_ids=['imported'];bridge._changed()
    assert bridge.state['rows'][0]['sourceFromOcr'] is False


def test_asr_partial_time_keeps_full_editable_source_draft_and_undo(bridge,tmp_path):
    ready(bridge,'audio');before=deepcopy(bridge.document.project.to_dict())
    detached=deepcopy(bridge.document);index=deepcopy(bridge.media_controller.index)
    generated=Project(mappings=[Mapping('asr1',[],text_override='known',start=.2,end=.8,method='asr_confirmed_source'),
                                Mapping('asr2',[],text_override='unknown',method='asr_confirmed_source')])
    def work(c,p):return candidate_from_project(capture_source(detached,'vocal',index),generated)
    bridge._start_candidate_job('controlled ASR','vocal',work,lambda r,e:bridge._candidate_install(r) if not e else None)
    settled(bridge)
    assert bridge._candidate_source=='main'
    assert len(bridge.document.source_candidates)==1
    record=bridge.document.source_candidates[0]
    assert record['rows'][1]['start'] is None and record['rows'][1]['end'] is None
    rows=bridge.document.main_timeline
    assert [mapping_text(bridge.document.project,r) for r in rows]==['known','unknown']
    assert [r.text_role for r in rows]==['source_asr','source_asr']
    assert rows[1].start is None and rows[1].end is None
    path=tmp_path/'asr.lyria';write_document(bridge.document.snapshot(explicit_save=True),path)
    assert load_document(path).project.to_dict()==bridge.document.project.to_dict()
    assert bridge.command('history.undo',{}) and bridge.document.project.to_dict()==before
    assert bridge.command('history.redo',{}) and len(bridge.document.main_timeline)==2


def test_asr_untimed_opt_in_cannot_apply_to_other_roles_or_existing_axis():
    for role in ('translation','source_ocr'):
        doc=ProjectDocument();generated=Project(mappings=[Mapping('r',[],text_override='text',text_role=role)])
        record=candidate_from_project(capture_source(doc,'vocal'),generated);install_candidate(doc,record)
        with pytest.raises(ValueError):prepare_apply(doc,record['id'],['r'],add_unmapped=True,initial_asr_draft=True)


def test_import_ui_contract_no_premature_navigation():
    root=Path(__file__).resolve().parents[1]
    text=(root/'desktop_qt/qml/ImportPanel.qml').read_text(encoding='utf-8')
    assert 'ocrAutoAiCheckBox' in text and 'chooseOcrEngineButton' not in text
    assert 'startOcrButton' in text
    assert 'changeEntryModeButton' in text and 'clearImportMaterialButton' in text
    assert 'cmd("stage.set", {stage: "review"})' not in text
    assert 'objectName: "importPairingStructureButton"' not in text
    assert '选择开始方式' not in text


@pytest.fixture(autouse=True)
def accepted_settings_for_downstream_ocr_contracts(monkeypatch):
    # These older suites exercise work AFTER a user's settings acceptance.
    # Actual modal acceptance/cancel/reentrancy is covered in dev8 entry tests.
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings', lambda *args, **kwargs: True)
