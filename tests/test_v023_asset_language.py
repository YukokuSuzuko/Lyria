import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from pathlib import Path
import pytest
from test_qt_entry_initialization_dev5 import bridge
from lyricsync.model import Mapping, Translation


def test_language_options_only_deployed_models(tmp_path):
    from desktop_qt.ocr_languages import language_options
    root=tmp_path/'models';root.mkdir()
    assert language_options('rapidocr','ppocrv4',tmp_path)==[]
    for name in ('ch_PP-OCRv4_det_infer.onnx','ch_ppocr_mobile_v2.0_cls_infer.onnx','japan_PP-OCRv4_rec_infer.onnx'):
        (root/name).touch()
    assert language_options('rapidocr','ppocrv4',tmp_path)==[('日语','jpn')]
    (root/'ch_PP-OCRv4_rec_infer.onnx').touch()
    values=dict((value,label) for label,value in language_options('rapidocr','ppocrv4',tmp_path))
    assert set(values)=={'jpn','eng','chi_sim','jpn+eng'}
    assert all(any('一'<=c<='鿿' for c in label) for label in values.values())


def test_clear_pristine_source_seed_and_undo(bridge):
    b=bridge;p=b.document.project
    p.inputs['ktv']='never-opened.txt'
    from lyricsync.model import Lyric
    p.lyrics=[Lyric('l','原词',None,None)]
    p.mappings=[Mapping('s',[],lyric_ids=['l'],text_override='原词',text_role='source_import',method='source_import')]
    b._entry_accept_import();before=deepcopy(p.to_dict())
    b._clear_input({'role':'source'})
    assert not b.document.project.mappings and not b.document.project.lyrics
    b.document.validate_project(b.document.project)
    b._history(False)
    assert b.document.project.to_dict()==before


def test_clear_manual_cancel_then_detach_preserves_text(bridge,monkeypatch):
    from desktop_qt import dialogs
    b=bridge;p=b.document.project;p.inputs['translation']='never-opened.txt'
    p.translations=[Translation('t','保留')]
    p.mappings=[Mapping('m',['t'],start=1,end=2,human_edited=True,confirmed=True)]
    before=deepcopy(p.to_dict())
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'cancel')
    b._clear_input({'role':'translation'})
    assert p.to_dict()==before
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
    b._clear_input({'role':'translation'})
    assert b._entry_mode=='edit' and p.mappings[0].text_override=='保留'
    assert not p.translations and not p.mappings[0].translation_ids
    b.document.validate_project(p)
    assert p.mappings[0].confirmed and p.mappings[0].start==1
    b._history(False);assert b.document.project.to_dict()==before


def test_asset_epoch_rejects_late_job_without_suppressing_save(bridge):
    import threading
    from test_qt_entry_initialization_dev5 import wait
    b=bridge;gate=threading.Event();calls=[]
    b._start_job('old_asset',lambda c,p:gate.wait(3),lambda r,e:calls.append('old'),foreground=False)
    b._invalidate_assets()
    gate.set();wait(lambda:not b.jobs.active and not b._handlers)
    assert calls==[]


def test_mode_switch_preserves_manual_and_isolates_candidates(bridge,monkeypatch):
    from desktop_qt import dialogs
    from desktop_qt.timeline_views import current_candidate
    b=bridge;p=b.document.project
    p.mappings=[Mapping('m',[],text_override='手改',start=1,end=2,human_edited=True)]
    from lyricsync.candidate_tracks import capture_source, candidate_from_project, install_candidate
    from lyricsync.model import Project, Visual
    captured = capture_source(b.document, 'ocr')
    generated = Project.from_dict(deepcopy(captured['project']))
    generated.visuals = [Visual('observed', '手改', 1., 2., [4,8,80,24], .94, 4)]
    generated.mappings[0].visual_ids = ['observed']
    record = candidate_from_project(captured, generated)
    install_candidate(b.document, record)
    assert current_candidate(b.document, record)
    before=deepcopy(p.to_dict())
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
    assert b._start_editor({'mode':'audio'})
    assert b._entry_mode=='edit' and p.mappings[0].text_override=='手改'
    assert not current_candidate(b.document,b.document.source_candidates[0])
    b._history(False)
    assert b.document.project.to_dict()==before
    assert current_candidate(b.document,b.document.source_candidates[0])


def test_real_language_combo_is_not_free_text(bridge,monkeypatch):
    from desktop_qt import dialogs
    from PySide6.QtWidgets import QDialog,QComboBox
    found=[]
    def inspect(dialog):
        combo=dialog.findChild(QComboBox,'ocrLanguage')
        assert combo is not None and not combo.isEditable()
        assert combo.count() and combo.findData('jpn+eng')>=0
        found.append(combo.currentData())
        return QDialog.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    assert not dialogs.ocr_settings(bridge,['CPUExecutionProvider'])
    assert found


def test_clear_new_project_uses_save_guard_without_deleting_rows(bridge,monkeypatch):
    from desktop_qt import dialogs
    b=bridge;p=b.document.project;p.inputs['translation']='old.txt'
    p.mappings=[Mapping('m',[],text_override='编辑',human_edited=True)]
    before=deepcopy(p.to_dict());requests=[]
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'new')
    monkeypatch.setattr(b,'_replace_guard',requests.append)
    b._clear_input({'role':'translation'})
    assert len(requests)==1 and p.to_dict()==before


def test_asset_clear_dialog_defaults_cancel(monkeypatch):
    from desktop_qt.dialogs import choose_asset_clear
    from PySide6.QtWidgets import QMessageBox
    def inspect(box):
        assert box.objectName()=='assetClearConfirmation'
        assert box.defaultButton().objectName()=='cancelAssetClear'
        assert box.escapeButton().objectName()=='cancelAssetClear'
    monkeypatch.setattr(QMessageBox,'exec',inspect)
    assert choose_asset_clear()=='cancel'


def test_model_switch_refreshes_language_choices(bridge,monkeypatch):
    from desktop_qt import dialogs,ocr_languages
    from PySide6.QtWidgets import QDialog,QComboBox
    monkeypatch.setattr(ocr_languages,'language_options',lambda engine,model,root:[('日语','jpn')] if model=='ppocrv4' else [('日语与英语','jpn+eng')])
    def inspect(dialog):
        model=dialog.findChild(QComboBox,'ocrRecognitionModel');language=dialog.findChild(QComboBox,'ocrLanguage')
        assert language.currentData()=='jpn+eng'
        model.setCurrentIndex(model.findData('ppocrv4'))
        assert language.count()==1 and language.currentData()=='jpn'
        return QDialog.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    assert not dialogs.ocr_settings(bridge,['CPUExecutionProvider'])


def test_save_completion_survives_asset_invalidation(bridge):
    import threading
    from test_qt_entry_initialization_dev5 import wait
    b=bridge;gate=threading.Event();calls=[]
    b._start_job('save',lambda c,p:gate.wait(3),lambda r,e:calls.append('save'),foreground=False)
    b._invalidate_assets();gate.set();wait(lambda:not b.jobs.active and not b._handlers)
    assert calls==['save']


def test_pristine_mode_clear_isolates_old_candidates(bridge,monkeypatch):
    from desktop_qt import dialogs
    from desktop_qt.timeline_views import current_candidate
    b=bridge
    assert b.command('project.newEntry',{'mode':'ocr'})
    b.document.project.translations=[Translation('t','导入种子')]
    b.document.project.mappings=[Mapping('seed',['t'],method='import')]
    b._entry_accept_import()
    b.document.source_candidates=[{'id':'old','source':'ocr'}]
    monkeypatch.setattr(dialogs,'confirm_mode_switch',lambda:True)
    assert b._start_editor({'mode':'audio'})
    assert b._asset_epoch>0 and b._entry_mode=='audio'
    assert not current_candidate(b.document,b.document.source_candidates[0])


def test_stale_candidate_completion_clears_busy_state(bridge):
    import threading
    from test_qt_entry_initialization_dev5 import wait
    b=bridge;gate=threading.Event();calls=[]
    b._start_candidate_job('controlled','ocr',lambda c,p:gate.wait(3),lambda r,e:calls.append('old'))
    b._invalidate_assets();gate.set();wait(lambda:not b.jobs.active and not b._handlers)
    assert not calls and b._candidate_job is None and not b._candidate_task['running']


@pytest.mark.parametrize('role',['source','media'])
def test_detach_keeps_reference_only_manual_text(bridge,monkeypatch,role):
    from desktop_qt import dialogs
    from lyricsync.model import Lyric,Visual
    from lyricsync.editing import mapping_text
    b=bridge;p=b.document.project
    p.inputs['ktv' if role=='source' else 'video']='absent-fixture'
    p.lyrics=[Lyric('l','保留原词',1,2)]
    p.visuals=[Visual('v','保留OCR',1,2,[0,0,50,20],.9,3)]
    p.mappings=[Mapping('m',[],lyric_ids=['l'] if role=='source' else [],visual_ids=['v'] if role=='media' else [],start=1,end=2,human_edited=True,text_role='source_import' if role=='source' else 'source_ocr')]
    # Legacy malformed source-role rows violate today's editable-body invariant.
    with pytest.raises(ValueError):b.document.validate_project(p)
    monkeypatch.setattr(dialogs,'choose_asset_clear',lambda:'detach')
    b._clear_input({'role':role})
    assert mapping_text(p,p.mappings[0])==('保留原词' if role=='source' else '保留OCR')
    b.document.validate_project(p)
    assert p.settings['timeline_archives'][-1]['project']['mappings'][0]['text_override'] is None


def test_historical_candidates_block_manual_preview_and_apply(bridge):
    b=bridge
    b.document.source_candidates=[{'id':'retired','source':'ocr'}]
    b._isolate_asset_candidates();result=[]
    b.candidate_preview('retired',[],{},False,lambda plan,error:result.append(error))
    assert len(result)==1 and isinstance(result[0],ValueError)
    with pytest.raises(ValueError):b.candidate_apply({'candidate_id':'retired'})


def test_orphan_job_token_cleaned(bridge):
    b=bridge;b._job_asset_epochs={'old':0}
    b._job_finished('old',None,None)
    assert not b._job_asset_epochs


def test_mode_choice_reentry_and_new_busy_do_not_mutate(bridge,monkeypatch):
    from desktop_qt import dialogs
    b=bridge;p=b.document.project;p.mappings=[Mapping('m',[],text_override='keep',human_edited=True)]
    before=deepcopy(p.to_dict());calls=[]
    def choose():
        calls.append(1)
        assert len(calls)==1,'modal mode choice reentered'
        assert b._start_editor({'mode':'audio'}) is False
        b._task['running']=True
        return 'detach'
    monkeypatch.setattr(dialogs,'choose_asset_clear',choose)
    with pytest.raises(ValueError):b._start_editor({'mode':'audio'})
    b._task['running']=False
    assert p.to_dict()==before


def test_independent_domain_job_finishes_after_text_asset_clear(bridge):
    import threading
    from test_qt_entry_initialization_dev5 import wait
    b=bridge;gate=threading.Event();calls=[]
    b.document.project.inputs['ktv']='fixture.txt'
    b._start_job('independent',lambda c,p:gate.wait(3),lambda r,e:calls.append('ready'),foreground=False,asset_dependent=False)
    b._clear_input({'role':'source'});gate.set();wait(lambda:not b.jobs.active and not b._handlers)
    assert calls==['ready']


def test_real_layout_worker_recovers_after_text_clear(bridge,monkeypatch):
    import threading
    import lyricsync.export as export
    from test_qt_entry_initialization_dev5 import wait
    b=bridge;gate=threading.Event();started=threading.Event()
    original=export.project_layout_rows
    def delayed(project):
        started.set();assert gate.wait(3)
        return original(project)
    monkeypatch.setattr(export,'project_layout_rows',delayed)
    b.document.project.inputs['ktv']='fixture.txt'
    b._begin_layout();wait(started.is_set)
    b._clear_input({'role':'source'});gate.set()
    wait(lambda:not b._layout_running and not b.jobs.active and not b._handlers)
    assert not b._layout_running
