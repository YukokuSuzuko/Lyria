"""Real PreviewPanel render regressions using locally generated frames/sources.

These tests inspect painted pixels inside the actual video content rectangle,
not just nonempty bridge text or the existence of a cached QML delegate.
Linux software rendering does not establish Windows GPU/real-video acceptance.
"""
import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import numpy as np
import pytest
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from desktop_qt.controller import Bridge
from desktop_qt.media import Request
from lyricsync.audit_service import ffmpeg_executable
from lyricsync.frames import FrameIndex
from lyricsync.model import Mapping, Translation
from tools.qt_event_loop_wait import delay, wait_until

ROOT = Path(__file__).resolve().parents[1]
QML_ROOT = Path(os.environ.get('LYRIA_PREVIEW_QML_ROOT', ROOT / 'desktop_qt/qml'))
EVIDENCE = Path(os.environ['LYRIA_PREVIEW_EVIDENCE']) if os.environ.get('LYRIA_PREVIEW_EVIDENCE') else None


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def find(ui, name):
    return next((item for item in walk(ui.window.contentItem()) if item.objectName() == name), None)


@pytest.fixture(scope='module')
def application():
    QQuickStyle.setStyle('Basic')
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui(application, tmp_path):
    bridge = engine = window = None
    try:
        bridge = Bridge(storage_dir=tmp_path / 'recovery')
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('bridge', bridge)
        engine.loadData(b'''import QtQuick
    import QtQuick.Controls
    import "."
    ApplicationWindow {
        visible: true; width: 900; height: 700; color: "#F7F5FB"
        PreviewPanel {
            objectName: "previewUnderTest"; anchors.fill: parent
            controller: bridge; media: bridge.media; state: bridge.uiState; review: true
        }
    }''', QUrl.fromLocalFile(str(QML_ROOT / 'PreviewVisibilityHarness.qml')))
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        result = SimpleNamespace(app=application, bridge=bridge, window=window,
                                 panel=find(SimpleNamespace(window=window), 'previewUnderTest'))
        yield result
    finally:
        if bridge is not None:
            bridge.shutdown()
            bridge.wait_for_shutdown(application)
            assert bridge.shutdown_complete
            assert not bridge.media_controller.worker.thread.is_alive()
            assert not bridge.jobs.live_threads()
        if window is not None:
            window.setProperty('allowClose', True)
            window.close()
        if engine is not None:
            from PySide6.QtCore import QEvent
            destroyed=[]
            engine.destroyed.connect(lambda: destroyed.append(True))
            engine.deleteLater()
            application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            from tools.qt_event_loop_wait import wait_until
            wait_until(lambda: bool(destroyed))
        application.processEvents()


def install_cues(ui, *, resolution=(1600, 300), entries=None, layout=True):
    entries = entries or [(0., .5, '第一句待确认中文'), (.5, 1., '第二句中文'),
                          (1.2, 1.5, ''), (1.5, 2., '末句中文')]
    p = ui.bridge.document.project
    p.video = dict(width=resolution[0], height=resolution[1], duration=2.)
    p.translations = [Translation('t' + str(n), text) for n, (_, _, text) in enumerate(entries)]
    p.mappings = [Mapping('m' + str(n), ['t' + str(n)], start=a, end=b)
                  for n, (a, b, _) in enumerate(entries)]
    p.settings.update(motion=False, font_size=20, font='Noto Sans CJK SC')
    ui.bridge.selected = 'm0'
    ui.bridge.stage = 'review'
    ui.bridge._changed()
    if not layout:
        ui.bridge.layout_timer.stop()
    m = ui.bridge.media_controller
    m.index = FrameIndex([n / 10 for n in range(20)], 2.)
    m.metadata = dict(has_audio=False)
    m._state.update(ready=True, loaded=True, totalFrames=20, durationSeconds=2.,
                    width=resolution[0], height=resolution[1])
    if layout:
        wait_until(lambda: ui.bridge._layout_revision == ui.bridge.document.revision)
    return p


def present(ui, frame, resolution=None):
    m = ui.bridge.media_controller
    resolution = resolution or (m._state['width'], m._state['height'])
    image = QImage(*resolution, QImage.Format.Format_RGB888)
    image.fill(0xff122638)
    request = Request(m.epoch, m.transport, m.sequence, frame, time.monotonic())
    m._present(request, image, m.index.stamp(frame), {})
    wait_until(lambda: find(ui, 'videoPreview').property('contentRect').width() > 1)
    delay(35)
    return request, image


def enter_roi_import(ui):
    # The standalone PreviewPanel test harness has no Main.qml page binding.
    assert ui.bridge.command('stage.set', {'stage': 'import'})
    ui.panel.setProperty('review', False)
    delay(30)


def frame_rect(ui):
    video = find(ui, 'videoPreview')
    rect = video.property('contentRect')
    top_left = video.mapToScene(rect.topLeft())
    from PySide6.QtCore import QRectF
    return QRectF(top_left.x(), top_left.y(), rect.width(), rect.height())


def visible_label(ui):
    return find(ui, 'previewSubtitle_0') or find(ui, 'subtitleFallback')


def shot(ui, name):
    image = ui.window.grabWindow()
    assert not image.isNull()
    if EVIDENCE:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        assert image.save(str(EVIDENCE / (name + '.png')))
    return image


def pixels(image):
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    return np.frombuffer(rgb.constBits(), dtype=np.uint8).reshape(rgb.height(), rgb.bytesPerLine())[:, :rgb.width()*3].reshape(rgb.height(), rgb.width(), 3).copy()


def painted_text_inside_frame(ui, label, name):
    assert label.isVisible()
    delay(35)
    with_text = pixels(shot(ui, name))
    label.setVisible(False)
    delay(35)
    without_text = pixels(ui.window.grabWindow())
    label.setVisible(True)
    delay(20)
    rect = frame_rect(ui).toAlignedRect()
    delta = np.any(with_text != without_text, axis=2)
    inside = delta[rect.y():rect.y()+rect.height(), rect.x():rect.x()+rect.width()]
    return int(inside.sum()), int(delta.sum())


@pytest.mark.parametrize('resolution,window_size', [((1600, 300), (900, 700)),
                                                    ((300, 1600), (900, 700)),
                                                    ((640, 360), (480, 700)),
                                                    ((1920, 1080), (1400, 500))])
def test_fallback_is_painted_inside_actual_frame(ui, resolution, window_size):
    ui.window.resize(*window_size)
    install_cues(ui, resolution=resolution, layout=False)
    present(ui, 1)
    label = find(ui, 'subtitleFallback')
    wait_until(label.isVisible)
    inside, total = painted_text_inside_frame(ui, label, f'fallback-{resolution[0]}x{resolution[1]}-{window_size[0]}x{window_size[1]}')
    assert inside > 20, (inside, total, frame_rect(ui), label.mapToScene(QPointF()))
    assert inside >= total * .98, (inside, total)
    controls = find(ui, 'playerControls')
    assert controls.mapToScene(QPointF()).y() >= frame_rect(ui).bottom()
    assert ui.bridge.state['roi'] is None


@pytest.mark.parametrize('resolution,window_size', [((1600, 300), (900, 700)),
                                                    ((300, 1600), (900, 700)),
                                                    ((640, 360), (480, 700)),
                                                    ((1920, 1080), (1400, 500))])
def test_completed_layout_is_painted_inside_actual_frame(ui, resolution, window_size):
    ui.window.resize(*window_size)
    install_cues(ui, resolution=resolution)
    present(ui, 1)
    label = find(ui, 'previewSubtitle_0')
    assert label and label.property('text') == '第一句待确认中文'
    inside, total = painted_text_inside_frame(ui, label, f'layout-{resolution[0]}x{resolution[1]}-{window_size[0]}x{window_size[1]}')
    assert inside > 20, (inside, total)
    assert inside >= total * .98, (inside, total)


def test_half_open_boundaries_empty_chinese_filter_and_selection(ui, monkeypatch):
    p = install_cues(ui, resolution=(640, 360))
    requests = []
    monkeypatch.setattr(ui.bridge.media_controller.worker, 'seek', requests.append)
    for frame, expected, status in [(1, '第一句待确认中文', 'single'), (5, '第一句待确认中文', 'single'),
                                    (6, '第二句中文', 'single'), (10, '第二句中文', 'single'),
                                    (11, '', 'gap'), (13, '', 'single'),
                                    (16, '末句中文', 'single'), (20, '末句中文', 'single')]:
        present(ui, frame)
        assert ui.bridge.media['subtitleText'] == expected
        assert ui.bridge.media['frameStatus'] == status
        label = find(ui, 'previewSubtitle_0')
        assert (label.property('text') if label else '') == expected
        assert not find(ui, 'subtitleFallback').isVisible()
    present(ui, 1)
    # Selection and the review-only filter must not masquerade as transport.
    assert ui.bridge.command('timeline.select', {'id': 'm3'})
    assert ui.bridge.selected == 'm3'
    assert ui.bridge.media['subtitleText'] == '第一句待确认中文'
    p.mappings[0].confirmed = True
    ui.bridge._changed()
    assert ui.bridge.command('view.filter', {'unconfirmedOnly': True})
    wait_until(lambda: ui.bridge._layout_revision == ui.bridge.document.revision)
    assert ui.bridge.media['hiddenCueCount'] == 1
    assert ui.bridge.media['subtitleText'] == '第一句待确认中文'
    label = find(ui, 'previewSubtitle_0')
    assert painted_text_inside_frame(ui, label, 'filtered-confirmed-still-visible')[0] > 20
    assert not requests


def test_pending_target_and_stale_seeks_do_not_replace_displayed_subtitle(ui, monkeypatch):
    install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    m = ui.bridge.media_controller
    requests = []
    monkeypatch.setattr(m.worker, 'seek', requests.append)
    assert ui.bridge.command('media.seek', {'frame': 8})
    assert ui.bridge.command('media.seek', {'frame': 17})
    assert m.snapshot()['requestedFrame'] == 17
    assert m.snapshot()['displayedFrame'] == 1
    assert ui.bridge.media['subtitleText'] == '第一句待确认中文'
    image = m._last_image
    m._present(requests[0], image, m.index.stamp(8), {})
    assert m.snapshot()['displayedFrame'] == 1
    m._present(requests[1], image, m.index.stamp(17), {})
    delay(30)
    assert ui.bridge.media['subtitleText'] == '末句中文'
    assert find(ui, 'previewSubtitle_0').property('text') == '末句中文'
    m._present(requests[0], image, m.index.stamp(8), {})
    delay(30)
    assert ui.bridge.media['subtitleText'] == '末句中文'
    assert len(requests) == 2


def test_structured_subtitles_hidden_when_actual_source_is_cleared(ui):
    install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    assert find(ui, 'previewSubtitle_0').isVisible()
    ui.bridge.media_controller.clear()
    delay(35)
    label = find(ui, 'previewSubtitle_0')
    assert label is None or not label.isVisible()
    assert not find(ui, 'subtitleFallback').isVisible()


def test_async_layout_error_keeps_painted_fallback_and_recovers(ui, monkeypatch):
    from lyricsync import export
    entered = threading.Event()
    release = threading.Event()
    original = export.layout_entries

    def delayed(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError('synthetic layout gate timed out')
        raise ValueError('synthetic placement failure')

    monkeypatch.setattr(export, 'layout_entries', delayed)
    install_cues(ui, layout=False)
    present(ui, 1)
    ui.bridge._begin_layout()
    try:
        wait_until(entered.is_set)
        assert ui.bridge.media['subtitleLayoutPending']
        fallback = find(ui, 'subtitleFallback')
        assert painted_text_inside_frame(ui, fallback, 'layout-pending-visible')[0] > 40
    finally:
        release.set()
    wait_until(lambda: bool(ui.bridge.media['subtitleLayoutError']))
    assert painted_text_inside_frame(ui, fallback, 'layout-error-visible')[0] > 40
    monkeypatch.setattr(export, 'layout_entries', original)
    assert ui.bridge.command('row.edit', {'id': 'm0', 'text': '修改后中文仍可见'})
    wait_until(lambda: ui.bridge._layout_revision == ui.bridge.document.revision)
    assert not ui.bridge.media['subtitleLayoutError']
    assert not fallback.isVisible()
    assert find(ui, 'previewSubtitle_0').property('text') == '修改后中文仍可见'
    assert painted_text_inside_frame(ui, find(ui, 'previewSubtitle_0'), 'layout-recovered-visible')[0] > 20


def test_subtitles_wait_for_first_presented_frame_after_index_ready(ui):
    install_cues(ui, resolution=(640, 360))
    # Index-ready is not picture-ready. Do not flash subtitle text over an
    # empty stage while the first decode is still outstanding.
    ui.bridge.media_controller.changed.emit()
    delay(30)
    assert ui.bridge.media['ready'] and ui.bridge.media['displayedFrame'] == 0
    assert find(ui, 'previewSubtitle_0') is None
    assert not find(ui, 'subtitleFallback').isVisible()
    present(ui, 1)
    assert find(ui, 'previewSubtitle_0').isVisible()


def test_late_layout_from_previous_project_cannot_replace_new_text(ui, monkeypatch):
    from lyricsync import export
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    original = export.layout_entries

    def delayed(entries, *args, **kwargs):
        if entries and entries[0][2] == '旧工程中文':
            entered.set()
            try:
                if not release.wait(5):
                    raise TimeoutError('synthetic old-project layout gate timed out')
                return original(entries, *args, **kwargs)
            finally:
                completed.set()
        return original(entries, *args, **kwargs)

    monkeypatch.setattr(export, 'layout_entries', delayed)
    install_cues(ui, resolution=(640, 360), entries=[(0., 2., '旧工程中文')], layout=False)
    present(ui, 1)
    ui.bridge._begin_layout()
    try:
        wait_until(entered.is_set)
        ui.bridge._new()
        install_cues(ui, resolution=(640, 360), entries=[(0., 2., '新工程中文')])
        present(ui, 1)
        assert find(ui, 'previewSubtitle_0').property('text') == '新工程中文'
    finally:
        release.set()
    wait_until(completed.is_set)
    delay(80)
    assert ui.bridge.media['subtitleText'] == '新工程中文'
    assert find(ui, 'previewSubtitle_0').property('text') == '新工程中文'
    assert not ui.bridge.media['subtitleLayoutPending']
    assert painted_text_inside_frame(ui, find(ui, 'previewSubtitle_0'), 'new-project-after-old-layout')[0] > 20


@pytest.fixture(scope='module')
def sources(tmp_path_factory):
    folder = tmp_path_factory.mktemp('preview-sources')
    result = []
    for name, size, color in [('wide', '640x160', '0x122638'), ('portrait', '240x640', '0x182d43')]:
        path = folder / (name + '.mp4')
        subprocess.run([ffmpeg_executable(), '-loglevel', 'error', '-f', 'lavfi', '-i',
                        f'color=c={color}:size={size}:rate=10:duration=2', '-an',
                        '-c:v', 'libx264', '-g', '10', '-pix_fmt', 'yuv420p', str(path)], check=True)
        result.append(path)
    return result


def test_real_sources_switch_aspect_and_preview_changes_do_not_decode(ui, sources):
    m = ui.bridge.media_controller
    old_request = old_image = None
    for n, path in enumerate(sources):
        if n:
            ui.bridge._new()
        assert ui.bridge.command('media.open', {'path': str(path)})
        wait_until(lambda: m.snapshot()['displayedFrame'] == 1, timeout_ms=15000)
        p = ui.bridge.document.project
        p.translations = [Translation('t', '宽屏实帧中文' if n == 0 else '竖屏实帧中文')]
        p.mappings = [Mapping('m', ['t'], start=0., end=2.)]
        p.settings.update(font='Noto Sans CJK SC', motion=False)
        ui.bridge._changed()
        wait_until(lambda: ui.bridge._layout_revision == ui.bridge.document.revision)
        delay(35)
        label = find(ui, 'previewSubtitle_0')
        assert label.property('text') == p.translations[0].text
        assert painted_text_inside_frame(ui, label, 'real-source-' + path.stem)[0] > 20
        # Both source generation and actual presented-frame state must resist
        # a late frame from the previous video.
        if old_request is not None:
            before = m.snapshot()['presentedFrames']
            m._present(old_request, old_image, 0., {})
            delay(30)
            assert m.snapshot()['presentedFrames'] == before
            assert find(ui, 'previewSubtitle_0').property('text') == '竖屏实帧中文'
        assert ui.bridge.command('media.seek', {'frame': 20})
        wait_until(lambda: m.snapshot()['displayedFrame'] == 20)
        old_request = Request(m.epoch, m.transport, m.sequence, 1, time.monotonic())
        old_image = m._last_image
        before = {key: m.snapshot()[key] for key in ('decoderSeeks', 'decodedFrames', 'presentedFrames', 'displayedFrame', 'requestedFrame')}
        sequence = m.sequence
        for width, height in [(480, 700), (1400, 500), (900, 700)]:
            ui.window.resize(width, height)
            delay(30)
            assert ui.bridge.command('settings.set', {'key': 'subtitlePreview', 'value': False})
            delay(10)
            assert find(ui, 'previewSubtitle_0') is None
            assert not find(ui, 'subtitleFallback').isVisible()
            assert ui.bridge.command('settings.set', {'key': 'subtitlePreview', 'value': True})
        wait_until(lambda: ui.bridge._layout_revision == ui.bridge.document.revision)
        delay(30)
        assert find(ui, 'previewSubtitle_0').isVisible()
        assert m.sequence == sequence
        assert {key: m.snapshot()[key] for key in before} == before
        assert ui.bridge.state['roi'] is None


def test_invalid_placement_preserves_style_and_anchor_then_undo_recovers(ui):
    p = install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    ui.bridge.history.push(p, '验证不适合的字幕位置')
    p.settings.update(subtitle_position='custom', subtitle_height_percent=5, font_size=50)
    ui.bridge._changed()
    wait_until(lambda: bool(ui.bridge.media['subtitleLayoutError']))
    assert '超出' in ui.bridge.media['subtitleLayoutError']
    fallback = find(ui, 'subtitleFallback')
    assert not fallback.isVisible()
    row=ui.bridge.media['subtitleRows'][0]
    assert row['yNormalized']==pytest.approx(.05)
    assert row['fontSizeNormalized']==pytest.approx(50/360)
    assert find(ui,'previewSubtitle_0').isVisible()
    assert ui.bridge.command('history.undo', {})
    wait_until(lambda: ui.bridge._layout_revision == ui.bridge.document.revision)
    assert not ui.bridge.media['subtitleLayoutError']
    assert not fallback.isVisible()
    assert find(ui, 'previewSubtitle_0').isVisible()
    assert ui.bridge.media['displayedFrame'] == 1


def test_changing_active_cue_cancels_uncommitted_subtitle_drag(ui):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    label = find(ui, 'previewSubtitle_0')
    start = label.mapToScene(QPointF(label.width()/2, label.height()/2)).toPoint()
    before = ui.bridge.document.snapshot()
    QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(ui.window, start + QPoint(30, -450), 10)
    assert abs(ui.panel.property('subtitleDragX')) > 10
    present(ui, 8)
    QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(30, -450))
    delay(35)
    assert ui.bridge.document.snapshot() == before
    assert painted_text_inside_frame(ui, find(ui, 'previewSubtitle_0'), 'canceled-drag-next-cue')[0] > 20
    assert ui.panel.property('subtitleDragX') == 0
    assert ui.panel.property('subtitleDragY') == 0
    assert find(ui, 'previewSubtitle_0').property('text') == '第二句中文'


@pytest.mark.parametrize('interruption', ['focus', 'hidden', 'roi', 'source', 'preview-off'])
def test_subtitle_drag_interruption_never_commits_on_later_release(ui, interruption):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    label = find(ui, 'previewSubtitle_0')
    start = label.mapToScene(QPointF(label.width()/2, label.height()/2)).toPoint()
    QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(ui.window, start + QPoint(30, -20), 10)
    assert ui.panel.property('subtitleDragging')
    if interruption == 'focus':
        ui.panel.setProperty('windowActive', False)
        ui.panel.setProperty('windowActive', True)
    elif interruption == 'hidden':
        ui.panel.setVisible(False)
        ui.panel.setVisible(True)
    elif interruption == 'roi':
        ui.panel.setProperty('roiSelecting', True)
        ui.panel.setProperty('roiSelecting', False)
    elif interruption == 'source':
        ui.bridge.media_controller.clear()
    else:
        assert ui.bridge.command('settings.set', {'key': 'subtitlePreview', 'value': False})
    delay(20)
    expected = ui.bridge.document.snapshot()
    assert not ui.panel.property('subtitleDragging')
    assert ui.panel.property('subtitleDragX') == ui.panel.property('subtitleDragY') == 0
    QTest.mouseMove(ui.window, start + QPoint(60, -40), 10)
    QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(60, -40))
    delay(30)
    assert ui.bridge.document.snapshot() == expected
    assert ui.panel.property('subtitleDragX') == ui.panel.property('subtitleDragY') == 0


def test_normal_subtitle_drag_commits_one_global_position_with_ass_and_undo(ui, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from lyricsync.export import placement_options, render_ass
    p = install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    requests = []
    monkeypatch.setattr(ui.bridge.media_controller.worker, 'seek', requests.append)
    label = find(ui, 'previewSubtitle_0')
    start = label.mapToScene(QPointF(label.width()/2, label.height()/2)).toPoint()
    before = ui.bridge.document.snapshot()
    history_count = len(ui.bridge.history.undo_stack)
    QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(ui.window, start + QPoint(30, -20), 10)
    assert ui.bridge.document.snapshot() == before
    QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(30, -20))
    wait_until(lambda: ui.bridge._layout_revision == ui.bridge.document.revision)
    assert len(ui.bridge.history.undo_stack) == history_count + 1
    assert p.settings['subtitle_position'] == 'custom'
    assert p.settings['subtitle_horizontal_percent'] > 50
    row = ui.bridge.media['subtitleRows'][0]
    ass = render_ass([(0., .5, '第一句待确认中文')], (640, 360), font_size=p.settings['font_size'], **placement_options(p))
    assert rf"\pos({round(row['xNormalized']*640)},{round(row['yNormalized']*360)})" in ass
    assert ui.bridge.command('history.undo', {})
    wait_until(lambda: ui.bridge._layout_revision == ui.bridge.document.revision)
    assert ui.bridge.document.project.settings.get('subtitle_position') != 'custom'
    assert not ui.panel.property('subtitleDragging')
    assert ui.panel.property('subtitleDragX') == ui.panel.property('subtitleDragY') == 0
    assert ui.bridge.media['displayedFrame'] == 1
    assert not requests


@pytest.mark.parametrize('status', ['pending', 'error'])
def test_layout_badges_require_an_actual_picture(ui, status):
    # A delayed layout result is not a video picture. Fresh projects and a
    # cleared/replaced source must not say a visible subtitle is being laid out.
    ui.bridge.layout_timer.stop()
    ui.bridge._layout_running = status == 'pending'
    ui.bridge._layout_error = 'synthetic layout failure' if status == 'error' else ''
    ui.bridge.mediaChanged.emit()
    delay(20)
    badge = find(ui, 'subtitleLayoutPendingBadge' if status == 'pending' else 'subtitleLayoutErrorBadge')
    assert not badge.isVisible()
    ui.bridge._layout_running = False
    install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    ui.bridge._layout_running = status == 'pending'
    ui.bridge._layout_error = 'synthetic layout failure' if status == 'error' else ''
    ui.bridge.mediaChanged.emit()
    delay(20)
    assert badge.isVisible()
    ui.bridge.media_controller.clear()
    delay(20)
    assert not badge.isVisible()
    ui.bridge._layout_running = False


def click_preview_item(ui, item):
    """Route a real window mouse event through Qt Quick's hit testing."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    assert item is not None and item.isVisible() and item.isEnabled()
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    delay(40)


def icon_image(button):
    return next(item for item in walk(button) if item.property('source') is not None)


def qml_enum(item, name):
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    value, undefined = QQmlExpression(QQmlEngine.contextForObject(item), item, f'Number({name})').evaluate()
    assert not undefined
    return value


def transport_snapshot(ui):
    media = ui.bridge.media_controller.snapshot()
    return {key: media[key] for key in ('decoderSeeks', 'decodedFrames', 'presentedFrames',
                                       'displayedFrame', 'requestedFrame')}


def test_roi_visibility_mouse_click_switches_rendered_state_without_commands(ui, monkeypatch):
    install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    enter_roi_import(ui)
    assert ui.bridge.command('roi.set', {'x': .1, 'y': .6, 'width': .8, 'height': .3})
    delay(40)
    ocr_calls = []
    monkeypatch.setattr(ui.bridge, '_run_ocr', lambda: ocr_calls.append('ocr'))
    monkeypatch.setattr(ui.bridge, '_auto_roi', lambda: ocr_calls.append('auto'))
    before = ui.bridge.document.snapshot()
    before_transport = transport_snapshot(ui)
    history_count = len(ui.bridge.history.undo_stack)
    button = find(ui, 'showRoiButton')
    overlay = find(ui, 'roiOverlay')
    icon = icon_image(button)
    assert button.property('text') == ''
    assert button.property('tip') == '显示 OCR 选区'
    from PySide6.QtGui import QAccessible
    assert QAccessible.queryAccessibleInterface(button).text(QAccessible.Text.Name) == '显示 OCR 选区'
    assert ui.panel.property('cropVisible') and overlay.isVisible()
    assert button.property('glyph') == 'ocr-region' and button.property('checked')
    assert button.property('glyph') == 'ocr-region' and button.property('checked')
    assert icon.property('source').toString().endswith('/ocr-region.svg')
    assert qml_enum(icon, 'status') == 1  # QQuickImage.Ready, not merely a source URL.
    assert overlay.isVisible()
    shown = pixels(shot(ui, 'preview-details-roi-eye-visible'))
    icon_top_left = button.mapToScene(QPointF()).toPoint()
    x, y, width, height = icon_top_left.x(), icon_top_left.y(), int(button.width()), int(button.height())
    for n in range(3):
        click_preview_item(ui, button)
        assert not ui.panel.property('cropVisible')
        assert not overlay.isVisible()
        assert button.property('glyph') == 'ocr-region' and not button.property('checked')
        assert icon.property('source').toString().endswith('/ocr-region.svg')
        assert qml_enum(icon, 'status') == 1
        hidden = pixels(shot(ui, 'preview-details-roi-eye-hidden'))
        # The confirmed OCR glyph stays constant; the actual checked button pixels must change.
        assert np.any(shown[y:y + height, x:x + width] != hidden[y:y + height, x:x + width])
        click_preview_item(ui, button)
        assert overlay.isVisible() and button.property('glyph') == 'ocr-region' and button.property('checked')
    assert ui.bridge.document.snapshot() == before
    assert len(ui.bridge.history.undo_stack) == history_count
    assert transport_snapshot(ui) == before_transport
    assert not ocr_calls


def cancel_roi_dialog_with_mouse(ui):
    # StandardButtons is the real Dialog.Cancel control, not a direct close().
    cancel = next(item for item in walk(ui.window.contentItem())
                  if item.isVisible() and item.property('text') in ('Cancel', '取消')
                  and item.property('down') is not None)
    click_preview_item(ui, cancel)
    manual = find(ui, 'manualRoiChoice')
    assert manual is None or not manual.isVisible()


def test_roi_setting_mouse_auto_manual_and_cancel_keep_original_ocr_routing(ui, monkeypatch, sources):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from lyricsync import vision
    p = install_cues(ui, resolution=(640, 360))
    p.inputs['video'] = str(sources[0])
    present(ui, 1)
    enter_roi_import(ui)
    calls = []
    detector = object()
    monkeypatch.setattr(vision, 'make_ocr', lambda settings: calls.append('make_ocr') or detector)

    def propose(video, engine, cancel):
        assert video == str(sources[0]) and engine is detector
        calls.append('propose_regions')
        return [{'box': [64, 216, 576, 324], 'hits': 7, 'examples': ['sample']}]

    monkeypatch.setattr(vision, 'propose_regions', propose)
    monkeypatch.setattr('desktop_qt.roi_controls.region_choices',lambda bands: bands)
    monkeypatch.setattr(ui.bridge, '_run_ocr', lambda: calls.append('full_ocr'))
    button = find(ui, 'selectRoiButton')
    assert button.property('tip') == '设置选区'
    assert button.property('glyph') == 'roi-settings'
    assert qml_enum(icon_image(button), 'status') == 1
    before = ui.bridge.document.snapshot()
    before_transport = transport_snapshot(ui)
    for n in range(2):
        click_preview_item(ui, button)
        assert find(ui, 'manualRoiChoice').isVisible()
        assert find(ui, 'autoRoiChoice').isVisible()
        shot(ui, 'preview-details-roi-settings-dialog')
        cancel_roi_dialog_with_mouse(ui)
        assert not ui.panel.property('roiSelecting')
        assert ui.bridge.document.snapshot() == before
        assert not calls
    # The production auto command/job/completion path runs. Only the detector
    # seam is deterministic here; this is not an OCR accuracy acceptance test.
    click_preview_item(ui, button)
    click_preview_item(ui, find(ui, 'autoRoiChoice'))
    wait_until(lambda: p.settings.get('roi') == [64, 216, 576, 324])
    assert calls == ['make_ocr', 'propose_regions']
    assert not ui.panel.property('roiSelecting')
    automatic = ui.bridge.document.snapshot()
    first_region_id = p.settings['ocr_regions'][0]['id']
    assert ui.panel.property('cropVisible')
    click_preview_item(ui, find(ui, 'showRoiButton'))
    assert not ui.panel.property('cropVisible')
    click_preview_item(ui, find(ui, 'showRoiButton'))
    assert ui.panel.property('cropVisible')
    click_preview_item(ui, button)
    click_preview_item(ui, find(ui, 'manualRoiChoice'))
    assert ui.panel.property('roiSelecting') and ui.panel.property('cropVisible')
    assert button.property('tip') == '取消框选'
    assert button.property('glyph') == 'roi-settings'
    shot(ui, 'preview-details-roi-manual-cancel')
    click_preview_item(ui, button)
    assert not ui.panel.property('roiSelecting')
    assert button.property('tip') == '重新框选字幕区域'
    assert button.property('glyph') == 'roi-settings'
    assert p.settings['roi'] == [64, 216, 576, 324]
    assert ui.bridge.document.snapshot() == automatic
    # A second manual entry commits exactly the existing roi.set transaction.
    click_preview_item(ui, button)
    click_preview_item(ui, find(ui, 'manualRoiChoice'))
    rect = frame_rect(ui)
    start = QPointF(rect.x() + rect.width() * .2, rect.y() + rect.height() * .3).toPoint()
    end = QPointF(rect.x() + rect.width() * .75, rect.y() + rect.height() * .8).toPoint()
    history_count = len(ui.bridge.history.undo_stack)
    QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(ui.window, end, 20)
    QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    delay(40)
    assert not ui.panel.property('roiSelecting')
    assert p.settings['roi'] == pytest.approx([128, 108, 480, 288], abs=1)
    assert p.settings['ocr_multi_region'] and p.settings['ocr_regions'][0]['id'] == first_region_id
    assert p.settings['ocr_regions'][0]['box'] == p.settings['roi']
    assert len(ui.bridge.history.undo_stack) == history_count + 1
    assert calls == ['make_ocr', 'propose_regions']
    assert transport_snapshot(ui) == before_transport
    shot(ui, 'preview-details-roi-manual-committed')
    from copy import deepcopy
    # Draw a second band from the preview toolbar. Existing identity and geometry survive.
    first=deepcopy(p.settings['ocr_regions'][0])
    click_preview_item(ui, button)
    click_preview_item(ui, find(ui, 'drawNewRegionChoice'))
    assert ui.panel.property('drawingNewRegion') and ui.panel.property('roiSelecting')
    rect=frame_rect(ui)
    top=QPointF(rect.x()+rect.width()*.10,rect.y()+rect.height()*.04).toPoint()
    bottom=QPointF(rect.x()+rect.width()*.80,rect.y()+rect.height()*.20).toPoint()
    QTest.mousePress(ui.window,Qt.LeftButton,Qt.NoModifier,top)
    QTest.mouseMove(ui.window,bottom,20)
    QTest.mouseRelease(ui.window,Qt.LeftButton,Qt.NoModifier,bottom)
    delay(60)
    assert len(p.settings['ocr_regions'])==2 and p.settings['ocr_regions'][0]==first
    assert p.settings['ocr_regions'][1]['box']==pytest.approx([64,14,512,72],abs=2)
    assert not ui.panel.property('drawingNewRegion')
    assert ui.bridge.command('history.undo',{})
    assert ui.bridge.document.project.settings['ocr_regions']==[first]


def scene_rect(item):
    from PySide6.QtCore import QRectF
    return QRectF(item.mapToScene(QPointF()), item.size())


@pytest.mark.parametrize('window_size', [(330, 700), (480, 700), (900, 700), (1400, 500)])
@pytest.mark.parametrize('long_numbers', [False, True])
def test_frame_typography_and_balanced_transport_geometry(ui, window_size, long_numbers, monkeypatch):
    ui.window.resize(*window_size)
    install_cues(ui, resolution=(640, 360))
    present(ui, 1)
    ui.panel.setProperty('review', False)
    if long_numbers:
        # Exercise the widest supported int-range counters without allocating
        # billions of index entries or claiming this synthetic state was decoded.
        m = ui.bridge.media_controller
        m._state.update(displayedFrame=1234567890, totalFrames=2147483647,
                        positionSeconds=12345678, durationSeconds=87654321)
        m.changed.emit()
    delay(60)
    current = find(ui, 'currentFrameLabel')
    total = find(ui, 'totalFrameLabel')
    controls = find(ui, 'playerControls')
    summary = find(ui, 'transportSummary')
    frame = find(ui, 'frameSummary')
    buttons = find(ui, 'transportButtons')
    timing = find(ui, 'playbackTimeLabel')
    assert current.property('font').pixelSize() == 14
    assert total.property('font').pixelSize() == 11
    assert qml_enum(current, 'textFormat') == qml_enum(total, 'textFormat') == 0  # PlainText
    assert ''.join(c for c in current.property('text') if c.isdigit()) == ('1234567890' if long_numbers else '1')
    assert ''.join(c for c in total.property('text') if c.isdigit()) == ('2147483647' if long_numbers else '20')
    c, s, f, b, t = map(scene_rect, (controls, summary, frame, buttons, timing))
    assert abs(b.center().x() - c.center().x()) < .6
    # Dev3 moves both summaries inward and keeps them above the independently
    # centered transport. Stacking now concerns the side tools, not the summaries.
    assert f.left() == pytest.approx(s.left() + 10)
    assert t.right() == pytest.approx(s.right() - 10)
    assert f.bottom() + 1.5 <= b.top() and t.bottom() + 1.5 <= b.top()
    assert f.right() + 11.5 <= t.left()
    assert c.contains(b) and c.contains(f) and c.contains(t)
    assert scene_rect(current).right() <= f.right() + .1
    assert scene_rect(total).right() <= f.right() + .1
    transport_buttons = [item for item in buttons.childItems() if item.property('glyph') is not None]
    assert len(transport_buttons) == 9
    assert b.width() <= s.width()
    for button in transport_buttons:
        assert button.isVisible() and 24 <= button.width() <= 32 and button.height() == 31
        assert b.contains(scene_rect(button))
    # Phase two replaces the old flat translucent tint with a reflected gradient.
    # Keep every geometry/transport assertion above and verify rendered material.
    from PySide6.QtCore import QObject
    gradient=controls.findChild(QObject,'playerGlassGradient')
    assert gradient is not None
    stops=[child.property('color') for child in gradient.children() if child.property('position') is not None]
    assert len(stops)==3 and len({color.rgba() for color in stops})==3
    rendered=ui.window.grabWindow()
    samples=[]
    for y in (8,controls.height()/2):
        p=controls.mapToScene(QPointF(8,y))
        samples.append(rendered.pixelColor(round(p.x()*rendered.width()/ui.window.width()),round(p.y()*rendered.height()/ui.window.height())))
    assert all(min(color.red(),color.green(),color.blue())>170 for color in samples)
    assert samples[0].rgba()!=samples[1].rgba(), 'Glass lighting must paint distinct surface tones'

    shot(ui, f'preview-details-frames-{window_size[0]}x{window_size[1]}-' + ('long' if long_numbers else 'normal'))
    if not long_numbers:
        requests = []
        monkeypatch.setattr(ui.bridge.media_controller.worker, 'seek', requests.append)
        click_preview_item(ui, find(ui, 'transportForwardOne'))
        assert len(requests) == 1 and requests[0].frame == 2
        assert ui.bridge.media['requestedFrame'] == 2
        assert ui.bridge.media['displayedFrame'] == 1
        assert current.property('text') == '1'
        m = ui.bridge.media_controller
        m._present(requests[0], m._last_image, m.index.stamp(2), {})
        delay(30)
        assert current.property('text') == '2'


@pytest.mark.parametrize('glyph', ['eye', 'eye-off', 'roi-settings'])
@pytest.mark.parametrize('white', [False, True])
def test_selection_svg_variants_render_at_button_size(application, glyph, white):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter
    from PySide6.QtSvg import QSvgRenderer
    name = glyph + ('-white' if white else '') + '.svg'
    renderer = QSvgRenderer(str(ROOT / 'desktop_qt/assets/icons' / name))
    assert renderer.isValid()
    raster = QImage(15, 15, QImage.Format.Format_ARGB32_Premultiplied)
    raster.fill(Qt.GlobalColor.transparent)
    painter = QPainter(raster)
    renderer.render(painter)
    painter.end()
    ink = [raster.pixelColor(x, y) for y in range(15) for x in range(15)
           if raster.pixelColor(x, y).alpha() > 80]
    assert len(ink) > 20
    if white:
        assert all(color.red() == color.green() == color.blue() == 255 for color in ink)
    else:
        assert all(color.blue() > color.red() > color.green() for color in ink)


def test_overflow_can_be_moved_back_and_exported_without_resetting_font(ui):
    from lyricsync.export import layout_entries,placement_options,render_ass
    p=install_cues(ui,resolution=(640,360),entries=[(0.,.5,'短句')]);present(ui,1)
    p.settings.update(subtitle_position='custom',subtitle_height_percent=5,font_size=35)
    ui.bridge._changed();wait_until(lambda:ui.bridge._layout_revision==ui.bridge.document.revision)
    assert ui.bridge.media['subtitleLayoutError']
    assert ui.bridge.command('subtitle.move',{'dx':.1,'dy':.7})
    wait_until(lambda:ui.bridge._layout_revision==ui.bridge.document.revision)
    assert not ui.bridge.media['subtitleLayoutError']
    row=ui.bridge.media['subtitleRows'][0]
    assert row['fontSizeNormalized']==pytest.approx(35/360)
    assert row['xNormalized']==pytest.approx(.6)
    assert row['yNormalized']==pytest.approx(.75)
    ass=render_ass([(0.,.5,'短句')],(640,360),p.settings['font'],35,**placement_options(p))
    assert r'\pos(384,270)' in ass
    assert ui.bridge.command('history.undo',{})
    wait_until(lambda:ui.bridge._layout_revision==ui.bridge.document.revision)
    assert ui.bridge.media['subtitleRows'][0]['yNormalized']==pytest.approx(.05)


def test_style_entry_visible_at_narrow_width_and_routes_to_existing_dialog(ui,monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    ui.window.resize(360,700);delay(40)
    called=[];monkeypatch.setattr(ui.bridge,'command',lambda name,payload:called.append(name))
    button=find(ui,'subtitleStyleButton')
    assert button.isVisible() and button.width()>0
    QTest.mouseClick(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint())
    assert called==['settings.subtitleStyle']


def test_visual_overflow_notice_can_be_dismissed_without_changing_style(ui):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    p=install_cues(ui,resolution=(640,360));present(ui,1)
    p.settings.update(subtitle_position='custom',subtitle_height_percent=5,font_size=35)
    ui.bridge._changed();wait_until(lambda:ui.bridge._layout_revision==ui.bridge.document.revision);delay(30)
    before=ui.bridge.document.snapshot();button=find(ui,'dismissSubtitleLayoutWarning')
    assert button.isVisible()
    QTest.mouseClick(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint())
    assert not find(ui,'subtitleLayoutErrorBadge').isVisible()
    assert ui.bridge.document.snapshot()==before
    ui.bridge._changed();wait_until(lambda:ui.bridge._layout_revision==ui.bridge.document.revision);delay(30)
    assert find(ui,'subtitleLayoutErrorBadge').isVisible()


def test_ocr_region_svg_renders_at_actual_button_size(application):
    test_selection_svg_variants_render_at_button_size(application, 'ocr-region', False)
