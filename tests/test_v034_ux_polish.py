"""Comprehensive regression checks for Lyria v0.3.4 UX, layout, dialog, and onboarding improvements."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import pytest
from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionFocusRect,
)

import lyricsync
from lyricsync.local_models import NEXTFIRE
from lyricsync.model import Lyric, Mapping, Project, Translation
from lyricsync.project_document import ProjectDocument
from desktop_qt import font_preview_widget as fpw
from desktop_qt import native_theme, split_dialog, style_dialog, workspace_layout
from desktop_qt.controller import Bridge
from desktop_qt.font_preview_widget import (
    FontPreviewWidget,
    show_font_preview_dialog,
    system_font_inventory,
)
from desktop_qt.local_alignment_dialog import LocalAlignmentDialog


QML_MAIN = Path(__file__).resolve().parents[1] / "desktop_qt" / "qml" / "Main.qml"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _load_main_window(app, tmp_path, *, startup=False, allow_onboarding=False):
    QQuickStyle.setStyle("Basic")
    engine = QQmlApplicationEngine()
    bridge = Bridge(storage_dir=tmp_path / "storage", startup=startup, allow_onboarding=allow_onboarding)
    project = Project(
        lyrics=[Lyric("l1", "君の全てが好き", 1.0, 4.0), Lyric("l2", "次のステージへ", 4.5, 8.0)],
        translations=[Translation("t1", "你的全部我都喜欢"), Translation("t2", "前往下一个舞台")],
        mappings=[
            Mapping("m1", ["t1"], ["l1"], start=1.0, end=4.0),
            Mapping("m2", ["t2"], ["l2"], start=4.5, end=8.0),
        ],
    )
    bridge._adopt_project(project)
    bridge.selected = "m1"
    bridge._refresh(False)

    warnings = []
    engine.warnings.connect(lambda w: warnings.extend(str(item.toString()) for item in w))
    ctx = engine.rootContext()
    ctx.setContextProperty("bridge", bridge)
    ctx.setContextProperty("uiStyle", "desktop")
    ctx.setContextProperty("desktopBuildTag", lyricsync.__version__)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    assert engine.rootObjects(), f"Main.qml failed to load: {warnings}"
    win = engine.rootObjects()[0]
    assert isinstance(win, QQuickWindow)
    app.processEvents()
    return engine, bridge, win, warnings


def test_version_identity_v034():
    assert lyricsync.__version__ == "v0.3.4"


def test_default_model_nextfire_across_dialogs_and_model_center(app, tmp_path):
    doc = ProjectDocument(Project(mappings=[Mapping("m1", [], text_override="こんにちは")]))
    align_dlg = LocalAlignmentDialog(doc, setup_only=True)
    try:
        assert align_dlg.alignment_model.currentData() == NEXTFIRE.id
        assert align_dlg.alignment_model.itemData(0) == NEXTFIRE.id
        assert align_dlg.download_source.currentData() == "mirror"
        # Verify tabBar white-gap properties
        tb = align_dlg.tabs.tabBar()
        assert tb.drawBase() is False
        assert tb.expanding() is False
    finally:
        align_dlg.reject()

    bridge = Bridge(storage_dir=tmp_path / "mc")
    try:
        mc = bridge._model_center_state()
        assert mc["models"][0]["id"] == NEXTFIRE.id
        assert mc["downloadSource"] == "mirror"

        old_exec = QDialog.exec
        captured_combos = []
        captured_labels = []
        def cap(dlg):
            from PySide6.QtWidgets import QComboBox
            captured_combos.extend(dlg.findChildren(QComboBox))
            captured_labels.extend(lbl.text() for lbl in dlg.findChildren(QLabel))
            return QDialog.DialogCode.Rejected
        QDialog.exec = cap
        try:
            split_dialog.guide(bridge, "settings")
        finally:
            QDialog.exec = old_exec
        assert any(c.currentData() == "mirror" for c in captured_combos)
        nf_idx = next(i for i, t in enumerate(captured_labels) if "NextFire" in t)
        qw_idx = next(i for i, t in enumerate(captured_labels) if "Qwen" in t)
        assert nf_idx < qw_idx
    finally:
        bridge.shutdown()


def test_workspace_defaults_spectrum_80_and_controls_no_blank_above():
    assert workspace_layout.DEFAULTS["audioMode"] == "spectrum"
    assert workspace_layout.DEFAULTS["spectrumGain"] == pytest.approx(7.2)
    assert workspace_layout.LIMITS["controlsHeight"] == (-52.0, 0.0)
    clamped = workspace_layout.update_layout(workspace_layout.DEFAULTS, {"controlsHeight": 88.0})
    assert clamped["controlsHeight"] == 0.0


def test_no_focus_rect_style_brand_badge_and_localized_msgbox(app):
    proxy = native_theme.NoFocusRectStyle()
    img = QImage(64, 32, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    opt = QStyleOptionFocusRect()
    opt.rect = img.rect()
    proxy.drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, opt, painter, None)
    painter.end()
    # Drawing PE_FrameFocusRect must be a complete no-op
    assert img.pixelColor(0, 0).alpha() == 0
    assert img.pixelColor(32, 16).alpha() == 0

    # Customized QMessageBox copy + vector badge pixmap
    box = QMessageBox(QMessageBox.Icon.Warning, "提示", "需要确认", QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    try:
        native_theme.apply_dialog_theme(box)
        assert box.button(QMessageBox.StandardButton.Ok).text() == "确定"
        assert box.button(QMessageBox.StandardButton.Cancel).text() == "取消"
        pix = box.iconPixmap()
        assert not pix.isNull() and pix.width() >= 28
    finally:
        box.close()
        box.deleteLater()

    narrow = QDialog()
    try:
        narrow.resize(420, 260)
        native_theme.install_dialog_title(narrow)
        native_theme.apply_dialog_theme(narrow)
        if os.name == "nt":
            assert hasattr(narrow, "_lyria_title") and narrow._lyria_title.objectName() == "lyriaDialogTitle"
            title_pix = narrow._lyria_title.grab()
            assert not title_pix.isNull()
    finally:
        narrow.close()
        narrow.deleteLater()
        app.processEvents()


def test_font_preview_cached_and_no_frame_background(app, tmp_path):
    first = system_font_inventory()
    second = system_font_inventory()
    assert first == second
    assert fpw._CACHED_FONT_INVENTORY is not None

    w = FontPreviewWidget(storage_dir=tmp_path)
    try:
        bg_keys = [w.bg_combo.itemData(i) for i in range(w.bg_combo.count())]
        assert bg_keys == ["dark", "light"]
        assert "frame" not in bg_keys
    finally:
        w.deleteLater()

    titles = []
    old_exec = QDialog.exec
    QDialog.exec = lambda d: (titles.append(d.windowTitle()), QDialog.DialogCode.Rejected)[1]
    try:
        bridge = Bridge(storage_dir=tmp_path / "fp")
        try:
            show_font_preview_dialog(bridge)
        finally:
            bridge.shutdown()
    finally:
        QDialog.exec = old_exec
    assert titles == ["字体预览器"]


def test_aegisub_two_column_style_manager(app, tmp_path):
    bridge = Bridge(storage_dir=tmp_path / "sm")
    try:
        project = Project(
            lyrics=[Lyric("l1", "君の全てが好き", 1.0, 4.0)],
            mappings=[Mapping("m1", [], ["l1"], start=1.0, end=4.0)],
        )
        bridge._adopt_project(project)
        bridge.selected = "m1"
        dlg = style_dialog.StyleManagerDialog(bridge)
        try:
            assert dlg.catalog_combo.objectName() == "styleCatalogCombo"
            assert dlg.library_list.objectName() == "libraryStyleList"
            assert dlg.project_list.objectName() == "projectStyleList"
            assert dlg.project_list.count() >= 1
            for prefix in ("libraryOrder", "projectOrder"):
                for suffix in ("arrow-up", "arrow-down", "arrow-top", "arrow-bottom", "sort-az"):
                    assert dlg.findChild(QPushButton, f"{prefix}_{suffix}") is not None
        finally:
            dlg.reject()
    finally:
        bridge.shutdown()


def test_main_window_menus_badges_timeline_sync_and_onboarding(app, tmp_path, monkeypatch):
    engine, bridge, win, warnings = _load_main_window(app, tmp_path / "main1", allow_onboarding=False)
    try:
        assert not warnings
        assert bridge.state["onboardingOpen"] is False

        # View menu forbids closing player
        toggle_pic = win.findChild(QObject, "togglePicturePanel")
        assert toggle_pic is not None
        assert toggle_pic.property("visible") is False
        assert toggle_pic.property("enabled") is False

        # Tools menu exposes vocal separation action
        vocal_sep = win.findChild(QObject, "toolsVocalSeparationAction")
        assert vocal_sep is not None

        # Top-left mode badge exists & supports switching to 手动模式
        badge = win.findChild(QObject, "entryModeBadge")
        assert badge is not None
        assert bridge.command("stage.set", {"stage": "review"})
        app.processEvents()
        add_btn = win.findChild(QObject, "reviewAddBlankButton")
        fmt_btn = win.findChild(QObject, "reviewTimeFormat")
        timeline = win.findChild(QObject, "frameTimeline")
        assert add_btn is not None and add_btn.property("text") == "新增" and add_btn.property("glyph") == "plus"
        assert fmt_btn is not None and timeline is not None
        assert bool(timeline.property("showTimecodes")) is False
        assert fmt_btn.property("text") == "帧数"
        win.setProperty("reviewTimecodes", True)
        app.processEvents()
        assert bool(timeline.property("showTimecodes")) is True
        assert fmt_btn.property("text") == "分秒"

        assert bridge.command("project.newEntry", {"mode": "edit"})
        app.processEvents()
        assert "手动模式" in str(badge.property("text"))

        # Toast object exists
        toast = win.findChild(QObject, "noticeToast")
        assert toast is not None

        # Onboarding open & complete persistence
        assert bridge.command("onboarding.open", {})
        app.processEvents()
        assert bridge.state["onboardingOpen"] is True
        assert bridge.command("onboarding.complete", {})
        app.processEvents()
        assert bridge.state["onboardingOpen"] is False
        assert (tmp_path / "main1" / "storage" / "onboarding-guide.json").is_file()
    finally:
        win.setProperty("allowClose", True)
        win.close()
        bridge.shutdown()
        engine.deleteLater()
        app.processEvents()

    # Frozen desktop startup with allow_onboarding=True auto-opens on fresh profile, not after completion
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("LYRIA_EXPECTED_TEST_DESKTOP", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "windows")
    fresh = Bridge(storage_dir=tmp_path / "fresh_onb", startup=True, allow_onboarding=True)
    reopened = Bridge(storage_dir=tmp_path / "main1" / "storage", startup=True, allow_onboarding=True)
    try:
        assert fresh.state["onboardingOpen"] is True
        assert reopened.state["onboardingOpen"] is False
    finally:
        fresh.shutdown()
        reopened.shutdown()
