import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts
import Lyria 1.0
import "TimelineSnap.js" as Snap
import "SplitTarget.js" as SplitTarget

Panel {
    id: timeline
    required property var controller
    required property var state
    required property var media
    clip: true
    property bool collapsed: false
    property bool lyricVisible: true
    property bool showTimecodes: false
    function formatFrameAxis(frame) {
        var f = Math.round(frame)
        if (!timeline.showTimecodes) return String(f)
        var dur = Number(timelineAudio.duration || timeline.media.duration || 0)
        var tot = Number(timeline.total || 0)
        var sec = 0
        if (timelineAudio && dur > 0 && tot > 0) sec = timelineAudio.secondsAt(f)
        else sec = Math.max(0, (f - 1) / Math.max(1, Number(timeline.media.fps || 24)))
        sec = Math.max(0, sec)
        return Math.floor(sec / 60) + ":" + (sec % 60).toFixed(1).padStart(4, "0")
    }
    function formatFrameMark(frame) {
        return timeline.showTimecodes ? timeline.formatFrameAxis(frame) : ("F" + Math.round(frame))
    }
    onLyricVisibleChanged: { cancelInteraction(); if (!lyricVisible) { collapsed = false; tool = "select"; createMode = false } }
    // Lyric editing stays light; only the independent audio plot owns a dark canvas.
    // Keep the specified purple selection and amber pending state independent.
    readonly property color lyricSplitBorder: "#397A63"
    readonly property color lyricPendingAccent: Theme.pendingTextAccent
    readonly property color lyricPlayhead: "#B85A82"
    readonly property color lyricStartMark: "#39758F"
    readonly property bool readOnly: !!state.candidateReadOnly
    onReadOnlyChanged: if (readOnly) { cancelInteraction(); createMode = false; tool = "select"; closePopups() }
    readonly property int splitFrame: SplitTarget.nearest(state.markers || [], media.displayedFrame || 0)
    property bool createMode: false
    property string tool: "select"
    property int razorHoverFrame: 0
    property string razorHoverId: ""
    property bool razorHoverValid: false
    property var pendingEntranceIds: ({})
    property bool componentReady: false
    property string pendingCreatedId: ""
    Connections {
        target: timeline.controller
        function onCueCreated(identity) { timeline.pendingCreatedId = identity; timeline.followedId = identity; Qt.callLater(timeline.revealCreated) }
        function onRazorSplitCreated(left, right) { var ids = ({}), now = Date.now(); ids[left] = now; ids[right] = now; timeline.pendingEntranceIds = ids }
    }
    onToolChanged: { razorHoverFrame = 0; razorHoverId = "" }
    property int rowMode: 0
    // Session choice survives page switches because this item stays alive.
    // Default on; never re-enable from onVisibleChanged or navigation handlers.
    property bool followEnabled: true
    readonly property string projectIdentity: (state.project || {}).id || ""
    readonly property int sourceIdentity: media.sourceEpoch || 0
    onSourceIdentityChanged: { workspaceAudioHeight=-1; followEnabled=true; manualView=false; followedId="" }
    onProjectIdentityChanged: { workspaceAudioHeight=-1; followEnabled=true; manualView=false; followedId=""; if(componentReady)Qt.callLater(function(){fit();follow()}) }
    property bool pageFollowSuspended: false
    property bool rowFollowSuspended: false
    readonly property int playheadFrame: media.playheadFrame === undefined ? (media.scrubbing ? media.requestedFrame : media.displayedFrame) || 0 : media.playheadFrame
    readonly property real cursorFrame: playing && controller && controller.playbackCursor !== undefined ? controller.playbackCursor : playheadFrame
    readonly property bool playing: !!media.playing
    onPlayheadFrameChanged: {
        if (componentReady && !interacting && !horizontalScroll.pressed) {
            pageFollowSuspended = false
            if (playing && followEnabled && !verticalScroll.pressed && rowFollowSuspended) {
                rowFollowSuspended = false
                positionCurrent()
            }
        }
        Qt.callLater(pagePlayhead)
    }
    onPlayingChanged: if (playing) { pageFollowSuspended = false; rowFollowSuspended = false; Qt.callLater(pagePlayhead); Qt.callLater(pageRows) }
    onFollowEnabledChanged: { pageFollowSuspended = false; rowFollowSuspended = false; Qt.callLater(pagePlayhead) }
    property bool windowActive: true
    property bool keyboardEditing: false
    property string keyboardId: ""
    property string keyboardEdge: ""
    property int viewFirst: 1
    property int viewLast: 301
    property bool manualView: false
    property string followedId: ""
    property bool editing: false
    property string editId: ""
    property string editEdge: ""
    property int editFrame: 1
    property bool snapped: false
    property bool snapEnabled: true
    property int snapFrame: 0
    property string snapLabel: ""
    property var snapTargets: []
    property var dragCue: ({})
    property real lastDragX: 0
    property int lastDragModifiers: 0
    property int editBoundary: 1
    property real editGrabOffset: 0
    onSnapEnabledChanged: {
        if (movingClip) adjustMove(dragCue, lastDragX, lastDragModifiers)
        else if (editing) adjust(dragCue, lastDragX, lastDragModifiers)
        else clearSnap()
    }
    property bool movingClip: false
    property string moveId: ""
    property int moveFirst: 0
    property int moveLast: 0
    property int moveDelta: 0
    property real moveOriginX: 0
    property bool panning: false
    property real panOriginX: 0
    property real panOriginY: 0
    property int panOriginFirst: 1
    property int panOriginSpan: 1
    property real panOriginTop: 0
    property real panWidth: 1
    property bool panRows: false
    property string savedTopId: ""
    property real savedTopOffset: 0
    property real savedContentY: 0
    readonly property var selection: state.selection || ({})
    readonly property var current: selection.current || ({})
    readonly property var previous: selection.previous || ({})
    readonly property var previousTimed: selection.previousTimed || selection.previous || ({})
    readonly property var next: selection.next || ({})
    readonly property var rowModel: controller ? controller.timelineRows : null
    readonly property int rowCount: rowModel ? rowModel.count : 0
    readonly property int selectedIndex: state.selectedTimelineIndex === undefined ? -1 : state.selectedTimelineIndex
    readonly property int span: Math.max(1, viewLast - viewFirst)
    readonly property int total: media.totalFrames || 0
    readonly property int contentPadding: Math.min(90, Math.max(12, Math.round(((state.timelineBounds || {}).lastFrame - (state.timelineBounds || {}).firstFrame || 0) * 0.03)))
    readonly property int contentFirst: 1
    readonly property int contentLast: total > 0 ? total + 1 : Math.max(2, (state.timelineBounds || {}).lastFrame + 1 || 302)
    readonly property real labelWidth: 76
    readonly property real plotWidth: lyricVisible ? lanes.width : Math.max(1, analysisStrip.width - labelWidth - 12)
    readonly property int rulerHeight: 34
    readonly property int rowHeight: 30
    readonly property int shownRows: rowMode || 5
    readonly property bool interacting: editing || movingClip || panning || rulerMouse.scrubbing || rulerMouse.creating || audioNavigation.scrubbing

    // Internal audio resizing must not change the outer SplitView minimum/preference.
    readonly property real minimumExpandedHeight: 55 + (foldHeader.stacked ? 31 : 0) + (audioVisible ? 40 + audioToolbarSpace : 0) + (lyricVisible ? 90 : 0)
    readonly property bool audioVisible: (state.workspaceLayout || {}).audioVisible !== false
    readonly property real savedAudioHeight: Number((state.workspaceLayout || {}).audioHeight || 160)
    property real resizingAudioHeight: -1
    // Outer workspace drags redistribute the lyric pane, not the audio plot.
    // This is a session-only hold, never an explicit persisted audio preference.
    property real workspaceAudioHeight: -1
    function holdAudioHeightForWorkspaceResize() {
        if (audioVisible && audioExpanded && !(state.workspaceLayout || {}).audioHeightExplicit && audioHeight > 36)
            workspaceAudioHeight = audioHeight
    }
    onSavedAudioHeightChanged: workspaceAudioHeight = -1
    readonly property real audioBudgetHeight: folding || collapsed || !visible ? Math.max(0, expandedLayoutHeight-expandedHeaderHeight-9) : foldViewport.height
    readonly property real minimumAudioHeight: audioBudgetHeight < 180 ? 8 : 36
    readonly property real maximumAudioHeight: Math.max(minimumAudioHeight, Math.min(600, audioBudgetHeight - (lyricVisible ? 136 : 30)))
    readonly property real audioHeight: Math.max(minimumAudioHeight, Math.min(maximumAudioHeight, resizingAudioHeight >= 0 ? resizingAudioHeight : (state.workspaceLayout || {}).audioHeightExplicit ? savedAudioHeight : workspaceAudioHeight >= 0 ? workspaceAudioHeight : Math.min(savedAudioHeight, Math.max(36, audioBudgetHeight - (lyricVisible ? 222 : 30)))))
    readonly property bool audioExpanded: timelineAudio.status === "ready" || timelineAudio.status === "loading"
    // Do not reserve a full empty plot for media with no audio or an error.
    // Keep a truthful compact status, and retain the user's saved plot height.
    readonly property real audioDisplayHeight: audioExpanded ? audioHeight : Math.min(44, Math.max(24, audioBudgetHeight-100))
    readonly property int audioToolbarSpace: lyricVisible ? 0 : 30
    readonly property real analysisHeight: audioVisible ? audioDisplayHeight + audioToolbarSpace + (audioExpanded ? 12 : 3) : 0
    readonly property real expandedHeight: (audioVisible ? (audioExpanded ? 172 : 47) + audioToolbarSpace : 0) + (lyricVisible ? 138 + Math.max(29, foldHeader.implicitHeight) + shownRows * rowHeight : 68)
    property real expandedLayoutHeight: expandedHeight
    property real expandedHeaderHeight: 29
    // Hidden SplitView children can briefly inherit the entire workspace height.
    // Never cache that as the expanded row viewport during initial reveal.
    onHeightChanged: if (visible && !collapsed && !folding) expandedLayoutHeight = Math.round(height)
    readonly property bool folding: foldOpacity.running || foldArrow.running
    implicitHeight: foldHeight.value
    MotionNumber { id: foldHeight; targetValue: timeline.collapsed ? 55 : timeline.expandedHeight; duration: 200 }
    MotionNumber { id: foldOpacity; targetValue: timeline.collapsed ? 0 : 1; duration: 200 }
    MotionNumber { id: foldArrow; targetValue: timeline.collapsed ? 180 : 0; duration: 180 }
    onCollapsedChanged: if (collapsed) {
        expandedLayoutHeight = Math.round(height)
        // A fading, clipped surface must give up its pointer/key grabs at once.
        cancelInteraction()
        closePopups()
        if (foldButton) foldButton.forceActiveFocus()
    }
    onWindowActiveChanged: if (!windowActive) cancelInteraction()
    onActiveFocusChanged: if (!activeFocus) endPan()
    onWidthChanged: endPan()
    onVisibleChanged: if (!visible) { cancelInteraction(); closePopups() } else Qt.callLater(revealCreated)
    onInteractingChanged: if (!interacting) { Qt.callLater(revealCreated); Qt.callLater(pagePlayhead) }
    onFoldingChanged: if (!folding && !collapsed) { expandedHeaderHeight = foldHeader.height; Qt.callLater(revealCreated) }
    onKeyboardEditingChanged: if (!keyboardEditing) Qt.callLater(revealCreated)
    Connections {
        target: timeline.Window.window
        // A height-only window resize can move this panel without changing
        // its fixed three/five-row height. End the old pointer origin too.
        function onWidthChanged() { timeline.endPan() }
        function onHeightChanged() { timeline.endPan() }
    }
    onRowModeChanged: Qt.callLater(function() { if (followEnabled) positionCurrent(); else clampRows(tracks.contentY) })
    onContentFirstChanged: Qt.callLater(clampView)
    onContentLastChanged: Qt.callLater(clampView)
    function cmd(name, payload) { if (controller) return controller.command(name, payload || {}) }
    function selectGroup(cue, mods) {
        forceActiveFocus()
        cmd("review.select", {id: cue.cueId, timeline: true, ctrl: !!(mods & Qt.ControlModifier), shift: !!(mods & Qt.ShiftModifier)})
    }
    function openRowMenu(cue, member) {
        if (!member) selectGroup(cue, 0)
        timelineContext.rowIdentity = cue.cueId
        timelineContext.popup()
    }
    function selectCue(cue) { forceActiveFocus(); followedId = cue.cueId; cmd("timeline.select", {id: cue.cueId}) }
    function rowAt(index) { return rowModel && index >= 0 && index < rowCount ? rowModel.get(index) : ({}) }
    function selectTool(value) { if (readOnly && value === "razor") return; cancelInteraction(); tool = value; createMode = false; forceActiveFocus() }
    function razor(cue, x) {
        if (readOnly) return
        var preserve = manualView || !followEnabled
        var first = viewFirst, last = viewLast, top = tracks.contentY
        cmd("timeline.razor", {id: cue.cueId, frame: frameFor(x)})
        followedId = current.cueId || ""
        if (preserve) { viewFirst = first; viewLast = last; clampRows(top) }
    }
    function previewRazor(cue, x) {
        if (tool !== "razor") return
        razorHoverId = cue.cueId; razorHoverFrame = frameFor(x)
        razorHoverValid = SplitTarget.eligible(cue, razorHoverFrame)
    }
    function endKeyboardEdit() {
        if (keyboardEditing) { cmd("history.end"); cmd("editor.activity", {id: "timeline:" + keyboardId + ":" + keyboardEdge, active: false, composing: false}); keyboardEditing = false }
    }
    function cancelEdgeEdit() {
        if (editing) cmd("editor.activity", {id: "edge:" + editId, active: false, composing: false})
        editing = false; clearSnap()
    }
    function cancelMove() {
        if (movingClip) cmd("editor.activity", {id: "move:" + moveId, active: false, composing: false})
        movingClip = false; moveDelta = 0; clearSnap()
    }
    function closePopups() {
        // Visibility can change before the child controls finish creation.
        if (timelineAudio) timelineAudio.closeMenu()
        if (createMenu) createMenu.close()
        if (allocationPopup) allocationPopup.cancelAllocation()
    }
    function cancelInteraction() { if (audioNavigation) audioNavigation.finish(); if (rulerMouse) rulerMouse.finish(); endPan(); endKeyboardEdit(); cancelEdgeEdit(); cancelMove() }
    function xFor(frame) { return (frame - viewFirst) / span * plotWidth }
    function frameFor(x) { return Math.max(1, Math.min(total || 2147483647, Math.round(viewFirst + x / Math.max(1, plotWidth) * span))) }
    // The scrollbar, wheel and hand share the real video frame extent. Empty
    // lead-in/tail remain reachable; no drag can overscroll the final frame.
    function view(a, b) {
        var size = Math.min(contentLast - contentFirst, Math.max(10, Math.round(b - a)))
        viewFirst = Math.max(contentFirst, Math.min(contentLast - size, Math.round(a)))
        viewLast = viewFirst + size
    }
    function clampView() { view(viewFirst, viewLast) }
    function clampRows(top) {
        tracks.contentY = Math.max(0, Math.min(Math.max(0, rowCount * rowHeight - tracks.height), top))
    }
    function positionIndex(index) {
        // Uniform row geometry is exact even before ListView has estimated the
        // heights of new delegates after a reset or a panel-height transition.
        clampRows(index * rowHeight - (tracks.height - rowHeight) / 2)
    }
    function revealCreated() {
        if (!pendingCreatedId || !visible || collapsed || interacting || keyboardEditing) return
        var identity = pendingCreatedId
        if (current.cueId !== identity) { pendingCreatedId = ""; return }
        var index = -1
        for (var i = 0; i < rowCount; ++i) if (rowAt(i).cueId === identity) { index = i; break }
        if (index < 0) return
        tracks.forceLayout()
        var top = index * rowHeight
        if (top < tracks.contentY) clampRows(top)
        else if (top + rowHeight > tracks.contentY + tracks.height) clampRows(top + rowHeight - tracks.height)
        var cue = rowAt(index)
        // Retain zoom and the current horizontal view whenever any part is visible.
        if (cue.firstFrame > 0 && (cue.lastFrame < viewFirst || cue.firstFrame >= viewLast)) {
            var size = span
            view(cue.firstFrame - Math.min(12, Math.floor(size / 10)), cue.firstFrame + size - Math.min(12, Math.floor(size / 10)))
        }
        followedId = identity; pendingCreatedId = ""
    }
    function positionCurrent() {
        if (selectedIndex >= 0 && !interacting && !rowFollowSuspended) positionIndex(selectedIndex)
    }
    function fit() {
        if (!current.firstFrame) { view(contentFirst, Math.min(contentLast, contentFirst + 300)); return }
        var radius = rowMode === 3 ? 1 : 2
        var a = current.firstFrame, b = current.lastFrame + 1
        for (var i = Math.max(0, selectedIndex - radius); i <= Math.min(rowCount - 1, selectedIndex + radius); i++) {
            var cue = rowAt(i)
            if (cue.firstFrame > 0) { a = Math.min(a, cue.firstFrame); b = Math.max(b, cue.lastFrame + 1) }
        }
        // A very remote neighbour must not make the current lyric unreadable.
        var currentSpan = current.lastFrame - current.firstFrame + 1
        if (b - a > Math.max(300, currentSpan * 8)) { a = current.firstFrame; b = current.lastFrame + 1 }
        var padding = Math.max(12, Math.round((b - a) * 0.1))
        view(a - padding, b + padding); manualView = false
        positionCurrent()
    }
    function follow() {
        if (interacting || (current.cueId || "") === followedId) return
        followedId = current.cueId || ""
        if (!followEnabled) { pageRows(); return }
        positionCurrent()
        if (playing) { pagePlayhead(); return }
        if (!pageFollowSuspended && current.firstFrame && (current.firstFrame < viewFirst || current.lastFrame >= viewLast)) {
            // A long/overlapping cue must not move the playhead off screen.
            var center = current.lastFrame - current.firstFrame >= span && playheadFrame >= current.firstFrame && playheadFrame <= current.lastFrame ? playheadFrame : (current.firstFrame + current.lastFrame + 1) / 2
            view(center - span / 2, center + span / 2)
        }
    }
    function pageRows() {
        if (followEnabled || !playing || interacting || keyboardEditing || selectedIndex < 0) return
        var top = selectedIndex * rowHeight
        if (rowFollowSuspended) {
            if (top >= tracks.contentY && top + rowHeight <= tracks.contentY + tracks.height) rowFollowSuspended = false
            return
        }
        if (top < tracks.contentY || top + rowHeight >= tracks.contentY + tracks.height - 1) {
            var preceding = Math.min(1, Math.max(0, Math.floor(tracks.height / rowHeight) - 2))
            clampRows((selectedIndex - preceding) * rowHeight)
        }
    }
    function zoom(factor, anchor) {
        var ratio = anchor === undefined ? 0.5 : Math.max(0, Math.min(1, anchor / Math.max(1, plotWidth)))
        var frame = viewFirst + ratio * span, size = span * factor
        view(frame - ratio * size, frame + (1 - ratio) * size); manualView = true
    }
    function revealRange() {
        if (!rowCount) return
        var top = Math.max(0, Math.floor(tracks.contentY / rowHeight))
        var bottom = Math.min(rowCount - 1, Math.ceil((tracks.contentY + tracks.height) / rowHeight) - 1)
        for (var i = top; i <= bottom; i++) {
            var visibleCue = rowAt(i)
            if (visibleCue.firstFrame < viewLast && visibleCue.lastFrame >= viewFirst) return
        }
        var best = controller ? controller.timelineIndexAtFrame(Math.round((viewFirst + viewLast) / 2)) : -1
        if (best >= 0) positionIndex(best)
    }
    function panTo(first, preserveRows) {
        manualView = true
        pageFollowSuspended = true
        view(first, first + span)
        // Wheel/scrollbar navigation may reveal a nearby lyric. A hand drag
        // owns its row position, even when its horizontal window is empty.
        if (!preserveRows) revealRange()
    }
    function beginPan(x, y, overRows) {
        forceActiveFocus()
        panOriginX = x; panOriginY = y; panOriginFirst = viewFirst
        panOriginSpan = span; panOriginTop = tracks.contentY; panWidth = Math.max(1, plotWidth)
        panRows = !!overRows; manualView = true; panning = true
        if (panRows) rowFollowSuspended = true
    }
    function dragPan(x, y) {
        if (!panning) return
        panTo(panOriginFirst - (x - panOriginX) / panWidth * panOriginSpan, true)
        if (panRows) clampRows(panOriginTop - (y - panOriginY))
    }
    function endPan() { panning = false; panRows = false }
    function pagePlayhead() {
        // During playback the playhead owns paging. When paused, existing cues
        // retain selection/manual-view priority; only an empty axis follows seeks.
        if (!followEnabled || interacting || horizontalScroll.pressed || pageFollowSuspended || keyboardEditing || !playheadFrame || (!playing && rowCount > 0)) return
        if (playheadFrame >= viewFirst && playheadFrame < viewLast) return
        if (!playing) { view(playheadFrame - span / 2, playheadFrame + span / 2); return }
        var size = span, pages = Math.floor((playheadFrame - viewFirst) / size)
        var first = viewFirst + pages * size
        view(first, first + size)
    }
    function wheelInput(wheel, overRows, anchorX) {
        if (wheel.modifiers & Qt.AltModifier) {
            // Windows can route Alt+wheel to the horizontal axis. Touchpads
            // may supply only pixel deltas; a zero/end event is not zoom-out.
            var angle = wheel.angleDelta.y || wheel.angleDelta.x
            var pixel = wheel.pixelDelta.y || wheel.pixelDelta.x
            var steps = angle ? angle / 120 : pixel / 40
            if (steps) zoom(Math.pow(1.25, -Math.max(-3, Math.min(3, steps))), anchorX === undefined ? wheel.x : anchorX)
        }
        else if (wheel.modifiers & Qt.ControlModifier) {
            var vertical = wheel.angleDelta.y || wheel.angleDelta.x
            var verticalSteps = vertical ? vertical / 120 : (wheel.pixelDelta.y || wheel.pixelDelta.x) / 40
            clampRows(tracks.contentY - verticalSteps * rowHeight * 2)
            rowFollowSuspended = true
        } else {
            var horizontal = wheel.angleDelta.x || wheel.angleDelta.y
            var horizontalSteps = horizontal ? horizontal / 120 : (wheel.pixelDelta.x || wheel.pixelDelta.y) / 40
            if (horizontalSteps) panTo(viewFirst - span * horizontalSteps * 0.12, true)
        }
        wheel.accepted = true
    }
    function first(cue) { return movingClip && moveId === cue.cueId ? moveFirst + moveDelta : editing && editId === cue.cueId && editEdge === "first" ? editFrame : cue.firstFrame || 0 }
    function last(cue) { return movingClip && moveId === cue.cueId ? moveLast + moveDelta : editing && editId === cue.cueId && editEdge === "last" ? editFrame : cue.lastFrame || 0 }
    function clearSnap() { snapped = false; snapFrame = 0; snapLabel = "" }
    function captureSnapTargets(cue) {
        // Capture before selection changes. Selected peers are never magnetic
        // targets for this gesture; moving one cue does not silently move a group.
        dragCue = cue
        var selected = state.multiSelectedIds || []
        var excluded = selected.indexOf(cue.cueId) >= 0 ? selected.slice() : []
        if (excluded.indexOf(cue.cueId) < 0) excluded.push(cue.cueId)
        var rows = []
        for (var i = 0; i < rowCount; ++i) rows.push(rowAt(i))
        snapTargets = Snap.targets(rows, excluded, total, state.markers || [])
        clearSnap()
    }
    function snapDelta(edges, raw, minimum, maximum, modifiers) {
        var result = Snap.resolve(edges, raw, minimum, maximum, snapTargets,
                                  media.ready ? playheadFrame : 0, plotWidth / span,
                                  snapEnabled && !(modifiers & Qt.AltModifier))
        snapped = result.snapped; snapFrame = result.frame
        snapLabel = !snapped ? "" : result.kind === "playhead" ? "吸附播放头 F" + result.frame :
                    result.kind === "marker" ? "吸附标记点 F" + result.frame : "吸附第 " + result.number + " 句" + (result.kind === "start" ? "入点" : "出点")
        return result.delta
    }
    function beginMove(cue, x) {
        if (readOnly) { selectCue(cue); return }
        captureSnapTargets(cue); lastDragX = x; lastDragModifiers = 0
        movingClip = true; moveId = cue.cueId; moveFirst = cue.firstFrame; moveLast = cue.lastFrame; moveDelta = 0; moveOriginX = x
        forceActiveFocus(); cmd("editor.activity", {id: "move:" + moveId, active: true, composing: false})
        cmd("timeline.select", {id: cue.cueId}); followedId = cue.cueId
    }
    function adjustMove(cue, x, modifiers) {
        if (!movingClip || cue.locked) return
        lastDragX = x; lastDragModifiers = modifiers || 0
        moveDelta = snapDelta([moveFirst, moveLast + 1], (x - moveOriginX) / Math.max(1, plotWidth) * span,
                              1 - moveFirst, total - moveLast, lastDragModifiers)
    }
    function commitMove() {
        if (movingClip && moveDelta !== 0) cmd("timeline.move", {id: moveId, delta: moveDelta})
        cancelMove()
    }
    function begin(cue, edge, x) {
        if (readOnly) return
        // Publish a complete preview atomically. An old/default last-frame
        // value could otherwise unload the clip and destroy its mouse grab.
        captureSnapTargets(cue)
        editId = cue.cueId; editEdge = edge; editFrame = edge === "first" ? cue.firstFrame : cue.lastFrame
        editBoundary = edge === "first" ? cue.firstFrame : cue.lastFrame + 1
        // Preserve where inside the handle the pointer was pressed. Otherwise
        // right-edge dragging introduces a one-frame jump before any movement.
        lastDragX = typeof x === "number" ? x : xFor(editBoundary); lastDragModifiers = 0
        editGrabOffset = viewFirst + lastDragX / Math.max(1, plotWidth) * span - editBoundary
        editing = true
        cmd("editor.activity", {id: "edge:" + editId, active: true, composing: false})
    }
    function adjust(cue, x, modifiers) {
        if (!editing || cue.locked) return
        lastDragX = x; lastDragModifiers = modifiers || 0
        var raw = viewFirst + x / Math.max(1, plotWidth) * span - editGrabOffset - editBoundary
        var minimum = (editEdge === "first" ? 1 : cue.firstFrame + 1) - editBoundary
        var maximum = (editEdge === "first" ? cue.lastFrame : total + 1) - editBoundary
        var target = editBoundary + snapDelta([editBoundary], raw, minimum, maximum, lastDragModifiers)
        editFrame = target - (editEdge === "last" ? 1 : 0)
    }
    function commit() { if (editing) cmd("row.setBoundary", {id: editId, edge: editEdge, frame: editFrame}); cancelEdgeEdit() }
    onCurrentChanged: { if (keyboardEditing && current.cueId !== keyboardId) endKeyboardEdit(); if (editing && current.cueId !== editId) cancelEdgeEdit(); Qt.callLater(follow) }
    // Initial lazy evaluation of total can originate inside ScrollBar.size.
    // Fitting there would change span while that same binding is evaluating.
    onTotalChanged: if (componentReady && !manualView) Qt.callLater(function() { if (!manualView) fit() })
    Component.onCompleted: { componentReady = true; Qt.callLater(function() { fit(); follow() }) }
    Keys.onEscapePressed: cancelInteraction()
    Connections {
        target: timeline.rowModel
        function onModelAboutToBeReset() {
            timeline.endPan()
            timeline.savedContentY = tracks.contentY
            var index = Math.max(0, Math.floor(tracks.contentY / timeline.rowHeight))
            timeline.savedTopId = timeline.rowAt(index).cueId || ""
            timeline.savedTopOffset = tracks.contentY - index * timeline.rowHeight
        }
        function onProjectionReset(preserveView) {
            // Emitted after the model and count notifications have completed.
            // Restore synchronously before the next render, never via callLater.
            tracks.forceLayout()
            if (!preserveView) { if (timeline.followEnabled) Qt.callLater(timeline.positionCurrent); else timeline.clampRows(0); return }
            if (timeline.pendingCreatedId) { timeline.revealCreated(); return }
            var anchor = !timeline.manualView && timeline.followEnabled ? timeline.current.cueId : timeline.savedTopId
            for (var i = 0; i < timeline.rowCount; i++) if (timeline.rowAt(i).cueId === anchor) {
                if (!timeline.manualView && timeline.followEnabled) timeline.positionIndex(i)
                else timeline.clampRows(i * timeline.rowHeight + timeline.savedTopOffset)
                return
            }
            // A whole-song AI candidate can replace every cue ID. Preserve
            // the pre-reset position, not the ListView's transient zero.
            timeline.clampRows(timeline.savedContentY)
        }

    }
    Item {
        id: foldHeader
        visible: timeline.lyricVisible
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        // Reserve the optional allocation action, so selection cannot move the audio group.
        readonly property real leftWidth: timelineTitle.implicitWidth + 10 + timelineToolGroup.implicitWidth
                                          + (allocateText.visible || timeline.readOnly ? 0 : allocateText.implicitWidth + timelineToolGroup.spacing)
        readonly property bool stacked: visible && !timeline.collapsed && width < leftWidth + 16 + rightTools.implicitWidth
        anchors.margins: 13; anchors.topMargin: 6; implicitHeight: stacked ? 60 : 29; height: visible ? implicitHeight : 0; objectName: "timelineHeader"
        onHeightChanged: if (!timeline.collapsed) timeline.expandedHeaderHeight = height
        RowLayout {
            id: timelineTitle; height: 29; spacing: 6
            Image { objectName: "timelineSequenceIcon"; source: "../assets/icons/sequence.svg"; Layout.preferredWidth: 18; Layout.preferredHeight: 16; sourceSize.width: 54; sourceSize.height: 46 }
            Label { textFormat: Text.PlainText; text: "时间轴"; color: Theme.ink; font.pixelSize: 13; font.weight: Font.DemiBold }
        }
        Row {
            id: timelineToolGroup; objectName: "timelineToolGroup"
            anchors.left: timelineTitle.right; anchors.leftMargin: 10; y: 1
            visible: !timeline.collapsed; spacing: 5
            ActionButton { objectName: "timelineSelectTool"; glyph: "tool-select"; text: ""; compact: true; checkable: true; checked: timeline.tool === "select" && !timeline.createMode; onClicked: timeline.selectTool("select"); tip: "选择（V）：选中、移动片段或拖动边缘修轴" }
            ActionButton { objectName: "timelineRazorTool"; visible: !timeline.readOnly; glyph: "tool-razor"; text: ""; compact: true; checkable: true; checked: timeline.tool === "razor"; onClicked: timeline.selectTool("razor"); tip: "剃刀（C）：点击片段切分时间轴，文字可稍后分配" }
            ActionButton { objectName: "timelineHandTool"; glyph: "tool-hand"; text: ""; compact: true; checkable: true; checked: timeline.tool === "hand"; onClicked: timeline.selectTool("hand"); tip: "手形（H）：拖动平移时间轴视野" }
            ActionButton { objectName: "timelineSnapToggle"; visible: !timeline.readOnly; glyph: "magnet"; text: ""; compact: true; checkable: true; checked: timeline.snapEnabled; onClicked: timeline.snapEnabled = checked; tip: "移动或修边时自动吸附播放头、标记点与邻句出入点（按住 Alt 临时关闭）" }
            ActionButton { objectName: "timelineCreateMenuButton"; visible: !timeline.readOnly; glyph: "subtitle-add"; text: ""; tip: "新建空白句"; compact: true; enabled: !!timeline.media.ready; onClicked: createMenu.popup()
                Menu { id: createMenu; objectName: "timelineCreateMenu"
                    property var rowAtOpen: ({})
                    onAboutToShow: rowAtOpen = timeline.current
                    MenuItem { objectName: "timelineCreateAtPlayheadMenuItem"; text: createMenu.rowAtOpen.untimed ? "在播放头处为本句打轴（3 秒）" : "在播放头处新建空句（3 秒）"; enabled: !createMenu.rowAtOpen.untimed || !createMenu.rowAtOpen.locked
                        onTriggered: createMenu.rowAtOpen.untimed ? timeline.cmd("timeline.assignAtPlayhead", {id: createMenu.rowAtOpen.cueId}) : timeline.cmd("timeline.createAtPlayhead") }
                    MenuItem { text: "另建空句（3 秒）"; visible: !!createMenu.rowAtOpen.untimed; onTriggered: timeline.cmd("timeline.createAtPlayhead") }
                    MenuItem { objectName: "timelineCreateRangeMenuItem"; text: timeline.createMode ? "退出拖拽新建" : "拖拽添加时间 / 新建空句"; onTriggered: { var entering = !timeline.createMode; timeline.selectTool("select"); timeline.createMode = entering } }
                }
            }

            ActionButton { id: allocateText; objectName: "allocateTextButton"; text: "分配文字"; compact: true; visible: !!timeline.current.pendingText; onClicked: timeline.cmd("textAllocation.begin", {id: timeline.current.cueId}); tip: "点选字间切分位置，将歌词分给前后两段" }
            Rectangle { visible: !timeline.readOnly; width: 1; height: 17; y: 5; color: Theme.line }
            Check { objectName: "manualTimingMode"; height: 27; visible: !timeline.readOnly; ToolTip.visible: hovered; ToolTip.text: "打轴模式：按 D 键打入点，再按 D 键打出点"; ToolTip.delay: 650; text: "打轴模式"; checked: !!(timeline.state.manualTiming || {}).enabled; onToggled: timeline.cmd("timeline.manualMode", {enabled: checked}) }
        }

        Row {
            id: rightTools; objectName: "timelineRightTools"
            anchors.right: parent.right
            y: foldHeader.stacked ? 32 : 1
            spacing: 5
            Item {
                id: audioDock
                visible: timeline.audioVisible && !timeline.collapsed
                width: audioToolbar.implicitWidth; height: 27
            }
            Rectangle { objectName: "audioZoomDivider"; visible: audioDock.visible; width: 1; height: 17; y: 5; color: Theme.line }
            Row {
                visible: !timeline.collapsed; spacing: 4
                ActionButton { objectName: "timelineZoomOut"; text: "−"; compact: true; enabled: timeline.total > 0; onClicked: timeline.zoom(1.5); tip: "缩小时间轴" }
                ActionButton { objectName: "timelineZoomIn"; text: "+"; compact: true; enabled: timeline.total > 0; onClicked: timeline.zoom(1 / 1.5); tip: "放大时间轴" }
                ActionButton { objectName: "audioVisibilityToggle"; glyph: "audio-wave"; compact: true; checkable: true; checked: timeline.audioVisible; onClicked: timeline.cmd("view.layout", {audioVisible:!timeline.audioVisible}); tip: (timeline.audioVisible ? "隐藏" : "显示") + (timelineAudio.mode === "waveform" ? "波形图" : "频谱图") }
            }
            ActionButton { id: followButton; objectName: "followCurrentButton"; text: "跟随"; compact: true; checkable: true; checked: timeline.followEnabled; onClicked: { timeline.followEnabled = !timeline.followEnabled; if (timeline.followEnabled) { timeline.followedId = ""; timeline.follow() } } visible: !timeline.collapsed }
            ActionButton {
                id: foldButton
                objectName: "timelineFoldButton"
                text: timeline.collapsed ? "展开" : "收起"; compact: true
                onClicked: timeline.collapsed = !timeline.collapsed
                contentItem: Item {
                    implicitWidth: foldContents.implicitWidth
                    implicitHeight: foldContents.implicitHeight
                    Row {
                        id: foldContents; objectName: "timelineFoldContents"
                        x: (parent.width - width) / 2; y: (parent.height - height) / 2
                        spacing: foldButton.spacing
                        Image {
                            objectName: "timelineFoldArrow"
                            width: 15; height: 15; y: (parent.height - height) / 2
                            source: "../assets/icons/chev-up.svg"; sourceSize.width: 24; sourceSize.height: 24
                            rotation: foldArrow.value
                        }
                        Text {
                            objectName: "timelineFoldText"
                            text: foldButton.text; textFormat: Text.PlainText
                            y: (parent.height - height) / 2
                            font.family: Theme.fontFamily; font.pixelSize: 11; font.weight: Font.Medium; color: Theme.label
                        }
                    }
                }
            }
        }
    }
    Item {
        id: foldViewport
        objectName: "timelineFoldContent"
        x: 13; y: foldHeader.y + (timeline.folding || timeline.collapsed ? timeline.expandedHeaderHeight : foldHeader.height) + 3; width: parent.width - 26
        height: timeline.collapsed && !timeline.folding ? 0 : Math.max(0, timeline.height - y - 6)
        clip: true
        opacity: foldOpacity.value
        visible: !timeline.collapsed || opacity > 0
        enabled: !timeline.collapsed
    }
    ColumnLayout {
        parent: foldViewport
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        // Keep track geometry and the user's vertical view stable while the
        // viewport clips away. Folding never animates playhead/clip positions.
        height: Math.max((timeline.folding || timeline.collapsed ? timeline.expandedLayoutHeight : Math.round(timeline.height)) - foldViewport.y - 6, 0)
        spacing: 3
        Item {
            id: analysisStrip; objectName: "independentAudioRegion"
            visible: timeline.audioVisible
            Layout.fillWidth: true; Layout.preferredHeight: timeline.audioDisplayHeight + timeline.audioToolbarSpace
            Layout.minimumHeight: timeline.audioDisplayHeight + timeline.audioToolbarSpace; Layout.maximumHeight: timeline.audioDisplayHeight + timeline.audioToolbarSpace
            Row {
                id: audioToolbar; objectName: "audioToolbarViewport"
                parent: timeline.lyricVisible ? audioDock : analysisStrip
                spacing: 5
                // Space activates the focused control instead of the workspace playback shortcut.
                Keys.onShortcutOverride: event => {
                    if (event.key === Qt.Key_Space) event.accepted = true
                }
                component ToolbarChoice: ChoiceBox {
                    id: choice
                    required property string widestLabel
                    FontMetrics { id: metrics; font: choice.font }
                    width: Math.ceil(metrics.advanceWidth(widestLabel)) + leftPadding + rightPadding + 5
                    height: 27
                    focusPolicy: Qt.StrongFocus
                    background: Rectangle {
                        radius: 6; color: parent.hovered || parent.visualFocus ? Theme.paper : Theme.slab
                        border.width: parent.visualFocus ? 2 : 1
                        border.color: parent.visualFocus ? Theme.lilac : Theme.line
                    }
                }
                ToolbarChoice {
                    objectName: "timelineAudioModeSwitch"; Accessible.name: "音轨显示"
                    model: ["波形", "频谱"]; widestLabel: "频谱"
                    currentIndex: timelineAudio.mode === "spectrum" ? 1 : 0
                    onActivated: index => timelineAudio.displaySettings({audioMode:index === 1 ? "spectrum":"waveform"})
                }
                ToolbarChoice {
                    objectName: "audioSourceSelector"; Accessible.name: "音源"
                    model: ["原声", "分离人声"]; widestLabel: "分离人声"
                    currentIndex: timelineAudio.analysis.source === "vocal" ? 1 : 0
                    onActivated: index => timeline.controller.selectAudioAnalysisSource(index === 1 ? "vocal":"original")
                    ToolTip.visible: hovered; ToolTip.text: "Alt+V 切换原声与分离人声"
                }
                ActionButton {
                    objectName: "spectrumSettings"; glyph: "gear"; compact: true; quiet: true
                    tip: "频谱显示设置"; onClicked: timelineAudio.openMenu(this)
                }
                ActionButton { objectName: "audioFollowCurrentButton"; text: "跟随"; compact: true; visible: !timeline.lyricVisible; checkable: true; checked: timeline.followEnabled
                    onClicked: { timeline.followEnabled = !timeline.followEnabled; timeline.pagePlayhead() } }
            }
            Repeater {
                model: !timeline.audioExpanded ? [] : timelineAudio.mode === "spectrum" ? timelineAudio.frequencyLabels() : [{hz:"波形",p:.5}]
                Label { required property var modelData; x: 0; y: timeline.audioToolbarSpace + modelData.p * (timeline.audioDisplayHeight - 12); width: timeline.labelWidth - 8; horizontalAlignment: Text.AlignRight; text: modelData.hz; color: Theme.label; font.pixelSize: 10 }
            }
            AudioAnalysisPanel {
                id: timelineAudio; objectName: "audioAnalysisPanel"
                x: timeline.labelWidth; y: timeline.audioToolbarSpace; width: analysisStrip.width - timeline.labelWidth - 12; height: timeline.audioDisplayHeight
                controller: timeline.controller; state: timeline.state; media: timeline.media
                timelineView: timeline
            }
            MouseArea {
                id: audioNavigation; objectName: "audioNavigationMouse"
                x: timelineAudio.x; y: timelineAudio.y; width: timelineAudio.width; height: timelineAudio.height
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton; preventStealing: true
                property bool scrubbing: false
                property int dragFrame: 1
                function finish() { if (scrubbing) timeline.cmd("media.scrubEnd", {frame: dragFrame}); scrubbing = false; timeline.endPan() }
                onPressed: mouse => {
                    timeline.forceActiveFocus()
                    if (mouse.button === Qt.RightButton) { timelineAudio.openMenu(); return }
                    if (timeline.tool === "hand" || mouse.button === Qt.MiddleButton || (mouse.modifiers & Qt.ShiftModifier)) timeline.beginPan(mouse.x, mouse.y, false)
                    else if (timeline.total > 0) { scrubbing = true; dragFrame = timeline.frameFor(mouse.x); timeline.cmd("media.scrubBegin", {frame: dragFrame}) }
                }
                onPositionChanged: mouse => { if (!pressed) return; if (timeline.panning) timeline.dragPan(mouse.x, mouse.y); else if (scrubbing) { dragFrame = timeline.frameFor(mouse.x); timeline.cmd("media.scrub", {frame: dragFrame}) } }
                onReleased: finish()
                onCanceled: finish()
                onVisibleChanged: if (!visible) finish()
                onWheel: wheel => timeline.wheelInput(wheel, false, wheel.x)
            }
        }
        MouseArea {
            id: audioGrip; objectName: "audioSplitHandle"
            visible: timeline.audioVisible && timeline.audioExpanded && timeline.lyricVisible
            Layout.fillWidth: true; Layout.preferredHeight: 6; Layout.minimumHeight: 6; Layout.maximumHeight: 6
            cursorShape: Qt.SplitVCursor; preventStealing: true; hoverEnabled: true
            property real pressY: 0
            property real startHeight: 0
            Rectangle { anchors.centerIn: parent; width: 38; height: 3; radius: 2; color: Theme.lilac; opacity: parent.containsMouse || parent.pressed ? 1 : .45 }
            ToolTip.visible: containsMouse; ToolTip.delay: 650; ToolTip.text: "上下拖动调整音轨高度"
            onPressed: mouse => { pressY = mapToItem(null, mouse.x, mouse.y).y; startHeight = timeline.audioHeight }
            onPositionChanged: mouse => { if (pressed) timeline.resizingAudioHeight = Math.max(36, Math.min(timeline.maximumAudioHeight, startHeight + mapToItem(null, mouse.x, mouse.y).y - pressY)) }
            onReleased: { timeline.cmd("view.layout", {audioHeight: timeline.audioHeight}); timeline.resizingAudioHeight = -1 }
            onCanceled: timeline.resizingAudioHeight = -1
        }
        Item {
            id: body; objectName: "lyricTimelineRegion"
            visible: timeline.lyricVisible
            WheelHandler {
                target: null
                enabled: !timeline.collapsed && timeline.total > 0
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                onWheel: event => { var pt = body.mapToItem(lanes, event.x, event.y); timeline.wheelInput(event, true, pt.x) }
            }
            // Cursor inheritance covers empty lanes, labels and row gaps. This
            // QObject changes no hit regions and never consumes pointer input.
            ToolCursor { target: body; mode: timeline.tool === "razor" ? "forbidden" : "" }
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 54
            Rectangle { objectName: "lyricTimelineBackground"; x: timeline.labelWidth; width: lanes.width; height: body.height; color: Theme.slab }
            Item {
                id: lanes
                z: timeline.tool === "hand" || timeline.createMode ? 2 : 0
                anchors.left: parent.left; anchors.leftMargin: timeline.labelWidth
                anchors.right: parent.right; anchors.rightMargin: 12; anchors.top: parent.top; anchors.bottom: parent.bottom
                clip: true
                Rectangle { width: parent.width; height: 1; y: timeline.rulerHeight - 1; color: Theme.emphasisBorder }
                Repeater {
                    model: 40
                    Rectangle {
                        required property int index
                        visible: index % 4 !== 0
                        x: index / 40 * lanes.width; y: 28; width: 1; height: 5; color: "#D7CFE8"
                    }
                }
                Repeater {
                    model: 11
                    Item {
                        required property int index
                        x: index / 10 * lanes.width; width: 1; height: lanes.height
                        Rectangle { y: 23; width: 1; height: 10; color: "#B8ADD3" }
                        Rectangle { y: timeline.rulerHeight; width: 1; height: parent.height - timeline.rulerHeight; color: Theme.line }
                        Label { id: tickLabel; objectName: "timelineTickLabel_" + index; textFormat: Text.PlainText; text: timeline.formatFrameAxis(timeline.viewFirst + index / 10 * timeline.span); anchors.horizontalCenter: parent.horizontalCenter; y: 10; color: "#6F6584"; font.pixelSize: 10; font.features: ({"tnum": 1}); visible: parent.x - implicitWidth / 2 >= 0 && parent.x + implicitWidth / 2 <= lanes.width }
                    }
                }
                MouseArea {
                    id: rulerMouse
                    objectName: "timelineRulerMouse"
                    anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton; preventStealing: true
                    property bool scrubbing: false
                    property bool creating: false
                    property string creationTarget: ""
                    property int creationStart: 1
                    property int creationEnd: 1
                    property int dragFrame: 1
                    ToolCursor {
                        target: rulerMouse
                        mode: timeline.tool === "razor" ? "forbidden" : ""
                        normalShape: timeline.createMode ? Qt.CrossCursor : timeline.tool === "hand" ? timeline.panning ? Qt.ClosedHandCursor : Qt.OpenHandCursor : Qt.ArrowCursor
                    }
                    function finish() { if (scrubbing) timeline.cmd("media.scrubEnd", {frame: dragFrame}); scrubbing = false; creating = false; creationTarget = "" }
                    onPressed: mouse => {
                        timeline.forceActiveFocus()
                        if (mouse.button === Qt.RightButton) { timelineContext.rowIdentity = ""; timelineContext.popup(); return }
                        if (timeline.tool === "hand" || (mouse.modifiers & Qt.ShiftModifier) || mouse.button === Qt.MiddleButton) timeline.beginPan(mouse.x, mouse.y, false)
                        else if (timeline.total > 0) {
                            if (timeline.createMode && mouse.y >= timeline.rulerHeight) {
                                var rowIndex = tracks.indexAt(1, tracks.contentY + mouse.y - tracks.y)
                                var row = timeline.rowAt(rowIndex)
                                creationTarget = row.untimed ? row.cueId : ""
                                creating = true; creationStart = creationEnd = timeline.frameFor(mouse.x)
                            }
                            else if (mouse.y < timeline.rulerHeight) { scrubbing = true; dragFrame = timeline.frameFor(mouse.x); timeline.cmd("media.scrubBegin", {frame: dragFrame}) }
                        }
                    }
                    onPositionChanged: mouse => {
                        if (!pressed) return
                        if (timeline.panning) timeline.dragPan(mouse.x, mouse.y)
                        else if (creating) creationEnd = timeline.frameFor(mouse.x)
                        else if (scrubbing) { dragFrame = timeline.frameFor(mouse.x); timeline.cmd("media.scrub", {frame: dragFrame}) }
                    }
                    onReleased: { if (creating) timeline.cmd(creationTarget ? "timeline.assignRange" : "timeline.createRange", {id: creationTarget, first: Math.min(creationStart, creationEnd), last: Math.max(creationStart, creationEnd)}); timeline.endPan(); finish() }
                    onCanceled: { timeline.endPan(); creating = false; finish() }
                    onWheel: wheel => timeline.wheelInput(wheel, wheel.y >= timeline.rulerHeight)
                }
            }
            ListView {
                id: tracks
                objectName: "timelineTracks"
                anchors.top: parent.top; anchors.topMargin: timeline.rulerHeight
                height: timeline.rowMode ? Math.min(parent.height - timeline.rulerHeight, timeline.rowMode * timeline.rowHeight) : Math.max(0, Math.floor((parent.height - timeline.rulerHeight) / timeline.rowHeight) * timeline.rowHeight)
                anchors.left: parent.left; anchors.right: parent.right
                model: timeline.rowModel
                onHeightChanged: {
                    timeline.endPan()
                    // manualView controls horizontal zoom/pan, not vertical sentence following.
                    if (timeline.followEnabled) Qt.callLater(timeline.positionCurrent)
                    else Qt.callLater(function() { timeline.clampRows(tracks.contentY) })
                }
                clip: true; interactive: false; cacheBuffer: 0; reuseItems: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { id: verticalScroll; objectName: "timelineVerticalScroll"; policy: ScrollBar.AsNeeded; onPressedChanged: if (pressed) timeline.rowFollowSuspended = true }
                delegate: Item {
                    id: lane
                    required property int index
                    required property string cueId
                    required property string chinese
                    required property string textRole
                    required property string speakerColor
                    readonly property color roleTint: speakerColor || Theme.lilac
                    readonly property string emptyText: textRole === "translation" ? "（空白译文）" : "（空白原文）"
                    readonly property string displayText: chinese || emptyText
                    required property int firstFrame
                    required property int lastFrame
                    required property bool locked
                    required property bool overlap
                    required property bool pendingText
                    required property int number
                    property var cue: ({cueId: cueId, chinese: chinese, firstFrame: firstFrame, lastFrame: lastFrame, locked: locked})
                    readonly property bool multiSelected: (timeline.state.multiSelectedIds || []).indexOf(cueId) >= 0
                    property bool selected: timeline.current.cueId === cueId
                    readonly property bool splitEligible: SplitTarget.eligible(cue, timeline.splitFrame)
                    readonly property bool showSplitTarget: !timeline.readOnly && timeline.tool === "razor" && timeline.razorHoverId === cueId && timeline.razorHoverValid
                    objectName: "timelineRow_" + cueId
                    width: tracks.width; height: timeline.rowHeight
                    Label { textFormat: Text.PlainText; x: 0; y: 5; width: timeline.labelWidth - 4; text: (lane.selected ? "当前句 " : "第 ") + lane.number + (lane.selected ? "" : " 句"); color: (lane.selected || lane.multiSelected) ? "#5E3B9E" : Theme.label; font.pixelSize: 11; font.weight: (lane.selected || lane.multiSelected) ? Font.DemiBold : Font.Normal; elide: Text.ElideRight }
                    MouseArea {
                        id: labelMouse
                        x: 0; y: 0; width: timeline.labelWidth; height: 26
                        enabled: timeline.tool === "select" && !timeline.createMode
                        ToolCursor { target: labelMouse; mode: timeline.tool === "razor" ? "forbidden" : "" }
                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                        onClicked: mouse => {
                            if (mouse.button === Qt.RightButton) timeline.openRowMenu(lane.cue,lane.multiSelected)
                            else if (mouse.modifiers & (Qt.ShiftModifier | Qt.ControlModifier)) timeline.selectGroup(lane.cue,mouse.modifiers)
                            else timeline.selectCue(lane.cue)
                        }
                        onWheel: wheel => timeline.wheelInput(wheel, true, 0)
                    }
                    Item {
                        id: laneContent
                        x: timeline.labelWidth; width: lanes.width; height: 26; clip: true
                        Rectangle { anchors.fill: parent; radius: 6; color: lane.overlap ? Theme.roseBg : Theme.paper; border.color: Theme.line }
                        MouseArea {
                            id: laneMouse
                            anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton; preventStealing: true
                            ToolCursor {
                                target: laneMouse
                                mode: timeline.tool === "razor" ? "forbidden" : ""
                                normalShape: timeline.createMode ? Qt.CrossCursor : timeline.tool === "hand" ? timeline.panning ? Qt.ClosedHandCursor : Qt.OpenHandCursor : Qt.ArrowCursor
                            }
                            onPressed: mouse => { if (timeline.createMode || timeline.tool === "hand" || mouse.button === Qt.MiddleButton || (mouse.modifiers & Qt.ShiftModifier)) mouse.accepted = false }
                            onClicked: mouse => { if (mouse.button === Qt.RightButton) timeline.openRowMenu(lane.cue,lane.multiSelected); else if (timeline.tool === "select" && (mouse.modifiers & Qt.ControlModifier)) timeline.selectGroup(lane.cue,mouse.modifiers); else if (timeline.tool === "select") timeline.selectCue(lane.cue) }
                            onWheel: wheel => timeline.wheelInput(wheel, true, wheel.x)
                        }
                        Loader {
                            id: clipLoader
                            anchors.fill: parent
                            // Instantiate clip text and handles only in the horizontal viewport.
                            active: lane.firstFrame > 0 && ((timeline.movingClip && timeline.moveId === lane.cueId) || (timeline.first(lane.cue) < timeline.viewLast && timeline.last(lane.cue) >= timeline.viewFirst))
                            sourceComponent: Component {
                                Item {
                                    Rectangle {
                                        id: clipBar
                                        objectName: "timelineClip_" + lane.cueId
                                        x: timeline.xFor(timeline.first(lane.cue))
                                        width: Math.max(3, (timeline.last(lane.cue) - timeline.first(lane.cue) + 1) / timeline.span * lanes.width)
                                        height: 26; radius: 5
                                        color: lane.selected ? Theme.lilac : lane.multiSelected ? "#CBB6F4" : lane.speakerColor ? Qt.rgba(lane.roleTint.r, lane.roleTint.g, lane.roleTint.b, .18) : Theme.lilacSoft
                                        border.color: lane.showSplitTarget ? timeline.lyricSplitBorder : (lane.multiSelected && !lane.selected) ? "#5E3B9E" : lane.speakerColor ? lane.roleTint : lane.selected ? Theme.lilacDark : Theme.lilac
                                        border.width: lane.showSplitTarget || lane.selected || lane.multiSelected ? 2 : 1
                                        property bool presentationReady: false
                                        property bool pendingState: lane.pendingText
                                        property int entranceDuration: 200
                                        onPendingStateChanged: if (presentationReady) entranceDuration = 200
                                        readonly property real pendingOpacity: pendingFade.value
                                        readonly property bool pendingAnimating: pendingFade.running
                                        readonly property int pendingDuration: pendingFade.duration
                                        function finishPresentation() {
                                            var started = timeline.pendingEntranceIds[lane.cueId]
                                            var remaining = started ? Math.max(0, 200 - (Date.now() - started)) : 0
                                            if (lane.pendingText) {
                                                clipBar.entranceDuration = remaining || 200
                                                pendingFade.currentValue = remaining ? 1 - Math.pow(remaining / 200, 3) : 1
                                            }
                                            clipBar.presentationReady = true
                                        }
                                        // A delegate-owned timer is cancelled with its QObject owner.
                                        // Qt.callLater can otherwise retain a callback past panel teardown.
                                        // Keep the same edit-time interval across delegate recreation.
                                        Timer {
                                            interval: 0; repeat: false; running: true
                                            onTriggered: clipBar.finishPresentation()
                                        }
                                        MotionNumber { id: pendingFade; targetValue: clipBar.presentationReady && clipBar.pendingState ? 1 : 0; duration: clipBar.entranceDuration; easingType: Easing.OutCubic }
                                        Rectangle { objectName: "pendingClipFill_" + lane.cueId; anchors.fill: parent; radius: parent.radius; color: Theme.pendingTextBg; border.color: timeline.lyricPendingAccent; opacity: lane.selected ? 0 : pendingFade.value }
                                        Rectangle { objectName: "pendingClipMark_" + lane.cueId; x: 6; y: parent.height - 4; width: Math.max(0, parent.width - 12); height: 3; radius: 1; color: timeline.lyricPendingAccent; opacity: lane.selected ? pendingFade.value : 0 }
                                        Rectangle { objectName: "multiClipAccent_" + lane.cueId; visible: lane.multiSelected && !lane.selected; x: 1; y: 2; width: 4; height: parent.height - 4; radius: 2; color: "#5E3B9E" }
                                        Label { objectName: "timelineText_" + lane.cueId; textFormat: Text.PlainText; x: Math.max(12, -clipBar.x + 12); width: Math.max(0, Math.min(clipBar.width, laneContent.width - clipBar.x) - x - 12); clip: true; visible: width > 0; height: parent.height; text: (lane.multiSelected && !lane.selected ? "✓ " : "") + (lane.pendingText && !lane.chinese ? "待分配文字" : lane.displayText); color: lane.selected ? "white" : (lane.multiSelected ? "#2F1B59" : Theme.ink); font.weight: lane.multiSelected && !lane.selected ? Font.DemiBold : Font.Normal; style: Text.Normal; opacity: !lane.selected && lane.pendingText && !lane.chinese ? 0.8 : 1; font.italic: lane.pendingText && !lane.chinese; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                                        MouseArea {
                                            anchors.fill: parent; acceptedButtons: Qt.RightButton
                                            enabled: !timeline.readOnly
                                            onClicked: mouse => { if (lane.pendingText && !lane.multiSelected) { timeline.selectCue(lane.cue); pendingMenu.popup(clipBar,mouse.x,mouse.y) } else timeline.openRowMenu(lane.cue,lane.multiSelected) }
                                        }
                                        Menu {
                                            id: pendingMenu; objectName: "timelinePendingMenu_" + lane.cueId
                                            property var action: ({})
                                            onAboutToShow: action = timeline.state.pendingTextAction || ({})
                                            Connections { target: timeline; function onVisibleChanged() { if (!timeline.visible) pendingMenu.close() } function onCollapsedChanged() { if (timeline.collapsed) pendingMenu.close() } }
                                            MenuItem { objectName: "timelineAllocateAction_" + lane.cueId; text: "分配文字"; enabled: !!pendingMenu.action.canAllocate; onTriggered: timeline.cmd("textAllocation.begin", pendingMenu.action) }
                                            MenuItem { objectName: "timelineKeepAction_" + lane.cueId; text: "不拆分文字"; enabled: !!pendingMenu.action.canKeep; onTriggered: timeline.cmd("textAllocation.keep", pendingMenu.action) }
                                        }
                                        MouseArea {
                                            id: clipMouse
                                            property bool shiftCandidate: false
                                            property point pressGlobal: Qt.point(0,0)
                                            property point pressLane: Qt.point(0,0)
                                            anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.MiddleButton; preventStealing: true
                                            hoverEnabled: timeline.tool === "razor"
                                            ToolCursor {
                                                target: clipMouse
                                                mode: timeline.tool === "razor" ? timeline.razorHoverId === lane.cueId && timeline.razorHoverValid ? "razor" : "forbidden" : ""
                                                normalShape: timeline.createMode ? Qt.CrossCursor : timeline.tool === "hand" ? timeline.panning ? Qt.ClosedHandCursor : Qt.OpenHandCursor : lane.locked ? Qt.ArrowCursor : clipMouse.pressed && timeline.moveDelta ? Qt.ClosedHandCursor : Qt.ArrowCursor
                                            }
                                            onEntered: { var pt = mapToItem(lanes, mouseX, mouseY); timeline.previewRazor(lane.cue, pt.x) }
                                            onExited: if (timeline.razorHoverId === lane.cueId) { timeline.razorHoverFrame = 0; timeline.razorHoverId = "" }
                                            onPressed: mouse => {
                                                var pt = mapToItem(lanes, mouse.x, mouse.y)
                                                shiftCandidate = timeline.tool === "select" && mouse.button === Qt.LeftButton && !!(mouse.modifiers & Qt.ShiftModifier)
                                                pressGlobal = mapToGlobal(mouse.x,mouse.y)
                                                pressLane = pt
                                                if (shiftCandidate) return
                                                if (timeline.tool === "select" && mouse.button === Qt.LeftButton && !shiftCandidate && (mouse.modifiers & Qt.ControlModifier)) { timeline.selectGroup(lane.cue, mouse.modifiers); return }
                                                if (timeline.tool === "hand" || (mouse.modifiers & Qt.ShiftModifier) || mouse.button === Qt.MiddleButton) timeline.beginPan(pt.x, pt.y, timeline.tool === "hand")
                                                else if (timeline.createMode) mouse.accepted = false
                                                else if (timeline.tool === "razor") timeline.forceActiveFocus()
                                                else timeline.beginMove(lane.cue, pt.x)
                                            }
                                            onClicked: mouse => { if (timeline.tool === "razor" && mouse.button === Qt.LeftButton && !(mouse.modifiers & Qt.ShiftModifier)) { var pt = mapToItem(lanes, mouse.x, mouse.y); timeline.razor(lane.cue, pt.x) } }
                                            onPositionChanged: mouse => { var pt = mapToItem(lanes, mouse.x, mouse.y); timeline.previewRazor(lane.cue, pt.x); if (pressed) {
                                                if (shiftCandidate) {
                                                    var g = mapToGlobal(mouse.x, mouse.y)
                                                    if (Math.abs(g.x-pressGlobal.x)+Math.abs(g.y-pressGlobal.y) < Qt.styleHints.startDragDistance) return
                                                    shiftCandidate = false
                                                    timeline.beginPan(pressLane.x, pressLane.y, false)
                                                }
                                                if (timeline.panning) timeline.dragPan(pt.x,pt.y)
                                                else if (timeline.movingClip) timeline.adjustMove(lane.cue, pt.x, mouse.modifiers) } }
                                            onReleased: { if (shiftCandidate) timeline.selectGroup(lane.cue,Qt.ShiftModifier); shiftCandidate = false; if (timeline.movingClip) timeline.commitMove(); timeline.endPan() }
                                            onCanceled: { shiftCandidate = false; timeline.cancelMove(); timeline.endPan() }
                                            onWheel: wheel => { var pt = mapToItem(lanes, wheel.x, wheel.y); timeline.wheelInput(wheel, true, pt.x) }
                                        }
                            Repeater {
                                model: lane.selected ? 2 : 0
                                Rectangle {
                                    id: edgeHandle
                                    required property int index
                                    property string edgeName: index === 0 ? "first" : "last"
                                    objectName: "cueEdge_" + edgeName
                                    visible: !timeline.readOnly && !lane.cue.locked && timeline.tool === "select" && !timeline.createMode
                                    enabled: visible
                                    x: edgeName === "first" ? 0 : clipBar.width - width
                                    width: 10; height: parent.height; radius: 4; color: "#7652B8"
                                    activeFocusOnTab: true
                                    Accessible.role: Accessible.Slider
                                    Accessible.name: edgeName === "first" ? "调整当前句入点帧" : "调整当前句出点帧"
                                    border.width: activeFocus ? 2 : 0; border.color: "#FFFFFF"
                                    Rectangle { anchors.centerIn: parent; width: 2; height: 12; radius: 1; color: "#FFFFFF" }
                                    Keys.onPressed: event => {
                                        if (event.key !== Qt.Key_Left && event.key !== Qt.Key_Right) return
                                        if (!timeline.keyboardEditing && event.isAutoRepeat) { event.accepted = true; return }
                                        if (!timeline.keyboardEditing) {
                                            timeline.keyboardId = lane.cue.cueId; timeline.keyboardEdge = edgeName
                                            timeline.cmd("history.begin", {label: "键盘微调出入点"}); timeline.keyboardEditing = true
                                            timeline.cmd("editor.activity", {id: "timeline:" + timeline.keyboardId + ":" + edgeName, active: true, composing: false})
                                        }
                                        timeline.cmd("row.setBoundary", {id: lane.cue.cueId, edge: edgeName, frame: (edgeName === "first" ? lane.cue.firstFrame : lane.cue.lastFrame) + (event.key === Qt.Key_Left ? -1 : 1)})
                                        event.accepted = true
                                    }
                                    Keys.onReleased: event => {
                                        if (event.key === Qt.Key_Left || event.key === Qt.Key_Right) { if (!event.isAutoRepeat) timeline.endKeyboardEdit(); event.accepted = true }
                                    }
                                    onActiveFocusChanged: if (!activeFocus) timeline.endKeyboardEdit()
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.SizeHorCursor
                                        preventStealing: true
                                        onPressed: mouse => { edgeHandle.forceActiveFocus(); var pt = mapToItem(lanes, mouse.x, mouse.y); timeline.begin(lane.cue, edgeHandle.edgeName, pt.x) }
                                        onPositionChanged: mouse => { if (pressed) { var pt = mapToItem(lanes, mouse.x, mouse.y); timeline.adjust(lane.cue, pt.x, mouse.modifiers) } }
                                        onReleased: timeline.commit()
                                        onCanceled: timeline.cancelEdgeEdit()
                                    }
                                }
                            }
                                    }
                                }
                            }
                        }
                        Label {
                            objectName: "timelineOffscreen_" + lane.cueId
                            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8
                            visible: !clipLoader.active
                            textFormat: Text.PlainText; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; font.pixelSize: 11; color: Theme.label
                            horizontalAlignment: lane.lastFrame < timeline.viewFirst ? Text.AlignLeft : Text.AlignRight
                            text: !lane.firstFrame ? "未打轴 · " + (lane.displayText) : (lane.lastFrame < timeline.viewFirst ? "◀ " + timeline.formatFrameMark(lane.firstFrame) + " · " : "") + (lane.displayText) + (lane.firstFrame >= timeline.viewLast ? " · " + timeline.formatFrameMark(lane.firstFrame) + " ▶" : "")
                        }
                    }
                }
            }
            MouseArea {
                id: handRowsMouse
                objectName: "timelineHandRowsMouse"
                // A fixed surface survives delegate recycling during vertical
                // drags and includes labels, row gaps and an empty timeline.
                x: 0; y: tracks.y; width: parent.width - 12; height: tracks.height; z: 3
                enabled: timeline.tool === "hand" && !timeline.createMode
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton; preventStealing: true
                ToolCursor {
                    target: handRowsMouse
                    // MouseArea.enabled disables input, not QQuickItem cursor
                    // hit testing. An inactive hand must not mask C or V below.
                    normalShape: handRowsMouse.enabled ? timeline.panning ? Qt.ClosedHandCursor : Qt.OpenHandCursor : -1
                }
                onPressed: mouse => timeline.beginPan(mouse.x - timeline.labelWidth, mouse.y, true)
                onPositionChanged: mouse => { if (pressed) timeline.dragPan(mouse.x - timeline.labelWidth, mouse.y) }
                onReleased: timeline.endPan()
                onCanceled: timeline.endPan()
                onWheel: wheel => timeline.wheelInput(wheel, true, wheel.x - timeline.labelWidth)
            }
            Item {
                x: timeline.labelWidth; width: lanes.width; height: body.height; clip: true
                Item {
                    objectName: "razorCutGuide"
                    visible: timeline.tool === "razor" && timeline.razorHoverFrame > 0
                    x: timeline.xFor(timeline.razorHoverFrame); y: timeline.rulerHeight; width: 1; height: parent.height - timeline.rulerHeight
                    // Dashed cut guide only: no top horizontal bar obscuring
                    // the lyric lane or suggesting another draggable handle.
                    Repeater { model: Math.max(0, Math.ceil(parent.height / 7)); Rectangle { required property int index; y: index * 7; width: 1; height: 4; color: timeline.razorHoverValid ? Theme.ink : timeline.lyricPlayhead } }
                }
                Repeater {
                    model: [timeline.previous.lastFrame && timeline.first(timeline.current) === timeline.previous.lastFrame + 1 ? timeline.first(timeline.current) : 0,
                            timeline.next.firstFrame && timeline.last(timeline.current) + 1 === timeline.next.firstFrame ? timeline.next.firstFrame : 0]
                    Item {
                        required property var modelData
                        // Option 1 (PR pure transient snap): never show static idle alignment lines.
                        visible: false; objectName: "timelineAlignmentGuide_" + modelData
                        x: timeline.xFor(modelData) - width / 2; y: timeline.rulerHeight; width: 1; height: parent.height - timeline.rulerHeight
                    }
                }
                Repeater {
                    model: timeline.state.markers || []
                    Item {
                        required property var modelData
                        objectName: "timelineMarker_" + modelData.frame
                        x: timeline.xFor(modelData.frame) - width / 2; y: 0; width: 1; height: body.height
                        visible: modelData.frame >= timeline.viewFirst && modelData.frame < timeline.viewLast
                        // Tier 1 PR narrow vertical solid bookmark (6.6x9 px inside 9x10 Canvas).
                        Canvas {
                            objectName: "timelineMarkerHead_" + modelData.frame
                            x: (parent.width - width) / 2; y: 1; width: 9; height: 10
                            readonly property real tipY: 9
                            onPaint: {
                                var c=getContext("2d"); c.reset();
                                c.fillStyle="#6D8D4B"; c.strokeStyle="#3B5323"; c.lineWidth=1; c.lineJoin="round";
                                c.beginPath(); c.moveTo(1.5,0.5); c.lineTo(7.5,0.5); c.lineTo(7.5,5.8); c.lineTo(4.5,9.4); c.lineTo(1.5,5.8); c.closePath();
                                c.fill(); c.stroke()
                            }
                        }
                        Repeater { model: Math.max(0, Math.ceil((parent.height - 10) / 5)); Rectangle { required property int index; objectName: "timelineMarkerGuide_" + index; x: 0; y: 10 + index * 5; width: 1; height: y < timeline.rulerHeight ? 1.5 : 2.5; color: "#4B6B32"; opacity: y < timeline.rulerHeight ? .78 : .85 } }
                    }
                }
                Rectangle { visible: rulerMouse.creating; x: Math.min(timeline.xFor(rulerMouse.creationStart), timeline.xFor(rulerMouse.creationEnd)); y: timeline.rulerHeight; width: Math.max(3, Math.abs(timeline.xFor(rulerMouse.creationEnd)-timeline.xFor(rulerMouse.creationStart))); height: 26; color: "#558562C5"; border.color: Theme.lilac; radius: 5 }
                Rectangle {
                    objectName: "manualStartMark"; visible: !!(timeline.state.manualTiming || {}).startFrame
                    x: timeline.xFor((timeline.state.manualTiming || {}).startFrame || 1) - width / 2; y: 23; width: 2; height: parent.height - 23; color: timeline.lyricStartMark
                    Canvas {
                        objectName: "manualStartDiamond"; x: (parent.width - width) / 2; y: 0; width: 11; height: 11
                        onPaint: {
                            var c=getContext("2d"); c.reset();
                            c.fillStyle=timeline.lyricStartMark; c.strokeStyle="#235064"; c.lineWidth=1; c.lineJoin="round";
                            c.beginPath(); c.moveTo(1.5,0.5); c.lineTo(9.5,0.5); c.lineTo(9.5,5.5); c.lineTo(5.5,10); c.lineTo(1.5,5.5); c.closePath();
                            c.fill(); c.stroke()
                        }
                    }
                }
                Rectangle {
                    objectName: "timelinePlayhead"
                    x: timeline.xFor(rulerMouse.scrubbing ? rulerMouse.dragFrame : timeline.cursorFrame || 1) - width / 2
                    visible: !!timeline.media.ready; y: 23; width: 1.5; height: parent.height - 23; color: timeline.lyricPlayhead
                    Canvas {
                        objectName: "timelinePlayheadDiamond"
                        x: (parent.width - width) / 2; y: 0; width: 11; height: 11
                        onPaint: {
                            var c=getContext("2d"); c.reset();
                            c.fillStyle=timeline.lyricPlayhead; c.strokeStyle="#7A3050"; c.lineWidth=1; c.lineJoin="round";
                            c.beginPath(); c.moveTo(0.8,0.5); c.lineTo(10.2,0.5); c.lineTo(10.2,5.6); c.lineTo(5.5,10.3); c.lineTo(0.8,5.6); c.closePath();
                            c.fill(); c.stroke();
                            c.strokeStyle="#561E36"; c.lineWidth=1.5; c.beginPath(); c.moveTo(5.5,7.4); c.lineTo(5.5,10.5); c.stroke()
                        }
                    }
                }
                Item {
                    objectName: "timelineSnapGuide"
                    visible: (timeline.editing || timeline.movingClip) && timeline.snapped
                    x: timeline.xFor(timeline.snapFrame) - width / 2; y: timeline.rulerHeight; width: 1; height: parent.height - timeline.rulerHeight
                    readonly property string activeDragId: timeline.editing ? timeline.editId : timeline.movingClip ? timeline.moveId : ""
                    readonly property int activeDragRow: {
                        var id = activeDragId
                        if (id && timeline.rowModel) {
                            for (var i = 0; i < timeline.rowCount; ++i)
                                if (timeline.rowAt(i).cueId === id) return i
                        }
                        return Math.max(0, timeline.selectedIndex)
                    }
                    readonly property real snapArrowY: Math.max(3, Math.min(height - 14, activeDragRow * timeline.rowHeight - tracks.contentY + 7))
                    Rectangle { anchors.fill: parent; color: Theme.lilacDark }
                    Canvas {
                        objectName: "timelineSnapArrows"
                        x: (parent.width - width) / 2; y: parent.snapArrowY; width: 13; height: 12
                        onPaint: {
                            var c=getContext("2d"); c.reset(); c.fillStyle=Theme.lilacDark;
                            c.beginPath(); c.moveTo(0.5,1); c.lineTo(5.5,6); c.lineTo(0.5,11); c.closePath(); c.fill();
                            c.beginPath(); c.moveTo(12.5,1); c.lineTo(7.5,6); c.lineTo(12.5,11); c.closePath(); c.fill()
                        }
                    }
                }
                Badge { objectName: "timelineSnapFeedback"; visible: timeline.editing || timeline.movingClip; x: Math.max(0, Math.min(lanes.width - width, timeline.xFor(timeline.snapped ? timeline.snapFrame : timeline.editing ? timeline.editFrame : timeline.moveFirst + timeline.moveDelta) - width / 2)); y: 0; text: (timeline.editing ? timeline.formatFrameMark(timeline.editFrame) : timeline.formatFrameMark(timeline.moveFirst + timeline.moveDelta) + "—" + timeline.formatFrameMark(timeline.moveLast + timeline.moveDelta)) + (timeline.snapped ? " · " + timeline.snapLabel : "") }
            }
        }
        ScrollBar {
            id: horizontalScroll
            objectName: "timelineHorizontalScroll"
            Layout.fillWidth: true; Layout.leftMargin: timeline.labelWidth; Layout.rightMargin: 12
            orientation: Qt.Horizontal; policy: ScrollBar.AlwaysOn; implicitHeight: 16
            Layout.minimumHeight: 16; Layout.maximumHeight: 16; Layout.bottomMargin: 3
            padding: 2
            size: Math.min(1, timeline.span / Math.max(1, timeline.contentLast - timeline.contentFirst))
            position: (timeline.viewFirst - timeline.contentFirst) / Math.max(1, timeline.contentLast - timeline.contentFirst)
            onPositionChanged: if (pressed) timeline.panTo(timeline.contentFirst + position * (timeline.contentLast - timeline.contentFirst))
        }
    }
    Menu {
        id: timelineContext; objectName: "timelineContextMenu"
        property string rowIdentity: ""
        onClosed: rowIdentity = ""
        MenuItem { text: "合并选中"; objectName: "timelineMergeSelected"; visible: !!timelineContext.rowIdentity && !timeline.readOnly && (timeline.state.multiSelectedIds || []).length >= 2; height: visible ? implicitHeight : 0; onTriggered: timeline.cmd("row.merge", {id: timelineContext.rowIdentity, ids: timeline.state.multiSelectedIds}) }
        MenuItem { text: "在播放头处新建句"; enabled: !timeline.readOnly && !!timeline.media.ready; onTriggered: timeline.cmd("timeline.createAtPlayhead") }
        MenuItem { text: "在播放头添加标记点"; enabled: !timeline.readOnly && !!timeline.media.ready; onTriggered: timeline.cmd("marker.add",{frame:timeline.media.displayedFrame}) }
        MenuItem { text: "时间轴适应窗口"; enabled: timeline.total > 0; onTriggered: timeline.fit() }
    }
    TextAllocationPopup {
        id: allocationPopup
        controller: timeline.controller; allocation: timeline.state.textAllocation || ({})
        ownerActive: timeline.visible && !timeline.collapsed && timeline.windowActive
        anchorItem: timeline
        parent: Overlay.overlay
    }
}
