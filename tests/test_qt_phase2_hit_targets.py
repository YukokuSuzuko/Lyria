"""Default four-panel controls must be painted and hit-testable, not cached offscreen."""
from PySide6.QtCore import QPointF
from test_qt_timeline_input import application, timeline_video, ui, find, point, drag
from tools.qt_event_loop_wait import delay


def bounds(item, viewport):
    pos = item.mapToItem(viewport, QPointF())
    return pos.x(), pos.y(), item.width(), item.height()


def test_default_scrollbar_contained_and_draggable(ui):
    app, bridge, window, timeline = ui
    assert (window.width(), window.height()) == (1400, 940)
    scroll = find(window, 'makeScroll')
    bar = find(window, 'timelineHorizontalScroll')
    x, y, width, height = bounds(bar, scroll)
    assert x >= 0 and y >= 0 and x + width <= scroll.width() and y + height <= scroll.height()
    assert timeline.mapToItem(scroll, QPointF(0, timeline.height())).y() <= scroll.height()
    before = bridge.document.snapshot()
    pos, size = bar.property('position'), bar.property('size')
    drag(window, point(bar, (pos + size / 2) * bar.width(), bar.height()/2), point(bar, bar.width()-1, bar.height()/2))
    assert timeline.property('viewLast') == timeline.property('contentLast')
    assert bridge.document.snapshot() == before


def test_candidate_reason_keeps_workspace_hit_regions_stable(ui):
    app, bridge, window, timeline = ui
    names = ['reviewPanel', 'reviewList', 'mainPreviewPanel', 'audioAnalysisPanel', 'frameTimeline', 'timelineHorizontalScroll']
    def geometry():
        return [bounds(find(window, name), window.contentItem()) for name in names]
    before = geometry()
    original = dict(bridge._state['alignmentPreview'])
    status = find(window, 'alignmentCandidateStatus')
    for reason in ['短说明', '需要核对的完整候选说明：' * 80, '']:
        bridge._state['alignmentPreview'] = dict(original, reason=reason)
        bridge.stateChanged.emit()
        delay(80)
        assert geometry() == before
        assert reason in status.property('text')
    # Dev6 intentionally hides AI controls, but hidden-state updates must not
    # grow or move the usable review/timeline hit regions.
    assert not find(window, 'alignmentMoreButton').isVisible()
    assert not find(window, 'candidatePreviewBanner').isVisible()
    assert geometry() == before
