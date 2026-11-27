"""Fixed import workspace bounds; no outer scrolling or foreground acceptance."""
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest
from test_qt_phase4_ui_feedback import chrome_ui, find, application


@pytest.mark.parametrize("size", [(760, 600), (800, 800)])
def test_import_and_preview_fit_fixed_workspace(chrome_ui, size, tmp_path):
    app, bridge, chrome, window = chrome_ui
    window.resize(*size)
    QTest.qWait(150)
    assert find(window, "makeScroll").property("contentItem") is None
    for name in ("mainPreviewPanel", "sideWorkspace", "importRecognitionPanel"):
        item = find(window, name)
        position = item.mapToScene(QPointF())
        assert item.isVisible() and item.width() >= 330 and item.height() >= 200
        assert 0 <= position.x() and position.x() + item.width() <= window.width()
        assert 0 <= position.y() and position.y() + item.height() <= window.height()
    assert window.grabWindow().save(str(tmp_path / "import-workspace.png"))
