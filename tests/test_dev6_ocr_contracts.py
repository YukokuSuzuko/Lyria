"""Offline data and real Qt widget contracts, not Japanese MV quality acceptance."""
from pathlib import Path
from unittest.mock import patch
import pytest
from lyricsync.model import Project, Visual
from lyricsync.vision import Detection, Tracker, OCR_MIN_SAMPLE_FPS, OCR_MAX_SAMPLE_FPS


def test_new_default_and_explicit_engine_preserved():
    assert Project().settings['engine'] == 'rapidocr'
    p=Project.from_dict({**Project().to_dict(), 'settings': {'engine':'tesseract'}})
    assert p.settings['engine'] == 'tesseract'


def test_shape_cannot_extend_forever_without_ocr():
    import numpy as np
    frame=np.zeros((40,80,3),dtype=np.uint8)
    tracker=Tracker(1/3)
    with patch('lyricsync.vision.visibility_score',return_value=1):
        tracker.feed(0,frame,[Detection('same',[0,0,40,20],.99)])
        for i in range(1,31):tracker.feed(i/3,frame,[])
    tracks=tracker.finish()
    assert len(tracks)==1 and tracks[0].last <= 2/3
    assert tracks[0].next_absent is not None


@pytest.mark.parametrize('translated',[False,True])
def test_pipeline_no_fake_original_and_no_legacy_auto_api(tmp_path,translated):
    from lyricsync.pipeline import run
    from lyricsync.storage import load_project
    video=tmp_path/'input.mp4';video.write_bytes(b'controlled fixture')
    p=Project();p.inputs['video']=str(video);p.settings['api_enabled']=True
    if translated:
        translation=tmp_path/'translation.txt';translation.write_text('translated line',encoding='utf-8')
        p.inputs['translation']=str(translation)
    observation=Visual('observed','recognized',.2,.8,[0,0,40,20],.9,3)
    target=tmp_path/'result.json'
    with patch('lyricsync.pipeline.scan',return_value=([observation],{'duration':1,'content_hash':'fixture'})), patch('lyricsync.pipeline.suggest',side_effect=AssertionError('no network')) as provider:
        run(p,target)
    assert provider.call_count==0
    assert p.lyrics == []
    assert p.mappings and p.mappings[0].visual_ids==['observed']
    assert all(not row.lyric_ids for row in p.mappings)
    saved=load_project(target)
    assert saved.lyrics == []
    assert saved.visuals[0].text=='recognized'


def test_settings_dialog_recommended_and_shared_sampling_bounds(monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication,QDialog,QComboBox,QDoubleSpinBox
    from desktop_qt.dialogs import ocr_settings
    app=QApplication.instance() or QApplication([])
    seen=[]
    def inspect(dialog):
        combo=next(c for c in dialog.findChildren(QComboBox) if c.findData('rapidocr')>=0)
        fps=dialog.findChild(QDoubleSpinBox,'ocrSampleFps')
        seen.append(combo.currentData())
        assert (fps.minimum(),fps.maximum())==(OCR_MIN_SAMPLE_FPS,OCR_MAX_SAMPLE_FPS)
        assert '越高' in fps.toolTip()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    for settings in ({},{'engine':'tesseract'}):
        ocr_settings(SimpleNamespace(document=SimpleNamespace(project=SimpleNamespace(settings=settings),id='ocr-contract-fixture')),['CPUExecutionProvider'])
    assert seen==['rapidocr','tesseract']


def test_explicit_reference_remains_distinct(tmp_path):
    from lyricsync.pipeline import run
    video=tmp_path/'video.mp4';video.write_bytes(b'fixture')
    original=tmp_path/'original.txt';original.write_text('real supplied original',encoding='utf-8')
    p=Project();p.inputs.update(video=str(video),ktv=str(original))
    with patch('lyricsync.pipeline.scan',return_value=([Visual('v','OCR differs',.2,.8,[0,0,40,20],.9,3)],{'duration':1})):
        run(p,tmp_path/'result.json')
    assert [x.text for x in p.lyrics]==['real supplied original']
    assert p.visuals[0].text=='OCR differs'


def test_theme_progress_component_loads():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlEngine,QQmlComponent
    app=QApplication.instance() or QApplication([])
    engine=QQmlEngine()
    path=Path(__file__).resolve().parents[1]/'desktop_qt/qml/ThemeProgressBar.qml'
    component=QQmlComponent(engine,QUrl.fromLocalFile(str(path)))
    assert component.isReady(),[x.toString() for x in component.errors()]
    item=component.create()
    assert item is not None
    item.setProperty('value',.5)
    assert item.property('position')==.5
    item.setProperty('indeterminate',True)
    assert item.property('indeterminate') is True
    item.deleteLater()
