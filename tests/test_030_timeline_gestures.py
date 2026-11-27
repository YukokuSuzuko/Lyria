from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from test_qt_timeline_input import application,timeline_video,ui,find,point,wait
from tools.qt_event_loop_wait import delay

def test_actual_clip_range_ctrl_drag_and_existing_merge(ui,monkeypatch):
    app,b,window,timeline=ui
    actions=[]
    monkeypatch.setattr(b.media_controller,'seek',lambda *a:actions.append('seek'))
    monkeypatch.setattr(b.media_controller,'pause',lambda *a:actions.append('pause'))
    def pos(i):
        return point(find(window,'timelineClip_r'+str(i)))
    def click(i,mod):
        QTest.mouseClick(window,Qt.MouseButton.LeftButton,mod,pos(i))
        delay(45)
    click(40,Qt.KeyboardModifier.NoModifier)
    click(41,Qt.KeyboardModifier.ControlModifier)
    assert b._multi_selected=={'r40','r41'}
    click(40,Qt.KeyboardModifier.ShiftModifier)
    assert b._multi_selected=={'r40','r41'}
    original=b.document.project.to_dict()
    anchor=pos(40)
    QTest.mousePress(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier,anchor)
    for off in (15,30,60):
        QTest.mouseMove(window,anchor+QPoint(off,0),10)
    QTest.mouseRelease(window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier,anchor+QPoint(60,0))
    delay(60)
    assert b._multi_selected=={'r40','r41'} and b.document.project.to_dict()==original
    assert not actions
    assert b.command('row.merge',{'id':'r40'})
    assert len(b.document.project.mappings)==len(original['mappings'])-1
    b._history(False)
    assert b.document.project.to_dict()==original
