"""Shutdown failure is terminal UI; normal unsaved-close cancellation is unchanged.

These are offscreen UI contract checks, not a claim about the native crash cause.
"""
import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest
from test_qt_phase1_preview_workspace import ui
from test_qt_preview_visibility import application
from tools.qt_event_loop_wait import delay


@pytest.mark.parametrize('failure,dirty,switching', [
    (False, True, False), (False, False, False),
    (False, True, True), (False, False, True), (True, True, False)])
def test_close_dialog_distinguishes_terminal_save_failure(ui, failure, dirty, switching):
    doc=ui.bridge.document
    if dirty:doc.touch()
    else:doc.mark_saved(doc.revision)
    details={'shutdownFailure': failure, 'switching': switching, 'message': 'Saving the original target failed.'}
    ui.bridge._close_dialog=details
    ui.bridge._refresh(False)
    ui.bridge.closeDialogRequested.emit(details)
    delay(80)
    def item(name):
        value=ui.window.findChild(QObject,name)
        assert value is not None
        return value
    dialog=item('closeDialog')
    assert dialog.property('visible')
    expected_title = '切换工程确认' if switching else '关闭窗口确认'
    assert dialog.property('title')==('保存工程失败' if failure else expected_title)
    assert item('closeSave').property('text')==('重试保存' if failure else '保存工程' if dirty else '工程已保存')
    assert item('closeSave').property('enabled') is (failure or dirty)
    action=('不保存并' if dirty else '不导出并')+('切换工程' if switching else '关闭窗口')
    assert item('closeDiscard').property('text')==('不保存并退出' if failure else action)
    assert item('closeExport').property('text')=='导出主时间轴字幕'
    assert item('closeCancel').property('visible') is (not failure)
    assert item('closeExport').property('visible') is (not failure)
    if failure:
        assert item('closeDialogMessage').property('text')==details['message']
    ui.window.requestActivate();delay(20)
    QTest.keyClick(ui.window,Qt.Key_Escape);delay(40)
    assert dialog.property('visible'), 'NoAutoClose must prevent returning to a stopped editing surface'
    # Close only this test popup without selecting a backend save/discard action.
    dialog.setProperty('visible',False)
