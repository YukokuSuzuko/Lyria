"""Picture/control adjacency using actual VideoOutput content geometry, offscreen."""
import pytest
from PySide6.QtCore import QPointF
from test_qt_phase4_ui_feedback import application, preview_ui, install_cues, present, preview_find

@pytest.mark.parametrize('resolution,size', [((1600,300),(900,700)),((300,1600),(900,700)),((640,360),(480,700)),((1920,1080),(1400,500)),((1600,300),(480,700))])
def test_controls_touch_fitted_picture_without_stretching(preview_ui, resolution, size):
    ui = preview_ui; ui.window.resize(*size)
    install_cues(ui, resolution=resolution, layout=False); present(ui, 2)
    video = preview_find(ui, 'videoPreview'); controls = preview_find(ui, 'playerControls')
    rect = video.property('contentRect')
    bottom = video.mapToScene(QPointF(0, rect.bottom())).y()
    top = controls.mapToScene(QPointF()).y()
    assert -.5 <= top - bottom <= 7, (resolution, size, top-bottom)
    assert rect.width()/rect.height() == pytest.approx(resolution[0]/resolution[1], rel=.001)

    assert ui.panel.property('minimumPanelHeight') < 420  # portrait media must not force a multi-screen viewport
