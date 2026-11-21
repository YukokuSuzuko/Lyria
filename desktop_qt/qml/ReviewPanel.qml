import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "SplitTarget.js" as SplitTarget

Panel {
    id: panel
    objectName: "reviewPanel"
    required property var controller
    required property var state
    required property var media
    // Outer scroll viewport in this panel's coordinates. Standalone panels
    // default to their own bounds; scrolling changes painting, not row geometry.
    property real viewportTop: 0
    property real viewportHeight: height
    readonly property var current: state.selection ? state.selection.current || ({}) : ({})
    readonly property var previous: state.selection ? state.selection.previous || ({}) : ({})
    readonly property var next: state.selection ? state.selection.next || ({}) : ({})
    readonly property var previousVisible: state.selection ? state.selection.previousVisible || previous : previous
    readonly property var nextVisible: state.selection ? state.selection.nextVisible || next : next
    readonly property bool sourceCandidate: !!state.candidateSource && state.candidateSource !== "main"
    readonly property bool editable: !sourceCandidate && !(state.alignmentPreview || {}).active
    readonly property bool hasCurrent: !!current.cueId
    readonly property var capabilities: state.capabilities || ({})
    readonly property var pendingAction: state.pendingTextAction || ({})
    property var activeEditor: null
    property var splitCursor: null
    readonly property int splitFrame: SplitTarget.nearest(state.markers || [], media.displayedFrame || 0)
    readonly property bool editingHasFocus: (activeEditor && activeEditor.activeFocus) || speakerField.editing || firstField.activeFocus || lastField.activeFocus
    readonly property string markerSignature: JSON.stringify(state.markers || [])
    // Reveal only on explicit marker changes, never every playback tick. A
    // focused text/IME session owns its viewport until the user leaves it.
    onMarkerSignatureChanged: Qt.callLater(autoRevealSplitTarget)
    function autoRevealSplitTarget() {
        if ((state.markers || []).length && !media.playing && !editingHasFocus) revealSplitTarget(false)
    }
    function revealSplitTarget(notify) {
        if (editingHasFocus) {
            if (notify !== false) feedback("请先按 Esc 结束文字编辑，再定位可切分句")
            return
        }
        var index = -1
        for (var i = 0; i < list.count; ++i) {
            var cue = list.model.get(i)
            if (SplitTarget.eligible(cue, splitFrame)) {
                if (index < 0) index = i
                if (cue.cueId === current.cueId) { index = i; break }
            }
        }
        if (index < 0) {
            if (notify !== false) feedback("F" + splitFrame + " 处没有可切分的句子；" + SplitTarget.range(current))
            return
        }
        if (index === selectedIndex) { requestSelectionReveal(); return }
        stopAutoScroll(false)
        followSelection = false; scrollAnchor = null; anchorItem = null
        list.positionViewAtIndex(index, ListView.Contain)
        list.forceLayout()
    }
    // Selection and text entry are separate modes. Only explicit double-click
    // or F2 entry gives the TextArea input ownership.
    function selectRow(identity, modifiers) {
        panel.forceActiveFocus()
        cmd("review.select", {id: identity, ctrl: !!(modifiers & Qt.ControlModifier), shift: !!(modifiers & Qt.ShiftModifier)})
    }
    function editSelectedRow() {
        if (!visible || !hasCurrent) return
        if (!editable) { feedback("当前为只读预览，请先通过「对比与应用」应用到主时间轴后再编辑"); return }
        if (current.locked) { feedback("该句已锁定，请先解锁后再编辑"); return }
        var identity = current.cueId
        var index = selectedIndex
        if (index < 0 || index >= list.count) {
            feedback("当前选中句被筛选隐藏，请先关闭“仅待确认”再编辑")
            return
        }
        // Explicit entry must not wait for a reveal animation or edit a cached
        // delegate belonging to a different filtered/model index.
        stopAutoScroll(false)
        followSelection = true
        scrollAnchor = null
        anchorItem = null
        syncViewport()
        list.forceLayout()
        var item = list.itemAtIndex(index)
        if (item && item.cueId === identity && current.cueId === identity) item.beginEditing()
    }
    signal fullWidthRequested()
    property bool showTimecodes: false
    signal timeFormatRequested()
    property bool evidenceCollapsed: false
    property string followedId: ""
    property bool followSelection: true
    property bool syncingViewport: false
    property bool animateSelectionReveal: false
    readonly property bool autoScrollRunning: autoScroll.running
    function stopAutoScroll(settle) {
        var destination = autoScroll.to
        var wasRunning = autoScroll.running
        autoScroll.stop()
        animateSelectionReveal = false
        if (settle && wasRunning && followSelection) {
            list.contentY = destination
            scheduleViewport()
        }
    }
    property var scrollAnchor: null
    property Item anchorItem: null
    readonly property int selectedIndex: state.selectedVisibleIndex === undefined ? -1 : state.selectedVisibleIndex
    onCurrentChanged: {
        if ((current.cueId || "") !== followedId) {
            followedId = current.cueId || ""
            requestSelectionReveal()
        }
    }
    onSelectedIndexChanged: scheduleViewport()
    onVisibleChanged: { if (visible) scheduleViewport(); else stopAutoScroll(true) }

    function requestSelectionReveal() {
        autoScroll.stop()
        animateSelectionReveal = Theme.motionEnabled
        followSelection = true
        scrollAnchor = null
        anchorItem = null
        scheduleViewport()
    }
    function scheduleViewport() {
        if (!syncingViewport) Qt.callLater(syncViewport)
    }
    function manualScroll() {
        stopAutoScroll(false)
        // Stop following before Flickable/ScrollBar handles the input. Geometry
        // and ordinary state updates must not pull a reader back to selection.
        followSelection = false
        scrollAnchor = null
        anchorItem = null
    }
    function rememberScrollAnchor() {
        if (followSelection || list.count === 0) return
        var index = list.indexAt(1, list.contentY + 1)
        if (index < 0) index = list.indexAt(1, list.contentY + 4)
        var item = list.itemAtIndex(index)
        if (item) scrollAnchor = {id: item.cueId, index: index, y: item.mapToItem(list, 0, 0).y}
        anchorItem = null
    }
    function restoreScrollAnchor() {
        if (scrollAnchor) {
            var index = Math.min(scrollAnchor.index, list.count - 1)
            // Structural resets are rare. Resolve just this saved identity once,
            // never copy/scan all rows on playback or on each layout frame.
            if (index >= 0 && list.model.get(index).cueId !== scrollAnchor.id) {
                for (var i = 0; i < list.count; ++i) {
                    if (list.model.get(i).cueId === scrollAnchor.id) { index = i; break }
                }
            }
            scrollAnchor = {id: scrollAnchor.id, index: index, y: scrollAnchor.y}
        }
        scheduleViewport()
    }
    function syncViewport() {
        if (!visible || list.height <= 0 || syncingViewport || autoScroll.running) return
        if (activeEditor && activeEditor.inputMethodComposing) return
        var index = followSelection ? selectedIndex : scrollAnchor ? scrollAnchor.index : -1
        if (index < 0 || index >= list.count) return
        syncingViewport = true
        var fromY = list.contentY
        var shouldAnimate = animateSelectionReveal && followSelection && Theme.motionEnabled
        animateSelectionReveal = false
        // positionViewAtIndex can use provisional heights during reset/resize.
        // Materialize the target first, then correct against its actual geometry.
        list.forceLayout()
        var item = list.itemAtIndex(index) || (list.currentIndex === index ? list.currentItem : null)
        if (!item) {
            list.positionViewAtIndex(index, ListView.Contain)
            list.forceLayout()
            item = list.itemAtIndex(index)
        }
        if (item) {
            var top = item.mapToItem(list, 0, 0).y
            var destination = list.contentY
            if (followSelection) {
                if (item.height <= list.height) {
                    if (top < 0) destination += top
                    else if (top + item.height > list.height) destination += top + item.height - list.height
                } else if (top > 0 || top + item.height < list.height) {
                    // A long translation can exceed the viewport. Keep its
                    // visible portion instead of fighting the editor's caret.
                    destination += top
                }
            } else {
                anchorItem = item
                destination += top - scrollAnchor.y
            }
            if (shouldAnimate && Math.abs(destination - fromY) > 0.5) {
                list.contentY = fromY
                autoScroll.from = fromY
                autoScroll.to = destination
                autoScroll.start()
            } else list.contentY = destination
        }
        syncingViewport = false
    }
    NumberAnimation {
        id: autoScroll; target: list; property: "contentY"
        duration: list.highlightMoveDuration; easing.type: Easing.OutCubic
        onFinished: { panel.animateSelectionReveal = Theme.motionEnabled; panel.scheduleViewport() }
    }
    Connections {
        target: Theme
        function onMotionEnabledChanged() { if (!Theme.motionEnabled) panel.stopAutoScroll(true) }
    }
    Connections {
        target: panel.controller
        function onSelectionRequested() { panel.requestSelectionReveal() }
    }
    Connections {
        target: panel.controller ? panel.controller.rows : null
        function onModelAboutToBeReset() { panel.stopAutoScroll(false); panel.rememberScrollAnchor() }
        function onModelReset() { panel.restoreScrollAnchor() }
    }
    Connections {
        target: list.currentItem
        function onYChanged() { panel.scheduleViewport() }
        function onHeightChanged() { panel.scheduleViewport() }
    }
    Connections {
        target: panel.anchorItem
        function onYChanged() { panel.scheduleViewport() }
        function onHeightChanged() { panel.scheduleViewport() }
    }
    signal editingStarted(var editor)
    signal nextStep()
    signal feedback(string message)
    function cmd(name, payload) { controller.command(name, payload || {}) }
    function relation(a, b, label) {
        if (!a.cueId || !b.cueId || !a.lastFrame || !b.firstFrame) return ""
        var gap = b.firstFrame - a.lastFrame - 1
        return label + (gap === 0 ? "无缝" : gap > 0 ? "间隔 " + gap + " 帧" : "重叠 " + (-gap) + " 帧")
    }
    function splitAtCursor() {
        if (!editable) { feedback("当前为只读预览，请先通过「对比与应用」应用到主时间轴后再编辑"); return }
        if (activeEditor && activeEditor.inputMethodComposing) { feedback("请先完成输入法选词，再切分歌词"); return }
        var caret = activeEditor && activeEditor.objectName === "chineseEditor_" + current.cueId
            ? {cueId: current.cueId, text: activeEditor.text, offset: activeEditor.cursorPosition} : splitCursor
        if (!hasCurrent || !caret || caret.cueId !== current.cueId || caret.text !== current.chinese) { feedback("请先将光标移到当前句要切分的字间位置"); return }
        var markers = state.markers || []
        if (markers.length === 0) { feedback("请先按 M 添加标记点，再将光标放在要切分的字间位置"); return }
        var chosen = splitFrame
        cmd("row.split", {id: current.cueId, frame: chosen, offsetUtf16: controller.graphemeBoundary(caret.text, caret.offset)})
    }
    implicitHeight: 480
    readonly property real minimumPanelHeight: reviewContents.implicitHeight + 8
    ColumnLayout {
        id: reviewContents
        // Keep a small font-metric safety margin in the default four-panel height.
        // Editable rows/evidence retain their full budgets; trim only inter-control gaps.
        anchors.fill: parent; anchors.margins: 8; anchors.topMargin: 4; anchors.bottomMargin: 4; spacing: 3
        RowLayout {
            objectName: "reviewHeaderRow"
            Layout.fillWidth: true
            Label { objectName: "reviewHeading"; textFormat: Text.PlainText; text: panel.state.entryMode === "edit" ? "歌词编辑" : "逐句核对"; font.pixelSize: 15; font.weight: Font.DemiBold; color: Theme.ink }
                Flow {
                    id: candidateActions; objectName: "candidateActionRow"; visible: false
                    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1; spacing: 5
                    ActionButton { objectName: "alignmentOriginalButton"; text: "当前主轴"; compact: true; checked: !candidateBanner.candidate.active; onClicked: panel.cmd("alignment.preview", {enabled: false}) }
                    ActionButton { objectName: "alignmentCandidateButton"; text: "AI 建议（预览）"; compact: true; checked: candidateBanner.candidate.active === true; enabled: candidateBanner.candidate.available === true; onClicked: panel.cmd("alignment.preview", {enabled: true}) }
                    ActionButton { objectName: "alignmentRequestButton"; visible: !candidateBanner.compactActions; text: "AI 拆合建议…"; compact: true; enabled: !(panel.state.task || {}).running && !candidateBanner.candidate.active; tip: "调用文本 AI 分析跨句拆合与原文配对，确认前不会修改当前字幕"; onClicked: panel.cmd("alignment.batch") }
                    ActionButton { objectName: "alignmentForceRetryButton"; visible: !candidateBanner.compactActions && panel.state.alignmentRetryAvailable === true; text: "重发未知批次…"; compact: true; enabled: !(panel.state.task || {}).running; onClicked: panel.cmd("alignment.forceRetry") }
                    ActionButton { objectName: "alignmentExplainButton"; visible: !candidateBanner.compactActions; text: "对比与应用建议…"; compact: true; enabled: !!(panel.state.alignment || {}).plan; onClicked: panel.cmd("alignment.show") }
                    ActionButton { objectName: "alignmentDiscardButton"; visible: !candidateBanner.compactActions; text: "放弃建议"; compact: true; enabled: candidateBanner.candidate.hasCandidate === true; onClicked: panel.cmd("alignment.reject") }
                    ActionButton {
                        id: candidateMore; objectName: "alignmentMoreButton"
                        visible: candidateBanner.compactActions; text: "更多"; glyph: "more"; compact: true
                        tip: "请求、对比应用或放弃 AI 拆合建议"
                        onClicked: candidateMenu.showMenu()
                        MotionMenu {
                            id: candidateMenu; objectName: "alignmentCandidateMenu"
                            y: candidateMore.height + 3; ownerActive: panel.visible && candidateMore.visible
                            MenuItem { visible: false; height: 0; objectName: "alignmentRequestMenuItem"; text: "AI 拆合建议…"; enabled: !(panel.state.task || {}).running && !candidateBanner.candidate.active; onTriggered: panel.cmd("alignment.batch") }
                            MenuItem { objectName: "alignmentForceRetryMenuItem"; visible: panel.state.alignmentRetryAvailable === true; text: "重发结果未知的批次…"; enabled: !(panel.state.task || {}).running; onTriggered: panel.cmd("alignment.forceRetry") }
                            MenuItem { objectName: "alignmentExplainMenuItem"; text: "对比与应用建议…"; enabled: !!(panel.state.alignment || {}).plan; onTriggered: panel.cmd("alignment.show") }
                            MenuItem { objectName: "alignmentDiscardMenuItem"; text: "放弃建议"; enabled: candidateBanner.candidate.hasCandidate === true; onTriggered: panel.cmd("alignment.reject") }
                        }
                    }
                }

            Label { textFormat: Text.PlainText; text: "当前 " + (panel.current.number || "—") + "/" + (panel.state.rowCount || 0); font.pixelSize: 12; color: Theme.label }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
            ActionButton { objectName: "pairingWorkbenchButton"; text: panel.state.pairingLabel || "与翻译配对"; compact: true; enabled: panel.editable && !(panel.state.task || {}).running; onClicked: panel.cmd("pairing.workbench") }
            ActionButton { objectName: "reviewFullWidthButton"; text: "全宽"; compact: true; tip: "展开全宽表格，不改变工程或配对关系"; onClicked: panel.fullWidthRequested() }
            ActionButton { id: operationsAnchor; objectName: "reviewOperationsButton"; glyph: "more"; tip: "更多编辑操作 · F" + panel.splitFrame + " · " + SplitTarget.range(panel.current); compact: true; onClicked: operations.showMenu() }
        }
        Flow {
            Layout.fillWidth: true; spacing: 4
            ReviewTaskStatus { controller:panel.controller; state:panel.state; width:implicitWidth; height:implicitHeight }
            Check { objectName: "unconfirmedFilter"; visible: panel.editable; text: "待确认"; checked: (panel.state.view || {}).unconfirmedOnly === true; font.pixelSize: 11; onToggled: panel.cmd("view.filter", {unconfirmedOnly: checked}) }
            ActionButton { objectName: "reviewTimeFormat"; text: panel.showTimecodes ? "分:秒" : "帧号"; compact: true; tip: "切换列表时间格式"; onClicked: panel.timeFormatRequested() }
            ActionButton { id: addBlankAnchor; objectName: "reviewAddBlankButton"; visible: panel.editable; text: "+句"; compact: true; tip: "在当前句之前或之后新建空白句"; onClicked: { if (panel.hasCurrent) addBlankMenu.showMenu(); else panel.cmd("row.add", {}) }
                MotionMenu { id: addBlankMenu; objectName: "reviewAddBlankMenu"; ownerActive: panel.visible && panel.editable
                    MenuItem { objectName: "reviewAddBefore"; text: "在当前句之前"; onTriggered: panel.cmd("row.add", {before: panel.current.cueId}) }
                    MenuItem { objectName: "reviewAddAfter"; text: "在当前句之后"; onTriggered: panel.cmd("row.add", {after: panel.current.cueId}) }
                }
            }
            ActionButton { visible: !!panel.state.candidateReadOnly; text: "返回主时间轴"; compact: true; onClicked: panel.controller.switchCandidateSource("main") }
        }
        RowLayout {
            id: pendingControls; objectName: "reviewPendingText"
            Layout.fillWidth:true; Layout.minimumHeight:27; Layout.maximumHeight:27
            visible: panel.editable && !!panel.pendingAction.leftId
            spacing:5
            Label {
                Layout.fillWidth: true; elide:Text.ElideRight; Layout.maximumHeight:27
                text: "待分配歌词 · 第 " + (panel.pendingAction.leftNumber || "") + " / " + (panel.pendingAction.rightNumber || "") + " 段"
                color: Theme.label; font.pixelSize: 11
            }
                ActionButton {
                    objectName: "reviewAllocateSplitText"; text: "分配文字"; compact: true
                    enabled: !!panel.pendingAction.canAllocate
                    onClicked: panel.cmd("textAllocation.begin", panel.pendingAction)
                    tip: "点选字间切分位置，将歌词分给前后两段"
                }
                ActionButton {
                    objectName: "reviewKeepSplitText"; text: "不拆分文字"; compact: true
                    enabled: !!panel.pendingAction.canKeep
                    onClicked: panel.cmd("textAllocation.keep", panel.pendingAction)
                    tip: "文字保留在前一段，后一段留空（可撤销）"
                }
            Label { Layout.maximumWidth:80; elide:Text.ElideRight; visible: !!panel.pendingAction.error; text: panel.pendingAction.error || ""; color: Theme.rose; font.pixelSize: 11; ToolTip.visible: pendingErrorHover.hovered; ToolTip.text: text; HoverHandler { id: pendingErrorHover } }
        }
        Rectangle {
            id: candidateBanner
            objectName: "candidatePreviewBanner"; visible: false
            property var candidate: panel.state.alignmentPreview || ({})
            readonly property bool compactActions: candidateActions.width < 560
            Layout.fillWidth: true; Layout.preferredHeight: candidateBar.implicitHeight + 12
            radius: 7
            color: candidate.active ? Theme.amberBg : Theme.slab
            ColumnLayout {
                id: candidateBar
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 6; spacing: 4
                Label { id: candidateStatus; objectName: "alignmentCandidateStatus"
                    // Reserve one font-based line independent of the reason's glyphs.
                    // Two pixels of leading protect fallback-font ascenders/descenders.
                    readonly property int lineHeightBudget: Math.ceil(candidateStatusMetrics.height) + 2
                    Layout.minimumHeight: lineHeightBudget; Layout.preferredHeight: lineHeightBudget; Layout.maximumHeight: lineHeightBudget
                    verticalAlignment: Text.AlignVCenter
                    FontMetrics { id: candidateStatusMetrics; font: candidateStatus.font }
                    Layout.fillWidth: true; textFormat: Text.PlainText; text: panel.sourceCandidate ? "当前为只读预览 · 可在「更多」里对比并应用结果" : (candidateBanner.candidate.active ? "正在预览 AI 建议（尚未应用到主时间轴）。 " : "当前显示主时间轴。 ") + (candidateBanner.candidate.reason || ""); color: Theme.label; elide: Text.ElideRight; font.pixelSize: 11
                    ToolTip.visible: candidateStatusHover.hovered
                    ToolTip.text: text
                    HoverHandler { id: candidateStatusHover }
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            // Pending split actions borrow list space instead of moving the
            // player/timeline splitter during consecutive razor clicks.
            // Reserve a selected row plus a clickable neighbor before a split.
            // The same budget absorbs pending controls without growing the panel.
            Layout.minimumHeight: Math.max(38, 76 - (pendingControls.visible ? pendingControls.implicitHeight + reviewContents.spacing : 0))
            color: "transparent"; radius: 8; border.color: Theme.lineSoft
            ListView {
                id: list
                objectName: "reviewList"
                readonly property real panelY: reviewContents.y + parent.y + y
                readonly property real paintFirst: contentY + Math.max(0, panel.viewportTop - panelY)
                readonly property real paintLast: contentY + Math.min(height, panel.viewportTop + panel.viewportHeight - panelY)
                anchors.fill: parent; anchors.margins: 1
                clip: true; spacing: 2
                model: panel.controller ? panel.controller.rows : null
                boundsBehavior: Flickable.StopAtBounds
                currentIndex: panel.selectedIndex
                highlightFollowsCurrentItem: false
                highlightMoveDuration: Theme.motionEnabled ? 180 : 0
                onCurrentItemChanged: panel.scheduleViewport()
                onHeightChanged: panel.scheduleViewport()
                onWidthChanged: panel.scheduleViewport()
                onContentHeightChanged: panel.scheduleViewport()
                onContentYChanged: panel.scheduleViewport()
                onMovementStarted: panel.manualScroll()
                WheelHandler {
                    target: null
                    blocking: false
                    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                    onWheel: event => { panel.manualScroll(); event.accepted = false }
                }
                ScrollBar.vertical: ScrollBar {
                    objectName: "reviewScrollBar"
                    onPressedChanged: if (pressed) panel.manualScroll()
                }
                delegate: Rectangle {
                    id: row
                    required property int index
                    required property string cueId
                    required property string chinese
                    required property string textRole
                    required property string textRoleLabel
                    required property string source
                    required property string ocr
                    required property int firstFrame
                    required property int lastFrame
                    required property bool confirmed
                    required property bool locked
                    required property bool overlap
                    required property string speakerName
                    required property string speakerColor
                    required property string styleName
                    required property bool specialStyle
                    required property real cps
                    required property bool cpsWarning
                    required property string timeLabel
                    required property int number
                    objectName: "cueRow_" + cueId
                    property bool selected: panel.current.cueId === cueId
                    readonly property bool multiSelected: (panel.state.multiSelectedIds || []).indexOf(cueId) >= 0
                    readonly property bool splitEligible: panel.editable && SplitTarget.eligible({firstFrame: firstFrame, lastFrame: lastFrame, locked: locked}, panel.splitFrame)
                    readonly property bool showSplitTarget: panel.editable && ((drop.containsDrag && drop.validFrame) || (row.editing && (panel.state.markers || []).length > 0 && splitEligible))
                    property bool editing: false
                    onSelectedChanged: if (!selected) endEditing()
                    onLockedChanged: if (locked) endEditing()
                    function beginEditing(x, y) {
                        if (!panel.editable) { panel.feedback("当前为只读预览，请先通过「对比与应用」应用到主时间轴后再编辑"); return }
                        if (locked) { panel.feedback("该句已锁定，请先解锁后再编辑"); return }
                        if (!selected) panel.cmd("row.select", {id: cueId})
                        panel.stopAutoScroll(true)
                        editing = true
                        editor.forceActiveFocus(Qt.ShortcutFocusReason)
                        if (x !== undefined && y !== undefined) editor.cursorPosition = editor.positionAt(x, y)
                        panel.editingStarted(editor)
                    }
                    function endEditing() {
                        if (editor.activeFocus) panel.forceActiveFocus()
                        editing = false
                    }
                    onChineseChanged: {
                        if (!editor.inputMethodComposing && editor.text !== chinese) {
                            var caret = editor.cursorPosition
                            editor.syncing = true
                            editor.text = chinese
                            editor.cursorPosition = Math.min(caret, editor.length)
                            editor.syncing = false
                        }
                    }
                    width: list.width
                    readonly property real targetRowHeight: Math.max(36, editor.contentHeight + 10)
                    readonly property bool heightAnimating: rowHeight.running
                    implicitHeight: rowHeight.value
                    height: implicitHeight
                    clip: true
                    // Newly created software nodes can lose an empty ancestor
                    // clip. Do not paint wholly off-viewport rows. Opacity keeps
                    // editor focus/composition alive; no model or height changes.
                    // Partly visible rows still use the existing nested clips.
                    opacity: !panel.controller || !panel.controller.softwareRendering ||
                             (list.paintLast > list.paintFirst && y + height > list.paintFirst && y < list.paintLast) ? 1 : 0
                    MotionNumber {
                        id: rowHeight; targetValue: row.targetRowHeight
                        duration: 160; easingType: Easing.OutQuart
                        // Preedit must remain fully visible, not wait for a tween.
                        motion: Theme.motionEnabled && !row.editing && !editor.inputMethodComposing
                    }
                    radius: 7
                    color: rowColor.value
                    MotionColor { id: rowColor; targetValue: (row.selected || row.multiSelected) ? "#F3EEFC" : row.showSplitTarget ? "#EAF6EF" : hover.hovered ? "#FAF8FE" : "transparent"; duration: 150 }
                    // Selection remains distinct from overlap and split-target indicators.
                    Rectangle { objectName: "selectedCueAccent_" + row.cueId; visible: !!row.speakerColor || row.selected; x: 0; y: 5; width: 3; height: parent.height - 10; radius: 1.5; color: row.speakerColor || Theme.lilac }
                    border.width: row.showSplitTarget ? 1 : 0
                    border.color: Theme.green
                    HoverHandler { id: hover }
                    Rectangle { visible: row.overlap; x: row.selected ? 5 : 0; y: 7; width: 3; height: parent.height - 14; radius: 2; color: Theme.rose }
                    MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton; onClicked: mouse => { if(mouse.button===Qt.RightButton) { if(!row.multiSelected && !row.selected)panel.selectRow(row.cueId,0); lyricMenu.popup(row,mouse.x,mouse.y) } else panel.selectRow(row.cueId, mouse.modifiers) } }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 8
                        Label { textFormat: Text.PlainText; text: row.number; Layout.preferredWidth: 23; horizontalAlignment: Text.AlignHCenter; color: row.selected ? Theme.lilacDark : Theme.muted; font.pixelSize: 11 }
                        ReviewBadge { objectName: "rowSpeaker_" + row.cueId; maximumWidth: 90; visible: !!row.speakerName; text: row.speakerName; property color speakerInk: row.speakerColor || Theme.label; foreground: Qt.darker(speakerInk,1.55); fill: Qt.tint(Theme.paper, Qt.rgba(speakerInk.r,speakerInk.g,speakerInk.b,.24))
                            MouseArea { anchors.fill: parent; enabled: panel.editable; onClicked: mouse => { if (mouse.modifiers & (Qt.ControlModifier|Qt.ShiftModifier)) panel.selectRow(row.cueId,mouse.modifiers); else { if (!row.multiSelected && !row.selected) panel.selectRow(row.cueId,0); speakerField.openNames() } } }
                        }
                        Item {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            TextArea {
                                textFormat: TextEdit.PlainText
                                id: editor
                                objectName: "chineseEditor_" + row.cueId
                                property bool syncing: false
                                anchors.fill: parent
                                font.family: Theme.fontFamily; font.pixelSize: 15
                                color: Theme.text
                                padding: 3; topPadding: 4; bottomPadding: 4
                                verticalAlignment: TextEdit.AlignVCenter
                                wrapMode: TextEdit.Wrap
                                selectByMouse: true
                                readOnly: !panel.editable || !row.editing || !row.selected || row.locked
                                activeFocusOnPress: row.editing
                                activeFocusOnTab: row.editing
                                placeholderText: panel.editable ? (row.textRole === "translation" ? "双击或按 F2 填写中文译文" : "双击或按 F2 编辑原文歌词") : "暂无文字"
                                placeholderTextColor: Theme.faint
                                Accessible.name: "第 " + row.number + " 句" + row.textRoleLabel
                                background: Rectangle { color: "transparent"; border.width: 0 }
                                Binding { target: editor; property: "text"; value: row.chinese; when: !editor.activeFocus && !editor.inputMethodComposing; restoreMode: Binding.RestoreNone }
                                Component.onCompleted: text = row.chinese
                                function commit() { if (!syncing && activeFocus && !inputMethodComposing && text !== row.chinese && !readOnly) panel.cmd("row.edit", {id: row.cueId, text: text}) }
                                onTextChanged: commit()
                                onInputMethodComposingChanged: { if (inputMethodComposing) { panel.stopAutoScroll(true); panel.splitCursor = null } panel.cmd("editor.activity", {id: row.cueId, active: activeFocus, composing: inputMethodComposing}); if (!inputMethodComposing) { commit(); panel.scheduleViewport() } }
                                onActiveFocusChanged: {
                                    panel.cmd("editor.activity", {id: row.cueId, active: activeFocus, composing: inputMethodComposing})
                                    if (activeFocus && row.editing) { panel.stopAutoScroll(true); panel.activeEditor = editor }
                                    else if (!activeFocus) {
                                        if (row.editing && !inputMethodComposing)
                                            panel.splitCursor = {cueId: row.cueId, text: text, offset: cursorPosition}
                                        row.editing = false
                                        if (panel.activeEditor === editor) panel.activeEditor = null
                                    }
                                }
                                Component.onDestruction: if (activeFocus && panel.controller) panel.cmd("editor.activity", {id: row.cueId, active: false, composing: false})
                                onCursorPositionChanged: if (activeFocus) panel.stopAutoScroll(true)
                                Keys.onPressed: event => {
                                    panel.stopAutoScroll(true)
                                    // Unhandled Alt+arrows otherwise bubble into ListView's
                                    // navigation and can steal focus from an IME preedit.
                                    if (event.modifiers & Qt.AltModifier && (event.key === Qt.Key_Up || event.key === Qt.Key_Down)) { event.accepted = true; return }
                                    if (!inputMethodComposing && event.key === Qt.Key_Escape) { row.endEditing(); event.accepted = true; return }
                                    if (!inputMethodComposing && event.modifiers & Qt.ControlModifier && event.key === Qt.Key_Z) {
                                        panel.cmd(event.modifiers & Qt.ShiftModifier ? "history.redo" : "history.undo"); event.accepted = true; return
                                    }
                                    if (!inputMethodComposing && event.modifiers & Qt.ControlModifier && event.key === Qt.Key_Y) {
                                        panel.cmd("history.redo"); event.accepted = true; return
                                    }
                                    if (!inputMethodComposing && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && event.modifiers & Qt.ControlModifier) { panel.cmd("row.confirmNext", {id: row.cueId}); event.accepted = true }
                                    if (event.key === Qt.Key_F6) { if (inputMethodComposing) panel.feedback("请先完成输入法选词，再切分歌词"); else panel.splitAtCursor(); event.accepted = true }
                                }
                            }
                            MouseArea {
                                objectName: "lyricSelect_" + row.cueId
                                anchors.fill: editor
                                enabled: !row.editing
                                cursorShape: row.editing ? Qt.IBeamCursor : Qt.ArrowCursor
                                onClicked: mouse => panel.selectRow(row.cueId, mouse.modifiers)
                                onDoubleClicked: mouse => row.beginEditing(mouse.x, mouse.y)
                            }
                            MouseArea {
                                objectName: "lyricContext_" + row.cueId
                                anchors.fill: editor; z: 10; acceptedButtons: Qt.RightButton; preventStealing: true
                                cursorShape: row.editing ? Qt.IBeamCursor : Qt.ArrowCursor
                                onClicked: mouse => {
                                    if (editor.inputMethodComposing) { panel.feedback("请先完成输入法选词"); return }
                                    if (!row.selected && (panel.state.multiSelectedIds || []).indexOf(row.cueId) < 0) panel.selectRow(row.cueId)
                                    lyricMenu.savedOffset = row.editing ? editor.cursorPosition : editor.positionAt(mouse.x, mouse.y)
                                    lyricMenu.popup(editor, mouse.x, mouse.y)
                                }
                            }
                            Menu {
                                id: lyricMenu; objectName: "lyricContextMenu_" + row.cueId
                                property int savedOffset: 0
                                MenuItem { text:row.locked ? "解锁本句" : "锁定本句"; enabled:panel.editable; onTriggered:panel.cmd("row.lock",{id:row.cueId,value:!row.locked}) }
                                Connections { target: panel; function onVisibleChanged() { if (!panel.visible) lyricMenu.close() } }
                                MenuItem { objectName: "lyricAddBefore_" + row.cueId; text: "在此句之前新增"; visible: panel.editable; height: visible ? implicitHeight : 0; enabled: panel.editable; onTriggered: panel.cmd("row.add", {before: row.cueId}) }
                                MenuItem { objectName: "lyricAddAfter_" + row.cueId; text: "在此句之后新增"; visible: panel.editable; height: visible ? implicitHeight : 0; enabled: panel.editable; onTriggered: panel.cmd("row.add", {after: row.cueId}) }
                                MenuItem { objectName: "lyricEditAction_" + row.cueId; visible: panel.editable; height: visible ? implicitHeight : 0; text: "编辑"; enabled: panel.editable && !row.locked; onTriggered: { row.beginEditing(); editor.cursorPosition = lyricMenu.savedOffset } }
                                MenuItem { objectName: "lyricCopyAction_" + row.cueId; text: "复制"; enabled: editor.length > 0; onTriggered: { var a = editor.selectionStart, b = editor.selectionEnd; if (a === b) editor.selectAll(); editor.copy(); editor.select(a, b) } }
                                MenuItem { objectName: "lyricPasteAction_" + row.cueId; visible: panel.editable; height: visible ? implicitHeight : 0; text: "粘贴"; enabled: panel.editable && !row.locked; onTriggered: { row.beginEditing(); editor.cursorPosition = lyricMenu.savedOffset; editor.paste() } }
                                MenuItem { objectName: "lyricSplitAction_" + row.cueId; visible: panel.editable; height: visible ? implicitHeight : 0; text: "分割歌词"; enabled: panel.editable && !row.locked; onTriggered: { panel.splitCursor = {cueId: row.cueId, text: editor.text, offset: lyricMenu.savedOffset}; panel.splitAtCursor() } }
                                MenuItem { text:"选中全部同说话人句子"; enabled:panel.editable; onTriggered:panel.cmd("review.selectSameSpeaker",{id:row.cueId}) }
                                Menu { title: "指定说话人"; enabled: panel.editable
                                    MenuItem { text: "清除说话人"; onTriggered: panel.cmd("review.assignSpeaker",{}) }
                                    Repeater { model: panel.state.reviewSpeakers || []; MenuItem { required property var modelData; text: modelData.name; onTriggered: panel.cmd("review.assignSpeaker",{id:modelData.id}) } }
                                }
                                Menu { title: "指定样式"; enabled: panel.editable
                                    Repeater { model: (panel.state.reviewStyles || {}).items || []; MenuItem { required property var modelData; text: modelData.name; onTriggered: panel.cmd("review.assignStyle",{id:modelData.id}) } }
                                }
                                MenuItem { objectName: "lyricMergeAction_" + row.cueId; text: "合并选中"; visible: panel.editable && (panel.state.multiSelectedIds || []).length >= 2; height: visible ? implicitHeight : 0; enabled: visible && panel.editable && !row.locked; onTriggered: panel.cmd("row.merge", {id: row.cueId, ids: panel.state.multiSelectedIds}) }
                            }
                            DropArea {
                                id: drop
                                enabled: panel.editable
                                objectName: "markerDrop_" + row.cueId
                                anchors.fill: editor
                                keys: ["lyria-fixed-marker"]
                                property int splitOffset: 0
                                property int markerFrame: 0
                                property real lastX: 0
                                property real lastY: 0
                                property int rawOffset: 0
                                property bool validFrame: false
                                property rect caret: Qt.rect(0,0,1,18)
                                function update(drag) {
                                    markerFrame = drag.source.frame
                                    lastX = drag.x; lastY = drag.y
                                    rawOffset = editor.positionAt(drag.x, drag.y)
                                    splitOffset = panel.controller.graphemeBoundary(editor.text, rawOffset)
                                    caret = editor.positionToRectangle(splitOffset)
                                    validFrame = !editor.inputMethodComposing && SplitTarget.eligible({firstFrame: row.firstFrame, lastFrame: row.lastFrame, locked: row.locked}, markerFrame) && splitOffset > 0 && splitOffset < editor.length
                                }
                                onEntered: drag => { update(drag) }
                                onPositionChanged: drag => update(drag)
                                onDropped: event => {
                                    if (editor.inputMethodComposing) { panel.feedback("请先完成输入法选词，再切分歌词"); event.accepted = false; return }
                                    update(event)
                                    // The backend owns validation and the immutable marker remains in place.
                                    panel.cmd("row.split", {id: row.cueId, frame: markerFrame, offsetUtf16: splitOffset})
                                    event.acceptProposedAction()
                                }
                                Rectangle {
                                    visible: drop.containsDrag
                                    x: drop.caret.x; y: drop.caret.y
                                    width: 2; height: Math.max(18, drop.caret.height)
                                    color: drop.validFrame ? Theme.lilac : Theme.rose
                                    Badge { text: "F" + drop.markerFrame; x: -12; y: -26; foreground: drop.validFrame ? Theme.lilacDark : Theme.rose; fill: drop.validFrame ? Theme.lilacSoft : Theme.roseBg }
                                }
                            }
                        }
                        ReviewBadge { objectName: "rowStyle_" + row.cueId; maximumWidth: 100; visible: row.specialStyle; glyph: "spark"; text: row.styleName; foreground: Theme.lilacDark; fill: Theme.lilacSoft
                            MouseArea { anchors.fill: parent; enabled: panel.editable; onClicked: mouse => { if (mouse.modifiers & (Qt.ControlModifier|Qt.ShiftModifier)) panel.selectRow(row.cueId,mouse.modifiers); else { if (!row.multiSelected && !row.selected) panel.selectRow(row.cueId,0); stylePicker.popup.open() } } }
                        }
                        ReviewBadge { maximumWidth: 90; visible: row.cpsWarning; text: "! " + row.cps + "字/s"; foreground: Theme.amber; fill: Theme.amberBg }
                        Label { textFormat: Text.PlainText; text: row.firstFrame > 0 && row.lastFrame > 0 ? (panel.showTimecodes ? row.timeLabel : row.firstFrame + "–" + row.lastFrame) : "未打轴"; Layout.preferredWidth: panel.showTimecodes ? 100 : 76; horizontalAlignment: Text.AlignHCenter; color: Theme.label; font.pixelSize: 11 }
                        Label {
                            id: statusLabel; objectName: "cueStatus_" + row.cueId
                            text: row.confirmed ? "✓" : "○"; Layout.preferredWidth: 14
                            property color fill: row.overlap ? Theme.rose : row.confirmed ? Theme.green : Theme.amber
                            color: statusColor.value; Accessible.name: row.confirmed ? "已确认" : "待确认"
                            MotionColor { id: statusColor; targetValue: statusLabel.fill; duration: 120 }
                        }
                        ToolButton {
                            objectName: "cueLock_" + row.cueId
                            Layout.preferredWidth: 19; Layout.preferredHeight: 22
                            enabled: panel.editable
                            Accessible.name: row.locked ? "解锁本句" : "锁定本句"
                            ToolTip.visible: hovered; ToolTip.text: Accessible.name
                            contentItem: Image { source: "../assets/icons/lock.svg"; fillMode: Image.PreserveAspectFit; opacity: row.locked || parent.hovered ? 1 : .28; sourceSize.width: 13; sourceSize.height: 13 }
                            background: Rectangle { color: parent.hovered ? Theme.slab : "transparent"; radius: 4 }
                            onClicked: panel.cmd("row.lock",{id:row.cueId,value:!row.locked})
                        }

                    }
                    Label { textFormat: Text.PlainText;
                        visible: drop.containsDrag && !drop.validFrame
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 5
                        text: row.locked ? "该句已锁定，请先解锁" : editor.inputMethodComposing ? "请先完成输入法选词" : drop.markerFrame <= row.firstFrame || drop.markerFrame > row.lastFrame ? "标记点 F" + drop.markerFrame + "：" + SplitTarget.range({firstFrame: row.firstFrame, lastFrame: row.lastFrame}) : "请拖到两个字之间（句首或句尾不可切分）"
                        color: Theme.rose; font.pixelSize: 11; elide: Text.ElideRight
                    }
                }
                Label { textFormat: Text.PlainText; anchors.centerIn: parent; visible: list.count === 0; text: panel.state.entryMode === "edit" ? "暂无歌词\n可导入歌词，或点击上方「新建空白句」" : "暂无字幕\n可导入歌词、运行画面 OCR，或点击上方「新建空白句」"; horizontalAlignment: Text.AlignHCenter; lineHeight: 1.6; color: Theme.muted; font.pixelSize: 12 }
            }
        }
        Rectangle {
            objectName: "reviewEvidencePanel"
            visible: !!panel.state.hasSourceEvidence
            // Cue changes must never resize the player/timeline workspace.
            // Long evidence stays fully reachable inside its own scroll viewport.
            Layout.fillWidth: true; Layout.preferredHeight: panel.evidenceCollapsed ? 24 : 48
            radius: 9; color: Theme.slab
            Label { anchors.left: parent.left; anchors.leftMargin: 6; anchors.verticalCenter: parent.verticalCenter; visible: panel.evidenceCollapsed; text: "参考原文 / 画面 OCR"; color: Theme.label; font.pixelSize: 11 }
            ActionButton { anchors.right: parent.right; z: 2; text: panel.evidenceCollapsed ? "展开" : "收起"; compact: true; onClicked: panel.evidenceCollapsed = !panel.evidenceCollapsed }
            ScrollView {
                visible: !panel.evidenceCollapsed
                id: evidenceScroll; objectName: "reviewEvidenceScroll"
                anchors.fill: parent; anchors.margins: 4; anchors.rightMargin: 48; clip: true
                contentWidth: availableWidth
                contentHeight: referenceLayout.implicitHeight
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    id: referenceLayout
                    width: evidenceScroll.availableWidth; spacing: 2
                    RowLayout {
                        Layout.fillWidth: true; spacing: 16
                        ColumnLayout {
                            visible: !panel.current.sourceFromOcr && !!panel.current.source
                            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
                            Label { textFormat: Text.PlainText; objectName: "reviewSourceOriginLabel"; text: "参考原文"; color: Theme.muted; font.pixelSize: 11 }
                            Label { textFormat: Text.PlainText; objectName: "reviewSourceEvidence"; text: panel.current.source || (panel.sourceCandidate ? "未关联参考原文" : "未导入原文"); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.text; font.pixelSize: 12; Accessible.name: "参考原文" }
                        }
                        ColumnLayout {
                            visible: !!panel.current.ocr
                            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
                            Label { textFormat: Text.PlainText; text: "画面 OCR"; color: Theme.muted; font.pixelSize: 11 }
                            Label { textFormat: Text.PlainText; objectName: "reviewOcrEvidence"; text: panel.current.ocr || (panel.sourceCandidate ? "无 OCR 识别结果" : "暂无 OCR 结果"); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 12; Accessible.name: "OCR 识别结果" }
                        }
                    }
                    Rectangle { visible: false; Layout.fillWidth: true; height: 1; color: Theme.line }
                    RowLayout {
                        visible: false; Layout.fillWidth: true
                        Image { source: "../assets/icons/spark.svg"; Layout.preferredWidth: 13; Layout.preferredHeight: 13 }
                        Label { textFormat: Text.PlainText; text: panel.current.aiNote || "暂无 AI 核对记录"; color: Theme.label; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        ActionButton { text: "AI 核对本句"; visible: panel.editable; tip: "用 AI 核对本句译文与原文配对（不改动时间轴）"; compact: true; enabled: panel.hasCurrent && panel.capabilities["row.align"] === true; onClicked: panel.cmd("row.align", {id: panel.current.cueId}) }
                    }
                }
            }
        }
        RowLayout {
            id: currentForm; objectName: "currentSentenceProperties"
            Layout.fillWidth: true; spacing: 4; visible: panel.editable
            enabled: panel.hasCurrent && !panel.current.locked
            Label { visible: (panel.state.multiSelectedIds || []).length > 1; text: "已选 " + (panel.state.multiSelectedIds || []).length + " 句"; color: Theme.label; font.pixelSize: 11 }
            ChoiceBox {
                id: stylePicker; objectName: "visibleStyleAction"; Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.minimumWidth: 60; implicitHeight: 28
                property var styles: (panel.state.reviewStyles || {}).items || []
                model: styles.concat([{name:"管理样式…", id:"@manage"}]); textRole: "name"
                displayText: panel.current.styleName || "默认样式"
                currentIndex: styles.findIndex(item => item.id === panel.current.styleId)
                onActivated: index => { if (index === styles.length) panel.cmd("style.manager", {batch:true}); else panel.cmd("review.assignStyle",{id:styles[index].id}) }
            }
            ActionButton { objectName: "editCurrentStyle"; text: "编辑"; compact: true; tip: "编辑当前句使用的样式"; onClicked: panel.cmd("style.editCurrent") }
            SpeakerField {
                id: speakerField; objectName: "visibleSpeakerAction"; inputName: "currentSpeakerInput"; menuName: "reviewSpeakerMenu"
                Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.minimumWidth: 96
                property var selection: panel.state.speakerSelection || ({})
                scopeKey: JSON.stringify([(panel.state.project || {}).id, panel.current.cueId, panel.state.multiSelectedIds])
                nameValue: selection.name || ""; nameColor: selection.color || ""; mixedCount: selection.mixed ? selection.count : 0
                choices: panel.state.reviewSpeakers || []
                onSubmitted: value => panel.cmd("review.assignSpeaker", {name:value})
            }
        }
        RowLayout {
            objectName: "reviewTimingStrip"; visible: panel.editable
            Layout.fillWidth: true; Layout.minimumHeight: 30; Layout.maximumHeight: 30; spacing: 3
            Row {
                spacing: 4
                Label { textFormat: Text.PlainText; text: panel.showTimecodes ? "入点（帧）" : "入点"; color: Theme.label; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                Field {
                    id: firstField; width: 62; font.features: ({"tnum": 1}); implicitHeight: 30; horizontalAlignment: Text.AlignHCenter
                    objectName: "firstFrameField"
                    property string editingId: ""
                    property int editingFrame: 0
                    enabled: panel.hasCurrent && !panel.current.locked
                    validator: IntValidator { bottom: 1; top: Math.max(1, panel.media.totalFrames || 2147483647) }
                    placeholderText: "待定"; Accessible.name: "当前句入点帧"
                    Binding { target: firstField; property: "text"; value: panel.current.firstFrame > 0 ? String(panel.current.firstFrame) : ""; when: !firstField.activeFocus; restoreMode: Binding.RestoreNone }
                    onActiveFocusChanged: {
                        if (activeFocus) { editingId = panel.current.cueId || ""; editingFrame = panel.current.firstFrame || 0 }
                        if (editingId) panel.cmd("editor.activity", {id: "first:" + editingId, active: activeFocus, composing: false})
                    }
                    onEditingFinished: {
                        if (editingId && text && acceptableInput && Number(text) !== editingFrame) panel.cmd("row.setBoundary", {id: editingId, edge: "first", frame: Number(text)})
                        editingFrame = Number(text); focus = false
                        text = panel.current.firstFrame > 0 ? String(panel.current.firstFrame) : ""
                    }
                }
                ActionButton { objectName: "firstSetCurrentButton"; text: "当前"; implicitWidth: 42; tip: "将当前帧设为本句入点（快捷键 [）"; Accessible.name: "将当前帧设为本句入点，快捷键左方括号"; compact: true; enabled: panel.hasCurrent && !panel.current.locked && panel.media.ready; onClicked: panel.cmd("row.setCurrent", {edge: "first"}) }
            }
            Row {
                spacing: 4
                Label { textFormat: Text.PlainText; text: panel.showTimecodes ? "出点（帧）" : "出点"; color: Theme.label; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                Field {
                    id: lastField; width: 62; font.features: ({"tnum": 1}); implicitHeight: 30; horizontalAlignment: Text.AlignHCenter
                    objectName: "lastFrameField"
                    property string editingId: ""
                    property int editingFrame: 0
                    enabled: panel.hasCurrent && !panel.current.locked
                    validator: IntValidator { bottom: 1; top: Math.max(1, panel.media.totalFrames || 2147483647) }
                    placeholderText: "待定"; Accessible.name: "当前句出点帧"
                    Binding { target: lastField; property: "text"; value: panel.current.lastFrame > 0 ? String(panel.current.lastFrame) : ""; when: !lastField.activeFocus; restoreMode: Binding.RestoreNone }
                    onActiveFocusChanged: {
                        if (activeFocus) { editingId = panel.current.cueId || ""; editingFrame = panel.current.lastFrame || 0 }
                        if (editingId) panel.cmd("editor.activity", {id: "last:" + editingId, active: activeFocus, composing: false})
                    }
                    onEditingFinished: {
                        if (editingId && text && acceptableInput && Number(text) !== editingFrame) panel.cmd("row.setBoundary", {id: editingId, edge: "last", frame: Number(text)})
                        editingFrame = Number(text); focus = false
                        text = panel.current.lastFrame > 0 ? String(panel.current.lastFrame) : ""
                    }
                }
                ActionButton { objectName: "lastSetCurrentButton"; text: "当前"; implicitWidth: 42; tip: "将当前帧设为本句出点（快捷键 ]）"; Accessible.name: "将当前帧设为本句出点，快捷键右方括号"; compact: true; enabled: panel.hasCurrent && !panel.current.locked && panel.media.ready; onClicked: panel.cmd("row.setCurrent", {edge: "last"}) }
            }
            Label {
                objectName: "reviewDurationLabel"; Layout.fillWidth: true; Layout.minimumWidth: 74
                Layout.preferredWidth: 90; Layout.maximumHeight: 30; horizontalAlignment: Text.AlignHCenter
                text: panel.current.firstFrame > 0 ? (panel.current.lastFrame - panel.current.firstFrame + 1) + " 帧" : "未打轴"
                font.pixelSize: 11; font.features: ({"tnum": 1}); color: Theme.label; elide: Text.ElideRight
                ToolTip.visible: durationHover.hovered
                ToolTip.text: text + " · " + panel.relation(panel.previous, panel.current, "上句") + " · " + panel.relation(panel.current, panel.next, "下句")
                HoverHandler { id: durationHover }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.lineSoft }
        RowLayout {
            objectName: "reviewConfirmationStrip"; Layout.fillWidth: true; Layout.minimumHeight: 30; Layout.maximumHeight: 30; spacing: 4
            ActionButton {
                Layout.preferredWidth: 58; Layout.minimumWidth: 58; Layout.maximumWidth: 58
                objectName: "confirmCurrent"; visible: panel.editable; emphasized: true; text: panel.current.confirmed ? "已确认" : "确认"; compact: true; enabled: panel.hasCurrent
                property string pressedCueId: ""
                onPressed: pressedCueId = panel.current.cueId
                onCanceled: pressedCueId = ""
                onClicked: { var identity = pressedCueId || panel.current.cueId; pressedCueId = ""; panel.cmd("row.confirm", {id: identity, value: true}) }
                tip: "确认当前句，不改变播放位置"
            }
            ActionButton {
                objectName: "confirmAndNext"; visible: panel.editable; primary: true; text: "确认下一句"; compact: true; enabled: panel.hasCurrent
                property string pressedCueId: ""
                onPressed: pressedCueId = panel.current.cueId
                onCanceled: pressedCueId = ""
                onClicked: { var identity = pressedCueId || panel.current.cueId; pressedCueId = ""; panel.cmd("row.confirmNext", {id: identity}) }
                tip: "确认当前句并跳至下一句（Ctrl+Enter）"
            }
            ActionButton { text: "↑"; tip: "上一句"; compact: true; enabled: !!panel.previousVisible.cueId; onClicked: panel.cmd("row.select", {id: panel.previousVisible.cueId}) }
            ActionButton { text: "↓"; tip: "下一句"; compact: true; enabled: !!panel.nextVisible.cueId; onClicked: panel.cmd("row.select", {id: panel.nextVisible.cueId}) }
            Item { Layout.fillWidth:true }
            ActionButton { objectName: "deleteCurrentCueButton"; visible: panel.editable; destructive: true; text: "删除"; compact: true; enabled: panel.hasCurrent && !panel.current.locked; onClicked: panel.cmd("row.delete", {id: panel.current.cueId}); tip: "删除当前句（可撤销）" }
        }
    }
    MotionMenu {
        id: operations
        objectName: "reviewOperationsMenu"
        parent: operationsAnchor; x: operationsAnchor.width - width; y: operationsAnchor.height + 4
        ownerActive: panel.visible
        MenuItem { objectName: "reviewCurrentOcr"; text: "核对本轮 OCR 结果…"; visible: !!(panel.state.candidateTask || {}).resultId && panel.state.candidateTask.source === "ocr"; height: visible ? implicitHeight : 0; onTriggered: panel.controller.reopenOcrResult() }
        MenuItem { text: "对比与应用识别/打轴结果…"; enabled: !!panel.state.candidateComparisonAvailable; onTriggered: panel.controller.compareCandidates() }
        MenuItem { text: "切换/管理保留时间轴…"; enabled: !!panel.state.timelineSwitchAvailable; onTriggered: panel.controller.manageTimelines() }
        MenuItem { text: (panel.state.pairingLabel || "与翻译配对") + "…"; enabled: panel.editable; onTriggered: panel.cmd("pairing.workbench") }
        MenuItem { objectName: "lockCurrentCue"; text: panel.current.locked ? "解锁本句" : "锁定本句"; enabled: panel.editable && panel.hasCurrent; onTriggered: panel.cmd("row.lock",{id:panel.current.cueId,value:!panel.current.locked}) }
        MenuItem { text: "取消本句确认状态"; enabled: panel.editable && panel.hasCurrent && panel.current.confirmed === true; onTriggered: panel.cmd("row.confirm", {id: panel.current.cueId, value: false}) }
        MenuSeparator { }
        MenuItem { objectName: "splitAtSavedCursorAction"; text: "按光标与最近标记点切分（F6）"; enabled: panel.editable && panel.hasCurrent && !panel.current.locked; onTriggered: panel.splitAtCursor() }
        MenuItem { text: "与下一句合并"; enabled: !!panel.next.cueId && !panel.current.locked && panel.capabilities["row.merge"] === true; onTriggered: panel.cmd("row.merge", {id: panel.current.cueId}) }
        MenuItem { text: "恢复 OCR 原始出入点"; enabled: panel.hasCurrent && panel.capabilities["row.restoreDetected"] === true; onTriggered: panel.cmd("row.restoreDetected", {id: panel.current.cueId}) }
        MenuItem { text: "延伸填满相邻空隙"; enabled: panel.hasCurrent && !panel.current.locked && panel.capabilities["row.fillGap"] === true; onTriggered: panel.cmd("row.fillGap", {id: panel.current.cueId}) }
        MenuItem { text: "局部重新识别…"; enabled: panel.hasCurrent && panel.capabilities["row.rescan"] === true; onTriggered: panel.cmd("row.rescan", {id: panel.current.cueId}) }
        MenuItem { text: "复制本句文字（不带时间轴）"; enabled: panel.hasCurrent && panel.capabilities["row.repeat"] === true; onTriggered: panel.cmd("row.repeat", {id: panel.current.cueId}) }
        MenuSeparator { }
        MenuItem { visible: false; height: 0; text: "AI 单句参考翻译"; enabled: panel.hasCurrent && panel.capabilities["row.referenceTranslation"] === true; onTriggered: panel.cmd("row.referenceTranslation", {id: panel.current.cueId}) }
        MenuItem { visible: false; height: 0; objectName: "savedAiCurrentAction"; text: "查看本句历史 AI 建议…"; enabled: panel.hasCurrent && panel.capabilities["api.reviewSaved"] === true; onTriggered: panel.cmd("api.reviewSaved", {id: panel.current.cueId}) }
        MenuItem { visible: false; height: 0; text: "AI 跨句拆合建议…"; enabled: panel.capabilities["alignment.batch"] === true; onTriggered: panel.cmd("alignment.batch") }
    }
}
