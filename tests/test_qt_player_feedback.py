"""Player feedback regressions exercised through actual QML controls."""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest

from test_qt_preview_visibility import application, ui, install_cues, present, find, frame_rect


def click(ui, item):
    assert item is not None and item.isEnabled() and item.isVisible()
    pos = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.qWait(50)


def test_mouse_focus_outline_clears_but_keyboard_focus_remains(ui, monkeypatch):
    install_cues(ui, layout=False); present(ui, 2)
    monkeypatch.setattr(ui.bridge.media_controller, 'step', lambda delta: None)
    button = find(ui, 'transportForwardOne')
    click(ui, button)
    QTest.mouseMove(ui.window, QPoint(1, 1)); QTest.qWait(80)
    background = button.property('background')
    # QML value objects cannot be inspected as Python QObject properties.
    from PySide6.QtQml import QQmlExpression, qmlContext
    width = QQmlExpression(qmlContext(background), background, 'border.width')
    assert width.evaluate()[0] == 0
    ui.panel.forceActiveFocus(Qt.FocusReason.OtherFocusReason)
    button.forceActiveFocus(Qt.FocusReason.TabFocusReason); QTest.qWait(20)
    assert width.evaluate()[0] > 0


def test_outer_cue_controls_seek_exact_start_and_guard_boundaries(ui, monkeypatch):
    install_cues(ui, layout=False); present(ui, 2)
    ui.bridge._refresh()
    calls = []
    monkeypatch.setattr(ui.bridge.media_controller, 'seek', lambda frame: calls.append(frame))
    monkeypatch.setattr(ui.bridge.media_controller, 'pause', lambda: None)
    previous, following = find(ui, 'transportPreviousCue'), find(ui, 'transportNextCue')
    assert previous is not None and following is not None
    assert not previous.isEnabled() and following.isEnabled()
    click(ui, following)
    assert ui.bridge.selected == 'm1' and calls == [6]
    assert QAccessible.queryAccessibleInterface(previous).text(QAccessible.Text.Name) == '上一句句首'
    click(ui, previous)
    assert ui.bridge.selected == 'm0' and calls == [6, 1]
    ui.bridge.command('timeline.select', {'id': 'm3'}); QTest.qWait(50)
    assert not following.isEnabled() and previous.isEnabled()
    for width in (330, 500, 900):
        ui.window.resize(width, 700); QTest.qWait(100)
        buttons = find(ui, 'transportButtons')
        pos = buttons.mapToScene(QPointF())
        assert pos.x() >= 0 and pos.x() + buttons.width() <= ui.window.width()


def test_all_video_corners_cover_fractional_content_edges(ui):
    from PySide6.QtGui import QColor
    install_cues(ui,resolution=(960,540),layout=False)
    ui.bridge.document.project.settings['subtitle_preview']=False
    ui.bridge._changed();present(ui,2)
    for width,height in ((531,721),(532,722),(900,701),(480,680)):
        ui.window.resize(width,height);QTest.qWait(120)
        rect=frame_rect(ui);image=ui.window.grabWindow();dpr=image.width()/ui.window.width()
        import os
        from pathlib import Path
        if os.environ.get('LYRIA_FEEDBACK_EVIDENCE'):
            evidence=Path(os.environ['LYRIA_FEEDBACK_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
            image.save(str(evidence/f'corners-{width}x{height}-{dpr}.png'))
        background=QColor('#F7F5FB')
        for x in (rect.left()+.5,rect.right()-.5):
            for y in (rect.top()+.5,rect.bottom()-.5):
                actual=image.pixelColor(int(x*dpr),int(y*dpr))
                assert max(abs(actual.red()-background.red()),abs(actual.green()-background.green()),abs(actual.blue()-background.blue()))<12,(width,height,x,y,actual.name())


def test_cue_jump_label_is_an_action_and_targets_inclusive_vfr_boundary(ui,monkeypatch):
    from lyricsync.frames import FrameIndex
    install_cues(ui,entries=[(.2,.7,'甲'),(.4,1.4,'乙')],layout=False)
    ui.bridge.media_controller.index=FrameIndex([0,.1,.2,.4,.7,1.,1.4,1.8],2.)
    ui.bridge.command('timeline.select',{'id':'m0'})
    ui.bridge._refresh()
    calls=[];monkeypatch.setattr(ui.bridge.media_controller,'seek',lambda frame:calls.append(frame))
    for frame in (1,3,4,5,8):
        ui.bridge.command('timeline.select',{'id':'m0'})
        present(ui,frame)
        last=find(ui,'transportCueLast');first=find(ui,'transportCueFirst')
        assert last.property('tip')=='跳到本句出点（F4）'
        assert first.property('tip')=='跳到本句入点（F3）'
        click(ui,last);assert calls[-1]==4
        click(ui,first);assert calls[-1]==3
        assert ui.bridge.media['displayedFrame']==frame  # A requested seek isn't a presented frame.

