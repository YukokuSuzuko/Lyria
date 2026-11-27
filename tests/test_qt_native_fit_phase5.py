"""Logical work-area and responsive-layout regression, not native visual proof."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
import pytest
from PySide6.QtCore import QRect,QSize,QUrl,QObject,QPoint
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlEngine,QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from desktop_qt.window_bounds import fitted_geometry,WindowBounds
from desktop_qt.dialogs import ApiSettingsDialog
from tests.test_qt_native_theme import ReadOnlyBridge

@pytest.fixture(scope='module')
def app():
    from PySide6.QtQuickControls2 import QQuickStyle
    QQuickStyle.setStyle('Basic')
    application=QApplication.instance() or QApplication([])
    from PySide6.QtGui import QFontDatabase
    for font in ('msyh.ttc','msyhbd.ttc','segoeui.ttf'):
        path=Path('C:/Windows/Fonts')/font
        if path.is_file():assert QFontDatabase.addApplicationFont(str(path))>=0
    return application

@pytest.mark.parametrize('scale',[1,1.25,1.5,2])
def test_default_fits_smaller_work_area_in_logical_units(scale):
    work=QRect(0,0,int(2048/scale),int(1104/scale))
    actual=fitted_geometry(QRect(0,0,1400,940),work)
    assert work.contains(actual)
    assert actual.width()==min(1400,work.width())
    assert actual.height()==min(940,work.height())

@pytest.mark.parametrize('area',[QRect(-1920,0,1920,1040),QRect(2048,80,1024,552),QRect(0,0,640,480)])
def test_monitor_origin_and_screen_shrink(area):
    assert area.contains(fitted_geometry(QRect(1800,900,1400,940),area))

def test_valid_custom_size_and_position_are_preserved():
    before=QRect(120,70,900,650)
    assert fitted_geometry(before,QRect(0,0,2048,1104))==before

def test_empty_work_area_does_not_rewrite_geometry():
    before=QRect(10,20,800,600)
    assert fitted_geometry(before,QRect())==before

def test_policy_skips_maximized_minimized_then_fits_restore(app):
    window=QWindow();window.setMinimumSize(QSize(760,600));window.resize(1400,940)
    policy=WindowBounds(window)
    class Screen:
        def availableGeometry(self):return QRect(0,0,1024,552)
    policy.timer.stop();policy.screen=Screen()
    window.showMaximized();before=window.geometry();assert not policy.fit();assert window.geometry()==before
    window.showMinimized();before=window.geometry();assert not policy.fit();assert window.geometry()==before
    window.showNormal();policy.fit()
    assert Screen().availableGeometry().contains(window.frameGeometry())
    assert window.minimumHeight()<=552
    window.close()

@pytest.mark.parametrize('size',[(984,492),(1100,676),(760,724),(580,600),(360,340)])
def test_api_reflow_preserves_controls_and_scroll_access(app,size,tmp_path):
    dialog=ApiSettingsDialog(ReadOnlyBridge());dialog.resize(*size);dialog.show();QTest.qWait(80)
    try:
        assert dialog._api_section_mode==(size[0]>=940 and size[1]<740)
        for value in (False,True,False):
            dialog.advanced.setChecked(value);QTest.qWait(10)
            frame=dialog.findChild(QObject,'apiAdvancedBody')
            assert dialog.advanced.geometry().bottom()<frame.geometry().top()
            for widget in (dialog.provider,dialog.name,dialog.url,dialog.model,dialog.key,dialog.max_calls,dialog.retries,dialog.transport,dialog.thinking,dialog.reasoning,dialog.idle_timeout,dialog.total_timeout):
                assert widget.isVisible()
            assert frame.isEnabled()==value
        assert dialog._api_scroll.horizontalScrollBar().maximum()==0
        assert dialog._api_buttons.geometry().bottom()<dialog.height()
        if size[0]>=940:
            assert dialog._api_scroll.verticalScrollBar().maximum()==0, (size,dialog._api_content.sizeHint(),dialog._api_scroll.viewport().size())
            assert dialog._api_action_columns==4
        dialog.grab().save(str(tmp_path/f'api-{size[0]}x{size[1]}.png'))
    finally:dialog.reject()

def test_upper_marker_joins_actual_svg_tip_and_stays_above_thumb(app):
    engine=QQmlEngine();component=QQmlComponent(engine,QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1]/'desktop_qt/qml/Marker.qml')))
    marker=component.create();assert marker,[x.toString() for x in component.errors()]
    try:
        head=marker.findChild(QObject,'fixedMarker_1Head');guide=marker.findChild(QObject,'fixedMarker_1GuideTop');bottom=marker.findChild(QObject,'fixedMarker_1GuideBottom')
        tip=head.y()+head.height()*15/16
        assert 0<=tip-guide.y()<=.51
        assert guide.y()+guide.height()>tip
        assert guide.x()+guide.width()/2==head.x()+head.width()/2==marker.width()/2
        assert bottom.y()+bottom.height()<42-3.5-10
    finally:marker.deleteLater()

@pytest.mark.parametrize('work_area',[(2048,1104),(1638,883),(1365,736),(1024,552)])
def test_api_default_fit_reserves_font_and_fractional_dpi_margin(app,work_area):
    class Dialog(ApiSettingsDialog):
        def screen(self):
            class Screen:
                def availableGeometry(self):return QRect(0,0,*work_area)
            return Screen()
    dialog=Dialog(ReadOnlyBridge());dialog.show();QTest.qWait(80)
    try:
        assert dialog._api_scroll.verticalScrollBar().maximum()==0
        assert dialog.height()<=work_area[1]-60
        # A tight heightForWidth estimate can lose a pixel to native font/layout
        # rounding. Unless screen-capped, retain at least one extra text gap.
        if dialog.height()<work_area[1]-60:
            assert dialog._api_scroll.viewport().height()-dialog._api_content.minimumSizeHint().height()>=8
    finally:dialog.reject()
