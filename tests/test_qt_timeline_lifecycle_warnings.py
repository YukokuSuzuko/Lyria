"""Offscreen Basic-style scrollbar feedback and delegate lifetime regression."""
from pathlib import Path
import pytest
from PySide6.QtCore import QPoint, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from test_qt_timeline_input import timeline_video, ui, find, point
from test_qt_timeline_snap import application, base_application
from test_qt_timeline_feedback_20261107 import evaluate


@pytest.fixture(autouse=True)
def timeline_messages(tmp_path):
    messages = []
    previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    yield messages
    qInstallMessageHandler(previous)
    (tmp_path / 'qt-messages.log').write_text('\n'.join(messages), encoding='utf-8')
    failures = [message for message in messages if 'FrameTimeline.qml' in message and
                ('Binding loop' in message or 'TypeError' in message or 'ReferenceError' in message)]
    assert not failures, '\n'.join(failures)


def test_horizontal_scrollbar_user_drag_preserves_span_and_stays_in_bounds(ui):
    app, bridge, window, timeline = ui
    scrollbar = find(window, 'timelineHorizontalScroll')
    start = point(scrollbar, (scrollbar.property('position') + scrollbar.property('size') / 2) * scrollbar.width())
    span = timeline.property('span')
    first = timeline.property('viewFirst')
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert scrollbar.property('pressed')
    QTest.mouseMove(window, start + QPoint(80, 0), 20)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(80, 0))
    QTest.qWait(30)
    assert timeline.property('viewFirst') > first
    assert timeline.property('span') == span
    assert timeline.property('followEnabled')
    assert timeline.property('contentFirst') <= timeline.property('viewFirst')
    assert timeline.property('viewLast') <= timeline.property('contentLast')


def test_preloaded_media_initialization_has_no_binding_reentry(application, tmp_path):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from desktop_qt.controller import Bridge
    from lyricsync.frames import FrameIndex
    from lyricsync.model import Mapping, Translation
    bridge = Bridge(storage_dir=tmp_path / 'preloaded-recovery')
    project = bridge.document.project
    project.video = {'width': 640, 'height': 360, 'duration': 50.0}
    project.settings.update(motion=False, subtitle_preview=False)
    project.translations = [Translation('t' + str(n), 'ABCD') for n in range(20)]
    project.mappings = [Mapping('m' + str(n), ['t' + str(n)], start=n * 2., end=n * 2. + 1.6)
                        for n in range(20)]
    bridge.media_controller.index = FrameIndex([n * .04 for n in range(1250)], 50.)
    bridge.media_controller._state.update(ready=True, totalFrames=1250, displayedFrame=480,
        requestedFrame=480, positionSeconds=19.16, playing=False)
    bridge.stage = 'review'; bridge.selected = 'm9'; bridge._changed()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(Path('desktop_qt/qml/Main.qml').resolve())))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        QTest.qWait(100)
        timeline = find(window, 'frameTimeline')
        assert timeline.property('viewFirst') <= 451 < timeline.property('viewLast')
        assert timeline.property('followEnabled')
    finally:
        window.setProperty('allowClose', True); window.close(); bridge.shutdown()
        engine.deleteLater(); application.processEvents()


def test_destroying_new_clip_delegates_cancels_deferred_entrance(ui):
    import shiboken6
    app, bridge, window, timeline = ui
    # Fresh IDs force ListView delegate recreation synchronously. Destroy the
    # panel before the queued initialization function gets an event-loop turn.
    rows = [dict(row, id='fresh-' + row['id'], cueId='fresh-' + row['cueId'])
            for row in bridge.timeline_model.items]
    bridge.timeline_model.replace(rows)
    tracks = find(window, 'timelineTracks')
    evaluate(tracks, 'forceLayout()')
    timeline.setParentItem(None)
    shiboken6.delete(timeline)
    QTest.qWait(40)
