"""Enhanced OCR setting labels/persistence; backend quality tested separately."""
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QDialog,QComboBox
from test_dev5_feedback_navigation import app,bridge
from desktop_qt.dialogs import ocr_settings
from lyricsync.project_document import write_document,load_document
from test_dev5_feedback_ocr_flow import setup_ocr,fake_run
from test_qt_entry_initialization_dev5 import settled


def controls(dialog):
    return tuple(dialog.findChild(QComboBox,name) for name in ('ocrEngine','ocrRecognitionModel','ocrPreprocessMode'))


@pytest.mark.parametrize('engine',['rapidocr','tesseract'])
def test_defaults_and_engine_toggle_never_reset_existing_choice(bridge,monkeypatch,engine):
    p=bridge.document.project;p.settings['engine']=engine
    p.settings.pop('ocr_model',None);p.settings.pop('ocr_preprocess',None)
    before=bridge.document.snapshot()
    def inspect(dialog):
        selected,model,preprocess=controls(dialog)
        assert selected.currentData()==engine
        assert model.currentData()=='ppocrv6' and model.currentText()=='增强多语模型'
        assert preprocess.currentData()=='subtitle' and preprocess.currentText()=='复杂背景字幕'
        assert model.isEnabled() is (engine=='rapidocr')
        selected.setCurrentIndex(selected.findData('rapidocr'))
        model.setCurrentIndex(model.findData('ppocrv4'));preprocess.setCurrentIndex(preprocess.findData('raw'))
        selected.setCurrentIndex(selected.findData('tesseract'))
        assert not model.isEnabled() and not preprocess.isEnabled()
        selected.setCurrentIndex(selected.findData('rapidocr'))
        assert model.currentData()=='ppocrv4' and preprocess.currentData()=='raw'
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    ocr_settings(bridge,['CPUExecutionProvider'])
    assert bridge.document.snapshot()==before


@pytest.mark.parametrize('engine',['rapidocr','tesseract'])
@pytest.mark.parametrize('model,preprocess',[('ppocrv6','subtitle'),('ppocrv4','raw')])
def test_saved_fields_reopen_project_and_undo(bridge,tmp_path,monkeypatch,engine,model,preprocess):
    before=bridge.document.project.to_dict()
    def save(dialog):
        e,m,p=controls(dialog)
        e.setCurrentIndex(e.findData(engine));m.setCurrentIndex(m.findData(model));p.setCurrentIndex(p.findData(preprocess))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',save)
    ocr_settings(bridge,['CPUExecutionProvider'])
    settings=bridge.document.project.settings
    assert (settings['engine'],settings['ocr_model'],settings['ocr_preprocess'])==(engine,model,preprocess)
    target=tmp_path/'settings.lyria';write_document(bridge.document.snapshot(explicit_save=True),target)
    assert load_document(target).project.settings==settings
    def reopen(dialog):
        e,m,p=controls(dialog)
        assert (e.currentData(),m.currentData(),p.currentData())==(engine,model,preprocess)
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',reopen);ocr_settings(bridge,['CPUExecutionProvider'])
    assert bridge.command('history.undo',{})
    assert bridge.document.project.to_dict()==before


@pytest.mark.parametrize('model,preprocess',[('ppocrv6','subtitle'),('ppocrv4','raw')])
def test_selected_fields_reach_detached_ocr_pipeline(bridge,tmp_path,monkeypatch,model,preprocess):
    setup_ocr(bridge,tmp_path)
    bridge.document.project.settings.update(ocr_model=model,ocr_preprocess=preprocess)
    calls=[]
    def run(project,*args,**kwargs):
        assert project is not bridge.document.project
        calls.append((project.settings['ocr_model'],project.settings['ocr_preprocess']))
        return fake_run(project,*args,**kwargs)
    monkeypatch.setattr('lyricsync.pipeline.run',run)
    assert bridge.command('ocr.start',{})
    settled(bridge)
    assert calls==[(model,preprocess)]
    assert bridge.document.project.settings['ocr_model']==model
    assert bridge.document.project.settings['ocr_preprocess']==preprocess


@pytest.fixture(autouse=True)
def accepted_settings_for_downstream_ocr_contracts(monkeypatch):
    # These older suites exercise work AFTER a user's settings acceptance.
    # Actual modal acceptance/cancel/reentrancy is covered in dev8 entry tests.
    monkeypatch.setattr('desktop_qt.dialogs.ocr_settings', lambda *args, **kwargs: True)
