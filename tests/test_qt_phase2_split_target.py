"""Phase-two split range, non-seeking target reveal, and focus regressions."""
from test_qt_timeline_commands import application, bridge
from test_qt_review_selection import ui, find, contained, settle, enter_lyric_editing
from PySide6.QtTest import QTest
from lyricsync.frames import FrameIndex
import pytest


def test_reported_start_boundary_lists_actual_inclusive_range(bridge):
    bridge.media_controller.index = FrameIndex([n / 30 for n in range(8400)], 280.)
    row = bridge._row('target')
    row.start, row.end = bridge.media_controller.index.interval(8158, 8311)
    bridge.document.project.settings['frame_markers'] = [8158]
    bridge._changed()
    before = bridge.document.snapshot()
    assert not bridge.command('row.split', {'id': 'target', 'frame': 8158, 'offsetUtf16': 2})
    assert 'F8159–F8311' in bridge.state['error']
    assert '句首' in bridge.state['error']
    assert bridge.document.snapshot() == before
    assert not bridge.history.undo_stack


def test_single_frame_has_no_split_range(bridge):
    row = bridge._row('target'); row.end = .4
    bridge.document.project.settings['frame_markers'] = [3]
    bridge._changed()
    assert not bridge.command('row.split', {'id': 'target', 'frame': 3, 'offsetUtf16': 2})
    assert '时长不足 2 帧' in bridge.state['error']


def test_marker_reveals_target_without_selection_transport_or_history(ui):
    settle(ui.app, lambda: contained(ui, 'r40'))
    revision = ui.bridge.document.revision
    assert ui.bridge.command('marker.clear', {})
    assert ui.bridge.command('marker.add', {'frame': 935})
    panel = find(ui.window, 'reviewPanel')
    settle(ui.app, lambda: contained(ui, 'r93'))
    assert panel.property('splitFrame') == 935
    assert find(ui.window, 'cueRow_r93').property('splitEligible')
    assert ui.bridge.selected == 'r40'
    assert ui.bridge.document.revision == revision + 2
    assert not ui.transport
    before = ui.view.property('contentY')
    for _ in range(5):
        ui.bridge.mediaChanged.emit(); ui.bridge._refresh(False)
    QTest.qWait(250)
    assert ui.view.property('contentY') == before


def test_marker_does_not_steal_edit_focus_or_scroll(ui):
    settle(ui.app, lambda: contained(ui, 'r40'))
    editor = enter_lyric_editing(ui)
    before = ui.view.property('contentY')
    assert ui.bridge.command('marker.clear', {})
    assert ui.bridge.command('marker.add', {'frame': 935})
    QTest.qWait(250)
    assert editor.hasActiveFocus()
    assert ui.bridge.selected == 'r40'
    assert ui.view.property('contentY') == before
    assert not ui.transport


def test_shared_target_contract_for_boundary_overlap_lock_and_nearest(application):
    import json
    from pathlib import Path
    from PySide6.QtQml import QJSEngine
    engine = QJSEngine()
    source = (Path(__file__).resolve().parents[1] / 'desktop_qt/qml/SplitTarget.js').read_text('utf8')
    assert not engine.evaluate(source.replace('.pragma library', '')).isError()
    def call(name, *args):
        result = engine.evaluate('JSON.stringify(' + name + '(' + ','.join(json.dumps(a) for a in args) + '))')
        assert not result.isError(), result.toString()
        return json.loads(result.toString())
    cue = dict(firstFrame=8158, lastFrame=8311, locked=False)
    assert not call('eligible', cue, 8158)
    assert call('eligible', cue, 8159)
    assert call('eligible', cue, 8311)
    assert not call('eligible', cue, 8312)
    assert not call('eligible', dict(cue, locked=True), 8200)
    assert call('eligible', dict(cue, firstFrame=8100), 8158)
    assert call('nearest', [{'frame': 100}, {'frame': 120}], 110) == 100
    assert call('nearest', [], 8158) == 8158


@pytest.mark.parametrize('frame', [8159, 8311])
def test_inclusive_split_endpoints_retain_nonempty_pieces(bridge, frame):
    index = FrameIndex([n / 30 for n in range(8400)], 280.)
    bridge.media_controller.index = index
    row = bridge._row('target'); row.start, row.end = index.interval(8158, 8311)
    bridge.document.project.settings['frame_markers'] = [frame]
    bridge._changed()
    assert bridge.command('row.split', {'id': 'target', 'frame': frame, 'offsetUtf16': 2})
    parts = [m for m in bridge.document.project.mappings if m.id != 'overlap']
    assert [(index.first(m.start), index.last(m.end)) for m in parts] == [(8158, frame - 1), (frame, 8311)]
    assert len(bridge.history.undo_stack) == 1


def test_long_target_hint_never_changes_workspace_geometry(ui):
    settle(ui.app, lambda: contained(ui, 'r40'))
    assert find(ui.window, 'splitTargetHint') is None
    hint = find(ui.window, 'reviewOperationsButton')
    panel = find(ui.window, 'reviewPanel')
    before = (hint.height(), panel.property('minimumPanelHeight'), ui.view.height())
    hint.setProperty('tip', 'F8158 F8159–F8311 ' * 50)
    QTest.qWait(250)
    assert (hint.height(), panel.property('minimumPanelHeight'), ui.view.height()) == before
