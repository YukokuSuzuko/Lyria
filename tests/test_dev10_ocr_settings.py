"""Offscreen OCR settings. Hardware enumeration does not prove GPU inference."""
from PySide6.QtWidgets import QDialog,QComboBox,QLabel
from test_dev5_feedback_navigation import app,bridge
from desktop_qt.dialogs import ocr_settings


def test_tesseract_locks_cpu_and_rapidocr_restores_selected_device(bridge,monkeypatch):
    bridge.document.project.settings['ocr_device']='directml'
    before=bridge.document.snapshot()
    def inspect(dialog):
        engine=dialog.findChild(QComboBox,'ocrEngine')
        device=dialog.findChild(QComboBox,'ocrComputeDevice')
        assert device.currentData()=='directml' and device.isEnabled()
        assert device.currentText()=='显卡加速（DirectML）'
        engine.setCurrentIndex(engine.findData('tesseract'))
        assert device.currentData()=='cpu' and not device.isEnabled()
        engine.setCurrentIndex(engine.findData('rapidocr'))
        assert device.currentData()=='directml' and device.isEnabled()
        device.setCurrentIndex(device.findData('cuda'))
        engine.setCurrentIndex(engine.findData('tesseract'))
        engine.setCurrentIndex(engine.findData('rapidocr'))
        assert device.currentData()=='cuda'
        hint=dialog.findChild(QLabel,'ocrDeviceAvailability').text()
        assert 'DmlExecutionProvider' not in hint and 'CUDAExecutionProvider' not in hint
        assert '初始化失败' in hint and '下次运行' in hint
        assert '不提高单帧' in dialog.findChild(QLabel,'ocrSamplingHint').text()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    assert not ocr_settings(bridge,['CPUExecutionProvider','DmlExecutionProvider','CUDAExecutionProvider'])
    assert bridge.document.snapshot()==before


def test_initial_tesseract_stale_gpu_saves_cpu(bridge,monkeypatch):
    bridge.document.project.settings.update(engine='tesseract',ocr_device='cuda')
    def inspect(dialog):
        device=dialog.findChild(QComboBox,'ocrComputeDevice')
        assert device.currentData()=='cpu' and not device.isEnabled()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',inspect)
    assert ocr_settings(bridge,['CPUExecutionProvider','CUDAExecutionProvider'])
    assert bridge.document.project.settings['ocr_device']=='cpu'
