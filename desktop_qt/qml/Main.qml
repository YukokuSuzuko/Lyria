import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: window
    objectName: "lyriaMainWindow"
    visible: false
    width: 1400; height: 940
    minimumWidth: 760; minimumHeight: 600
    title: "Lyria " + Qt.application.version + " · " + (startup ? "欢迎使用" : ((state.project || {}).name || "未命名工程")) + " — Lyria · Moonlit Lilac"
    color: Theme.canvas
    font.family: Theme.fontFamily
    font.pixelSize: 12
    // Tests/embedded hosts without the optional policy keep native decoration.
    readonly property var chrome: typeof windowChrome !== "undefined" ? windowChrome : null
    readonly property bool integratedTitlebar: !!chrome && chrome.enabled
    readonly property real resizeBorder: 0 // Resize hit regions overlay the flush opaque client edge.
    flags: Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint | (integratedTitlebar ? Qt.FramelessWindowHint : 0)
    readonly property var state: bridge ? bridge.uiState || ({}) : ({})
    readonly property var media: bridge ? bridge.media || ({}) : ({})
    readonly property var project: state.project || ({})
    readonly property var current: state.selection ? state.selection.current || ({}) : ({})
    readonly property var task: state.task || ({})
    readonly property var capabilities: state.capabilities || ({})
    readonly property string stage: state.stage || "import"
    readonly property bool narrow: width < 1040
    readonly property bool reviewWorkspace: !startup && workspace === "make" && stage === "review"
    readonly property var panelLayout: state.workspaceLayout || ({})
    readonly property bool previewShown: !reviewWorkspace || panelLayout.previewVisible !== false
    property bool reviewTimecodes: false
    readonly property bool reviewShown: !reviewWorkspace || panelLayout.reviewVisible !== false
    readonly property bool audioShown: panelLayout.audioVisible !== false
    readonly property bool timelineShown: panelLayout.timelineVisible !== false
    readonly property bool writableReview: reviewWorkspace && !state.candidateReadOnly && !(state.alignmentPreview || {}).active
    readonly property int shownPanelCount: (previewShown ? 1 : 0) + (reviewShown ? 1 : 0) + (audioShown ? 1 : 0) + (timelineShown ? 1 : 0)
    function togglePanel(key, value) { var change = {}; change[key] = value; cmd("view.layout", change) }
    function resetLayout() {
        cmd("view.layout", {reset: true})
        mainSplit.previewFraction = 0.52; verticalSplit.topFraction = 0.58
        verticalSplit.timelineResized = false; timeline.collapsed = false
        previewPanel.SplitView.preferredWidth = mainSplit.width * 0.52
        previewPanel.SplitView.preferredHeight = 420
        verticalSplit.restoreTimelineHeightBinding()
    }
    readonly property bool textFocus: !!activeFocusItem && ("cursorPosition" in activeFocusItem)
    readonly property bool audioControlFocus: !!activeFocusItem && activeFocusItem.objectName === "audioPositionSlider"
    readonly property bool timelineEdgeFocus: !!activeFocusItem && activeFocusItem.objectName.indexOf("cueEdge_") === 0
    readonly property bool windowControlFocus: !!activeFocusItem && ["windowMinimize", "windowMaximize", "windowClose"].indexOf(activeFocusItem.objectName) !== -1
    function canDeleteCue() {
        if ((state.alignmentPreview || {}).active || state.candidateReadOnly) return false
        if (!active || workspace !== "make" || stage !== "review" || !current.cueId) return false
        var item = activeFocusItem
        while (item) {
            if (item.objectName.indexOf("fixedMarker_") === 0) return false
            if ("cursorPosition" in item || ("inputMethodComposing" in item && item.inputMethodComposing)) return false
            if (item === workspaceArea) return true
            item = item.parent
        }
        return false
    }
    readonly property bool startup: state.startupVisible === true
    property bool fullWidthReview: false
    property string workspace: "make"
    property string previousWorkspace: "make"
    onWorkspaceChanged: {
        if (workspace !== "make") fullWidthReview = false
        releaseWorkspaceFocus()
        if (workspace !== "settings") previousWorkspace = workspace
    }
    onStageChanged: { releaseWorkspaceFocus(); if (stage !== "review") fullWidthReview = false }
    function releaseWorkspaceFocus() {
        if (!workspaceArea || !activeFocusItem) return
        var item = activeFocusItem
        while (item) {
            if (item === workspaceArea) { workspaceArea.forceActiveFocus(); return }
            item = item.parent
        }
    }
    function revealLyricCaret(editor) { /* Each editing panel owns its local viewport. */ }
    function reviewBrowseFocus() {
        if (timeline.interacting || timeline.keyboardEditing) return false
        if (startup || workspace !== "make" || stage !== "review" || !activeFocusItem) return false
        var item = activeFocusItem
        while (item) {
            if ("cursorPosition" in item || ("inputMethodComposing" in item && item.inputMethodComposing)) return false
            if (item === review || item === timeline || item.objectName === "fullWidthReviewWorkspace") return true
            item = item.parent
        }
        return false
    }
    property bool allowClose: false
    property string noticeText: ""
    property string noticeLevel: "info"
    onNoticeTextChanged: if (noticeText.length > 0) toast.displayedText = noticeText
    Binding { target: Theme; property: "motionEnabled"; value: (window.state.settings || {}).motion !== false }
    function cmd(name, payload) { return bridge.command(name, payload || {}) }
    function navigate(stage) { cmd("project.resume"); workspace = "make"; cmd("stage.set", {stage: stage}) }
    function showNotice(level, message) { noticeLevel = level; noticeText = message; toastTimer.restart() }
    Component.onCompleted: {
        if (chrome && chrome.prepareWindow) chrome.prepareWindow(window)
        visible = true
        if (chrome && chrome.applyWindowCorners) chrome.applyWindowCorners(window)
    }
    onVisibilityChanged: if (chrome && chrome.applyWindowCorners) chrome.applyWindowCorners(window)
    onClosing: close => { close.accepted = allowClose || bridge.requestClose() }
    Connections {
        target: bridge
        ignoreUnknownSignals: true
        function onNotice(level, message) { window.showNotice(level, message) }
        function onMessage(message) { window.showNotice("error", message) }
        function onCloseDialogRequested(details) { closeDialog.open() }
        function onQuitRequested() { window.allowClose = true; window.close() }
    }
    Connections {
        target: window.chrome
        function onFallbackTriggered(message) { window.showNotice("info", message) }
    }
    Shortcut { sequence: StandardKey.Save; onActivated: window.cmd("project.save") }
    Shortcut { sequence: StandardKey.Open; onActivated: window.cmd("project.open") }
    Shortcut { sequences: [StandardKey.Undo]; enabled: !window.startup && !window.textFocus; onActivated: window.cmd("history.undo") }
    Shortcut { sequences: [StandardKey.Redo]; enabled: !window.startup && !window.textFocus; onActivated: window.cmd("history.redo") }
    Shortcut { sequence: "Ctrl+Shift+Z"; enabled: !window.startup && !window.textFocus; onActivated: window.cmd("history.redo") }
    Shortcut { sequence: "Space"; enabled: !window.startup && !window.textFocus && !window.windowControlFocus && !!window.media.ready; onActivated: window.cmd("media.play") }
    Shortcut { sequence: "Left"; enabled: !window.startup && !window.textFocus && !window.timelineEdgeFocus && !window.audioControlFocus && !!window.media.ready; onActivated: window.cmd("media.step", {delta: -1}) }
    Shortcut { sequence: "Right"; enabled: !window.startup && !window.textFocus && !window.timelineEdgeFocus && !window.audioControlFocus && !!window.media.ready; onActivated: window.cmd("media.step", {delta: 1}) }
    Shortcut { sequence: "Shift+Left"; enabled: !window.startup && !window.textFocus && !window.timelineEdgeFocus && !window.audioControlFocus && !!window.media.ready; onActivated: window.cmd("media.step", {delta: -5}) }
    Shortcut { sequence: "Shift+Right"; enabled: !window.startup && !window.textFocus && !window.timelineEdgeFocus && !window.audioControlFocus && !!window.media.ready; onActivated: window.cmd("media.step", {delta: 5}) }
    Shortcut { sequence: "Shift+F6"; autoRepeat: false; enabled: !window.startup && !window.textFocus && window.workspace === "make" && window.stage === "review"; onActivated: review.revealSplitTarget() }
    Shortcut { sequence: "F2"; autoRepeat: false; enabled: !window.startup && !window.textFocus && window.workspace === "make" && window.stage === "review"; onActivated: window.fullWidthReview ? fullReview.editSelectedRow() : review.editSelectedRow() }
    Shortcut { sequence: "M"; enabled: !window.startup && !window.textFocus && !!window.media.ready; onActivated: window.cmd("marker.add", {frame: window.media.displayedFrame}) }
    Shortcut { sequence: "V"; autoRepeat: false; enabled: !window.startup && !window.textFocus && window.workspace === "make" && window.stage === "review"; onActivated: timeline.selectTool("select") }
    Shortcut { sequence: "H"; autoRepeat: false; enabled: !window.startup && !window.textFocus && window.workspace === "make" && window.stage === "review"; onActivated: timeline.selectTool("hand") }
    Shortcut { sequence: "C"; autoRepeat: false; enabled: !window.startup && !window.textFocus && window.workspace === "make" && window.stage === "review"; onActivated: timeline.selectTool("razor") }
    Shortcut { sequence: "D"; autoRepeat: false; enabled: !window.startup && !window.textFocus && window.workspace === "make" && window.stage === "review" && !!window.media.ready && !!(window.state.manualTiming || {}).enabled; onActivated: window.cmd("timeline.manualMark") }
    Shortcut { sequence: "["; enabled: !window.startup && !window.textFocus && !window.state.candidateReadOnly && !(window.state.alignmentPreview || {}).active && !!window.current.cueId; onActivated: window.cmd("row.setCurrent", {edge: "first"}) }
    Shortcut { sequence: "]"; enabled: !window.startup && !window.textFocus && !window.state.candidateReadOnly && !(window.state.alignmentPreview || {}).active && !!window.current.cueId; onActivated: window.cmd("row.setCurrent", {edge: "last"}) }
    Shortcut { sequence: "Alt+Up"; enabled: !window.startup && !window.textFocus && !!(window.state.selection || {}).previousVisible; onActivated: { var cue = window.state.selection.previousVisible; if (cue && cue.cueId) window.cmd("row.select", {id: cue.cueId}) } }
    Shortcut { sequence: "Alt+Down"; enabled: !window.startup && !window.textFocus && !!(window.state.selection || {}).nextVisible; onActivated: { var cue = window.state.selection.nextVisible; if (cue && cue.cueId) window.cmd("row.select", {id: cue.cueId}) } }
    Shortcut { sequence: "Ctrl+Return"; enabled: !window.startup && !window.textFocus && !window.state.candidateReadOnly && !(window.state.alignmentPreview || {}).active && !!window.current.cueId; onActivated: window.cmd("row.confirmNext", {id: window.current.cueId}) }
    Shortcut { sequence: "Delete"; autoRepeat: false; enabled: !window.startup && window.canDeleteCue(); onActivated: window.cmd("row.deleteSelected", {ids: (window.state.multiSelectedIds || []).length ? window.state.multiSelectedIds : [window.current.cueId]}) }
    Shortcut { sequence: "Ctrl+A"; enabled: window.reviewBrowseFocus(); onActivated: window.cmd("review.selectAll") }
    Shortcut { sequence: "Escape"; enabled: window.reviewBrowseFocus(); onActivated: window.cmd("review.clearSelection") }
    Shortcut { sequence: "Alt+0"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:0}) }
    Shortcut { sequence: "Alt+1"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:1}) }
    Shortcut { sequence: "Alt+2"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:2}) }
    Shortcut { sequence: "Alt+3"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:3}) }
    Shortcut { sequence: "Alt+4"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:4}) }
    Shortcut { sequence: "Alt+5"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:5}) }
    Shortcut { sequence: "Alt+6"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:6}) }
    Shortcut { sequence: "Alt+7"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:7}) }
    Shortcut { sequence: "Alt+8"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:8}) }
    Shortcut { sequence: "Alt+9"; autoRepeat: false; enabled: window.reviewBrowseFocus() && !window.state.candidateReadOnly; onActivated: window.cmd("review.speakerShortcut", {number:9}) }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: window.resizeBorder; spacing: 0
        Rectangle {
            objectName: "integratedTitlebarStrip"
            Layout.fillWidth: true; Layout.preferredHeight: 43
            color: Theme.paper
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: window.integratedTitlebar ? 12 : 20
                anchors.rightMargin: window.integratedTitlebar ? 6 : 20
                spacing: window.integratedTitlebar && window.width < 900 ? 5 : 8
                ActionButton {
                    objectName: "homeButton"; text: window.width < 960 ? "Lyria" : "Lyria STUDIO"
                    quiet: true; compact: true; tip: "返回启动页"; implicitWidth: window.width < 960 ? 63 : 112
                    onClicked: { window.workspace = "make"; window.cmd("project.home") }
                    contentItem: Row {
                        spacing: 5
                        Image { objectName: "lyriaTitlebarIcon"; width: 21; height: 21; source: "../assets/app/lyria.png"; sourceSize.width: 63; sourceSize.height: 63 }
                        Text { text: window.width < 960 ? "Lyria" : "Lyria STUDIO"; anchors.verticalCenter: parent.verticalCenter; color: Theme.ink; font.pixelSize: 11; font.bold: true }
                    }
                }
                Rectangle { width: 1; height: 16; color: Theme.line; Layout.leftMargin: 2; Layout.rightMargin: 2 }
                ActionButton { id: fileMenuAnchor; objectName: "fileMenuButton"; text: "文件"; quiet: true; compact: true; checked: fileMenu.opened; implicitWidth: window.width < 960 ? 34 : 43; padding: window.width < 960 ? 3 : 6; onClicked: fileMenu.showMenu() }
                ActionButton { id: editMenuAnchor; objectName: "editMenuButton"; text: "编辑"; quiet: true; compact: true; checked: editMenu.opened; implicitWidth: window.width < 960 ? 34 : 43; padding: window.width < 960 ? 3 : 6; onClicked: editMenu.showMenu() }
                ActionButton { id: subtitleMenuAnchor; objectName: "subtitleMenuButton"; text: "字幕"; quiet: true; compact: true; checked: subtitleMenu.opened; implicitWidth: window.width < 960 ? 34 : 43; padding: window.width < 960 ? 3 : 6; onClicked: subtitleMenu.showMenu() }
                ActionButton { id: viewMenuAnchor; objectName: "viewMenuButton"; text: "视图"; quiet: true; compact: true; checked: viewMenu.opened; implicitWidth: window.width < 960 ? 34 : 43; padding: window.width < 960 ? 3 : 6; onClicked: viewMenu.showMenu() }
                ActionButton { id: toolsMenuAnchor; objectName: "toolsMenuButton"; text: "工具"; quiet: true; compact: true; checked: toolsMenu.opened; implicitWidth: window.width < 960 ? 34 : 43; padding: window.width < 960 ? 3 : 6; onClicked: toolsMenu.showMenu() }
                ActionButton { id: settingsMenuAnchor; objectName: "settingsMenuButton"; text: "设置"; quiet: true; compact: true; checked: settingsMenu.opened; implicitWidth: window.width < 960 ? 34 : 43; padding: window.width < 960 ? 3 : 6; onClicked: settingsMenu.showMenu() }
                ActionButton { id: helpMenuAnchor; objectName: "helpMenuButton"; text: "帮助"; quiet: true; compact: true; checked: helpMenu.opened; implicitWidth: window.width < 960 ? 34 : 43; padding: window.width < 960 ? 3 : 6; onClicked: helpMenu.showMenu() }
                TitlebarCaption {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    Layout.minimumWidth: window.integratedTitlebar ? 48 : 0
                    targetWindow: window; chrome: window.chrome
                }
                ActionButton { text: window.narrow ? "" : "撤销"; glyph: "undo"; compact: true; enabled: (window.state.history || {}).canUndo === true; onClicked: window.cmd("history.undo"); tip: "撤销（Ctrl+Z）" }
                ActionButton { text: window.narrow ? "" : "重做"; glyph: "redo"; compact: true; enabled: (window.state.history || {}).canRedo === true; onClicked: window.cmd("history.redo"); tip: "重做（Ctrl+Shift+Z）" }
                ActionButton { text: window.integratedTitlebar && window.width < 900 ? "保存" : "保存工程"; glyph: "save"; compact: true; onClicked: window.cmd("project.save"); tip: "保存工程（Ctrl+S）" }
                Label { textFormat: Text.PlainText; text: "●  " + (window.project.dirty ? "有修改未保存" : "本地工程"); color: window.project.dirty ? Theme.amber : Theme.green; font.pixelSize: 10; visible: window.width > 1190; Layout.leftMargin: 5 }
                WindowControls { targetWindow: window; chrome: window.chrome; visible: window.integratedTitlebar }
            }
        }
        Item {
            objectName: "projectHeader"
            visible: !window.startup || window.workspace === "settings"
            Layout.fillWidth: true; Layout.preferredHeight: window.reviewWorkspace ? Math.max(33,steps.implicitHeight+6) : 57
            RowLayout {
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.leftMargin: 22; anchors.rightMargin: 22; anchors.topMargin: window.reviewWorkspace ? 2 : 8
                ColumnLayout {
                    Layout.maximumWidth: Math.max(0,parent.width-steps.implicitWidth-28); Layout.preferredWidth: Math.min(360, Layout.maximumWidth); Layout.minimumWidth: 0; spacing: 3
                    Label { visible: !window.reviewWorkspace; textFormat: Text.PlainText; text: "MOONLIT LILAC  ·  歌词与画面，恰好相遇"; font.pixelSize: 10; font.letterSpacing: 0.7; color: "#998BB8" }
                    RowLayout {
                        Label { textFormat: Text.PlainText; text: window.project.name || "未命名工程"; font.pixelSize: 19; font.weight: Font.Bold; color: Theme.ink; elide: Text.ElideRight; Layout.minimumWidth: 0; Layout.maximumWidth: Math.min(310, Math.max(0, window.width-steps.implicitWidth-90)); Layout.fillWidth: true }
                        Label { textFormat: Text.PlainText; text: (window.state.rowCount || 0) + " 条歌词" + (window.media.fpsLabel ? "  ·  " + window.media.fpsLabel : ""); color: Theme.label; font.pixelSize: 11; visible: window.width > 1160 }
                    }
                }
                Item { Layout.fillWidth: true }
                Rectangle {
                    id: workspaceSwitch; objectName: "workspaceSwitch"; visible: false
                    Layout.preferredWidth: switchRow.implicitWidth + 8; Layout.preferredHeight: 38; radius: 10; color: "#EDE9F5"; border.color: "#E5DFEF"
                    Rectangle {
                        id: switchHighlight
                        objectName: "workspaceSwitchHighlight"
                        visible: window.workspace !== "settings"
                        x: switchX.value
                        MotionNumber { id: switchX; targetValue: switchRow.x + (window.workspace === "qc" ? qcTab.x : makeTab.x); duration: 180 }
                        y: switchRow.y; width: switchWidth.value
                        MotionNumber { id: switchWidth; targetValue: window.workspace === "qc" ? qcTab.width : makeTab.width; duration: 180 }
                        height: makeTab.height; radius: 7; color: "white"
                    }
                    Row {
                        id: switchRow; anchors.centerIn: parent; spacing: 3
                        ActionButton { id: makeTab; objectName: "makeWorkspaceButton"; text: "字幕制作"; glyph: "sub"; compact: true; checked: window.workspace === "make"; background: Rectangle { color: "transparent" } onClicked: window.workspace = "make" }
                        ActionButton { id: qcTab; objectName: "qcWorkspaceButton"; visible: false; text: "成片质检"; glyph: "qc"; compact: true; checked: window.workspace === "qc"; background: Rectangle { color: "transparent" } onClicked: window.workspace = "qc" }
                        ActionButton { objectName: "settingsWorkspaceButton"; glyph: "settings"; compact: true; checked: window.workspace === "settings"; tip: "设置"; onClicked: window.workspace = window.workspace === "settings" ? window.previousWorkspace : "settings" }
                    }
                }
            }
            Rectangle {
                visible: window.workspace === "make"
                objectName: "workflowStageBar"
                anchors.right: parent.right; anchors.rightMargin: 22
                anchors.verticalCenter: parent.verticalCenter
                width: steps.implicitWidth + 14; height: window.reviewWorkspace ? steps.implicitHeight + 4 : 36; radius: 10; color: Theme.paper; border.color: Theme.line
                Rectangle {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    anchors.leftMargin: 9; anchors.rightMargin: 9; anchors.bottomMargin: 1
                    height: 2; radius: 1; color: "#EEE8F7"; visible: (window.state.rowCount || 0) > 0
                    Rectangle { id: progressFill; objectName: "confirmationProgressFill"; height: parent.height; radius: 1; color: Theme.lilac; width: progressMotion.value; MotionNumber { id: progressMotion; targetValue: progressFill.parent.width * Math.min(1, (window.state.confirmedCount || 0) / Math.max(1, window.state.rowCount || 0)); duration: 220 } }
                }
                Row {
                    id: steps; anchors.centerIn: parent; spacing: 3
                    Repeater {
                        model: [{key: "import", title: "导入 / 识别", number: 1}, {key: "review", title: window.state.entryMode === "edit" ? "歌词编辑" : "逐句核对", number: 2}]
                        StepButton { required property var modelData; objectName: "stageButton_" + modelData.key; text: modelData.title; stepNumber: modelData.number; completed: (window.state.stageCompletion || {})[modelData.key] === true || (modelData.key === "review" && window.stage === "export"); checked: window.stage === modelData.key || (modelData.key === "import" && window.stage === "ocr"); onClicked: window.navigate(modelData.key) }
                    }
                    Rectangle { width: 1; height: 16; color: Theme.line; anchors.verticalCenter: parent.verticalCenter }
                    Label { textFormat: Text.PlainText; text: "已确认 " + (window.state.confirmedCount || 0) + "/" + (window.state.rowCount || 0); color: Theme.label; font.pixelSize: 10; anchors.verticalCenter: parent.verticalCenter; leftPadding: 5; rightPadding: 5 }
                    ActionButton { objectName: "exportStageButton"; emphasized: true; text: "导出字幕"; glyph: "export"; compact: true; checked: window.stage === "export"; onClicked: window.navigate("export") }
                }
            }
        }
        Item {
            id: workspaceArea
            objectName: "workspaceArea"
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: window.narrow ? 14 : 22; Layout.rightMargin: window.narrow ? 14 : 22; Layout.bottomMargin: 12
            StartupPage {
                anchors.fill: parent; visible: window.startup && window.workspace === "make"; active: visible
                controller: bridge; state: window.state
                onQcRequested: window.workspace = "qc"
            }
            MotionSurface {
                objectName: "makeWorkspaceMotion"
                anchors.fill: parent
                active: !window.fullWidthReview && !window.startup && window.workspace === "make" && window.stage !== "export"
                pageKey: window.stage
            Item {
                id: makeScroll
                objectName: "makeScroll"
                anchors.fill: parent
                readonly property real availableWidth: width
                readonly property real availableHeight: height
                clip: true
                Row {
                    id: compactPlayer; objectName: "audioOnlyPlayback"; visible: window.reviewWorkspace && !window.previewShown
                    height: 30; spacing: 6; anchors.horizontalCenter: parent.horizontalCenter
                    TransportButton { glyph: "previous-cue"; tip: "上一句"; onClicked: window.cmd("row.select", {id: ((window.state.selection || {}).previousTimed || {}).cueId}); enabled: !!((window.state.selection || {}).previousTimed || {}).cueId }
                    TransportButton { glyph: window.media.playing ? "pause":"play"; tip: "播放 / 暂停"; onClicked: window.cmd("media.play"); enabled: !!window.media.ready }
                    TransportButton { glyph: "play-cue"; tip: "试听本句"; onClicked: window.cmd("media.playCue"); enabled: !!window.media.ready }
                    Label { text: "F " + (window.media.displayedFrame || 0) + " / " + (window.media.totalFrames || 0); height: parent.height; verticalAlignment: Text.AlignVCenter; color: Theme.label }
                }
                Item {
                    id: makeContent
                    width: makeScroll.availableWidth
                    y: compactPlayer.visible ? compactPlayer.height + 4 : 0
                    height: makeScroll.height - y
                    SplitView {
                        id: verticalSplit
                        objectName: "verticalSplit"
                        property real topFraction: (window.state.workspaceLayout || {}).topFraction || 0.58
                        property bool timelineResized: false
                        onHeightChanged: if (timeline) timeline.workspaceAudioHeight = -1
                        anchors.fill: parent; orientation: Qt.Vertical
                        // Keep row-mode/header/window changes declarative. Assigning a
                        // number from onHeightChanged severed this binding and left a
                        // stale preference (visible as a 6px fold/unfold height jump).
                        readonly property real requestedTimelineHeight: timeline.collapsed ? timeline.implicitHeight :
                            Math.max(timeline.minimumExpandedHeight, Math.round(height * (1-topFraction)))
                        function restoreTimelineHeightBinding() {
                            timeline.SplitView.preferredHeight = Qt.binding(function() { return verticalSplit.requestedTimelineHeight })
                        }
                        onResizingChanged: {
                            if (resizing) { timelineResized = true; timeline.holdAudioHeightForWorkspaceResize() }
                            else {
                                if (timeline.visible && height > 0) { topFraction = (mainSplit.height + 8) / height; window.cmd("view.layout", {topFraction: topFraction}) }
                                restoreTimelineHeightBinding()
                            }
                        }
                        // Native boundary belongs to the fixed outer split, above the audio/timeline region.
                        handle: Rectangle {
                            objectName: "timelineSplitHandle"; implicitHeight: 8; color: Theme.canvas
                            Rectangle { anchors.centerIn: parent; width: 44; height: 3; radius: 2; color: Theme.lilac; opacity: parent.SplitHandle.pressed || topGripHover.hovered ? 1 : .72 }
                            HoverHandler { id: topGripHover; cursorShape: Qt.SplitVCursor }
                        }
                        SplitView {
                            id: mainSplit
                            visible: window.previewShown || window.reviewShown
                            objectName: "mainSplit"
                            property real previewFraction: (window.state.workspaceLayout || {}).previewFraction || 0.52
                            // Keep both panes inside the fixed workspace; ImportPanel scrolls internally.
                            orientation: Qt.Horizontal
                            onWidthChanged: if (orientation === Qt.Horizontal && !resizing) previewPanel.SplitView.preferredWidth = width * previewFraction
                            onOrientationChanged: if (orientation === Qt.Horizontal) previewPanel.SplitView.preferredWidth = width * previewFraction
                            onResizingChanged: if (!resizing) {
                                if (window.previewShown && window.reviewShown && orientation === Qt.Horizontal && width > 0) { previewFraction = previewPanel.width / width; window.cmd("view.layout", {previewFraction: previewFraction}) }
                                else if (window.previewShown && window.reviewShown) window.cmd("view.layout", {previewHeight: previewPanel.height})
                            }
                            SplitView.fillHeight: true
                            SplitView.minimumHeight: orientation === Qt.Vertical ? (previewPanel.visible ? previewPanel.SplitView.minimumHeight : 0) + (sideWorkspace.visible ? sideWorkspace.SplitView.minimumHeight : 0) + 12 : Math.max(previewPanel.visible ? previewPanel.SplitView.minimumHeight : 0, sideWorkspace.visible ? sideWorkspace.SplitView.minimumHeight : 0)
                            handle: Rectangle {
                                id: horizontalGrip; objectName: "mainSplitHandle"; implicitWidth: 8; implicitHeight: 6; color: Theme.canvas
                                Rectangle { objectName: "mainSplitIndicator"; anchors.centerIn: parent; width: mainSplit.orientation === Qt.Horizontal ? 3 : 36; height: mainSplit.orientation === Qt.Horizontal ? 36 : 3; radius: 2; color: Theme.lilac; opacity: horizontalGripHover.hovered || horizontalGrip.SplitHandle.pressed ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 140 } } }
                                HoverHandler { id: horizontalGripHover; cursorShape: mainSplit.orientation === Qt.Horizontal ? Qt.SplitHCursor : Qt.SplitVCursor }
                                ToolTip.visible: horizontalGripHover.hovered && window.active
                                ToolTip.text: "拖动调整视频播放器大小"; ToolTip.delay: 650
                            }
                            PreviewPanel {
                                id: previewPanel
                                visible: window.previewShown
                                objectName: "mainPreviewPanel"
                                controlsExtraHeight: (window.state.workspaceLayout || {}).controlsHeight || 0
                                onControlsHeightEdited: value => window.cmd("view.layout", {controlsHeight: value})
                                controller: bridge; state: window.state; media: window.media; review: window.stage === "review"; windowActive: !window.startup && window.active && window.visible && window.workspace === "make" && window.stage !== "export"
                                SplitView.preferredWidth: mainSplit.width * mainSplit.previewFraction
                                SplitView.minimumWidth: 330
                                SplitView.minimumHeight: previewPanel.minimumPanelHeight
                                SplitView.preferredHeight: (window.state.workspaceLayout || {}).previewHeight || 420
                                SplitView.fillHeight: true
                            }
                            Item {
                                id: sideWorkspace
                                objectName: "sideWorkspace"
                                visible: window.reviewShown
                                SplitView.fillWidth: true; SplitView.fillHeight: true
                                SplitView.minimumWidth: 380
                                SplitView.minimumHeight: window.stage === "review" ? review.minimumPanelHeight : Math.min(400, mainSplit.height)
                                ReviewPanel { id: review; showTimecodes: window.reviewTimecodes; onTimeFormatRequested: window.reviewTimecodes = !window.reviewTimecodes; anchors.fill: parent; viewportTop: 0; viewportHeight: height; visible: window.stage === "review"; controller: bridge; state: window.state; media: window.media; onFullWidthRequested: window.fullWidthReview = true; onEditingStarted: editor => window.revealLyricCaret(editor); onNextStep: window.navigate("export"); onFeedback: message => window.showNotice("info", message) }
                                ImportPanel { anchors.fill: parent; visible: window.stage !== "review"; controller: bridge; state: window.state; media: window.media; ocrMode: window.stage === "ocr"; onReviewRequested: window.navigate("review"); regionsVisible: previewPanel.cropVisible; onVisibilityRequested: value => previewPanel.cropVisible = value; onShowRegions: previewPanel.cropVisible = true; onRoiSetupRequested: previewPanel.beginRoiSetup(); onRoiManualRequested: { if (previewPanel.ready && !previewPanel.nonVideo) { previewPanel.drawingNewRegion = true; previewPanel.roiSelecting = true; previewPanel.cropVisible = true } } }
                            }
                        }
                        FrameTimeline {
                            id: timeline
                            objectName: "frameTimeline"
                            visible: window.stage === "review" && (window.timelineShown || window.audioShown)
                            lyricVisible: window.timelineShown
                            controller: bridge; state: window.state; media: window.media
                            windowActive: !window.startup && window.active && window.visible && window.workspace === "make" && window.stage !== "export"
                            SplitView.preferredHeight: verticalSplit.requestedTimelineHeight
                            SplitView.minimumHeight: collapsed ? implicitHeight : minimumExpandedHeight
                            SplitView.maximumHeight: collapsed ? implicitHeight : Math.max(minimumExpandedHeight, verticalSplit.height - mainSplit.SplitView.minimumHeight - 8)
                        }
                    }

                }
            }
            }
            FullWidthReview {
                id: fullReview; timecodes:window.reviewTimecodes; onTimeFormatRequested:window.reviewTimecodes=!window.reviewTimecodes; objectName: "fullWidthReviewWorkspace"; anchors.fill: parent
                visible: window.fullWidthReview && !window.startup && window.workspace === "make" && window.stage === "review"
                enabled: visible; controller: bridge; state: window.state; media: window.media
                onCloseRequested: window.fullWidthReview = false
            }
            MotionSurface {
                objectName: "exportWorkspaceMotion"; anchors.fill: parent
                active: !window.startup && window.workspace === "make" && window.stage === "export"
                ExportPage { anchors.fill: parent; controller: bridge; state: window.state; onReturnToReview: window.navigate("review") }
            }
            MotionSurface {
                objectName: "qcWorkspaceMotion"; anchors.fill: parent; active: window.workspace === "qc"
                QCPage { anchors.fill: parent; controller: bridge; state: window.state }
            }
            MotionSurface {
                objectName: "settingsWorkspaceMotion"; anchors.fill: parent; active: window.workspace === "settings"
                SettingsPage { anchors.fill: parent; controller: bridge; state: window.state; onCloseRequested: window.workspace = window.previousWorkspace }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 29
            color: "#F5F2FA"
            Rectangle { width: parent.width; height: 1; color: Theme.line }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22; spacing: 14
                ShimmerText { running: !!window.task.running; text: window.task.running ? window.task.label || "正在处理…" : (window.state.status || "本地工程") + " · " + (window.project.autosaveStatus || "自动备份已开启"); color: Theme.label; fontPixelSize: 10; Layout.fillWidth: true }
                ActionButton { objectName: "apiProgressDetailsButton"; visible: !!window.task.running && !!window.task.details; text: "详情"; compact: true; implicitHeight: 23; onClicked: taskDetailsDialog.open() }
                ThemeProgressBar { objectName: "foregroundTaskProgress"; visible: !!window.task.running && !!window.task.api; Layout.preferredWidth: 110; from: 0; to: 1; value: window.task.progress || 0 }
                ActionButton { visible: !!window.task.running; text: window.task.api ? "停止后续请求" : "取消"; enabled: !window.task.cancelling; compact: true; implicitHeight: 23; onClicked: window.cmd("task.cancel") }
                Label { textFormat: Text.PlainText; text: "Space 播放   ← → 逐帧   [ ] 设入/出点   M 标记点   Ctrl+Z 撤销"; color: Theme.muted; font.pixelSize: 10; visible: window.width > 1050 }

            }
        }
    }
    WindowResizeFrame { anchors.fill: parent; targetWindow: window; chrome: window.chrome }
    Rectangle {
        id: toast
        objectName: "noticeToast"
        property string displayedText: ""
        anchors.bottom: parent.bottom; anchors.bottomMargin: toastOffset.value; anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(window.width - 40, toastLabel.implicitWidth + 32); height: toastLabel.implicitHeight + 24
        opacity: toastOpacity.value
        visible: opacity > 0
        MotionNumber { id: toastOpacity; targetValue: window.noticeText.length > 0 ? 1 : 0; duration: 160 }
        MotionNumber { id: toastOffset; targetValue: window.noticeText.length > 0 ? 44 : 34; duration: 160 }
        radius: 10; color: window.noticeLevel === "error" ? "#FCF0F5" : "#F1ECFB"; border.color: window.noticeLevel === "error" ? "#E7BAD0" : "#D6C5EB"; z: 1000
        Label { textFormat: Text.PlainText; id: toastLabel; anchors.centerIn: parent; width: Math.min(implicitWidth, window.width - 72); text: toast.displayedText; wrapMode: Text.Wrap; color: window.noticeLevel === "error" ? "#A24C75" : Theme.lilacDark; font.pixelSize: 12 }
        MouseArea { anchors.fill: parent; onClicked: { window.noticeText = ""; toastTimer.stop() } }
    }
    ThemeDialog {
        id: taskDetailsDialog; objectName: "taskDetailsDialog"
        anchors.centerIn: parent; modal: true; title: "任务详情"
        width: Math.min(540, window.width - 40)
        standardButtons: Dialog.Close
        Label { width: parent.width; text: window.task.details || window.task.label || "任务已结束"; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: Theme.label }
    }
    Timer { id: toastTimer; interval: 6500; onTriggered: window.noticeText = "" }
    MotionMenu {
        id: fileMenu; objectName: "fileMenu"; parent: fileMenuAnchor; y: fileMenuAnchor.height + 4
        MenuItem { text: "启动页 / 最近工程"; onTriggered: { window.workspace = "make"; window.cmd("project.home") } }
        MenuItem { objectName: "newProjectMenuAction"; text: "新建工程"; onTriggered: { const review = window.reviewWorkspace; window.workspace = "make"; window.cmd(review ? "project.newFromReview" : "project.home") } }
        MenuItem { text: "打开工程    Ctrl+O"; onTriggered: window.cmd("project.open") }
        MenuSeparator { }
        MenuItem { text: "导入音视频"; onTriggered: window.cmd("media.open") }
        MenuItem { text: "导入中文字幕 / 译文"; onTriggered: window.cmd("input.import", {role: "translation"}) }
        MenuItem { text: "导入原文 / KTV"; onTriggered: window.cmd("input.import", {role: "source"}) }
        MenuSeparator { }
        MenuItem { text: "保存工程    Ctrl+S"; onTriggered: window.cmd("project.save") }
        MenuItem { text: "工程另存为"; onTriggered: window.cmd("project.saveAs") }
        MenuItem { text: "导出字幕"; onTriggered: window.navigate("export") }
        MenuSeparator { }
        MenuItem { objectName: "relocateSourcesAction"; text: "重新关联素材文件"; enabled: window.capabilities["project.relocate"] === true; onTriggered: window.cmd("project.relocate") }
        MenuItem { text: "恢复自动备份"; enabled: window.capabilities["project.recover"] === true; onTriggered: window.cmd("project.recover") }
        MenuSeparator { }
        MenuItem { text: "退出"; onTriggered: window.close() }
    }
    MotionMenu {
        id: editMenu; objectName: "editMenu"; parent: editMenuAnchor; y: editMenuAnchor.height + 4
        MenuItem { text: "撤销    Ctrl+Z"; enabled: (window.state.history || {}).canUndo === true; onTriggered: window.cmd("history.undo") }
        MenuItem { text: "重做    Ctrl+Shift+Z"; enabled: (window.state.history || {}).canRedo === true; onTriggered: window.cmd("history.redo") }
        MenuItem { objectName: "clearCandidateHistoryMenuAction"; text: "清理识别历史…"; onTriggered: bridge.clearOldCandidates() }
        MenuItem { text: "编辑历史"; enabled: window.capabilities["history.show"] === true; onTriggered: window.cmd("history.show") }
        MenuSeparator { }
        MenuItem { text: "清空所有标记点"; enabled: window.writableReview && (window.state.markers || []).length > 0; onTriggered: window.cmd("marker.clear") }
    }
    MotionMenu {
        id: subtitleMenu; objectName: "subtitleMenu"; parent: subtitleMenuAnchor; y: subtitleMenuAnchor.height + 4
        MenuItem { text: "样式管理"; enabled: !window.state.candidateReadOnly; onTriggered: window.cmd("style.manager") }
        Menu {
            id: currentStyleMenu; title: "当前样式"; enabled: window.writableReview
            Instantiator {
                model: (window.state.styles || {}).items || []
                delegate: MenuItem { required property var modelData; text: modelData.name; checkable: true; checked: modelData.id === (window.state.styles || {}).currentId; onTriggered: window.cmd("style.select", {id: modelData.id}) }
                onObjectAdded: (index, object) => currentStyleMenu.insertItem(index, object)
                onObjectRemoved: (index, object) => currentStyleMenu.removeItem(object)
            }
        }
        MenuItem { text: "应用样式到所选句（替换个人覆盖）"; enabled: window.writableReview && !!window.current.cueId && !!(window.state.styles || {}).currentId; onTriggered: window.cmd("style.apply") }
        MenuItem { objectName: "subtitlePlacementMenu"; text: "字幕位置与排版…"; enabled: !window.state.candidateReadOnly; onTriggered: window.cmd("settings.subtitleStyle") }
        MenuItem { text: "批量应用样式"; enabled: window.writableReview && (window.state.rowCount || 0) > 0; onTriggered: window.cmd("style.manager", {batch: true}) }
        MenuSeparator { }
        MenuItem { text: "新建空白句"; enabled: window.writableReview; onTriggered: window.cmd("row.add", {after: window.current.cueId || ""}) }
        MenuItem { text: "删除所选句    Delete"; enabled: window.writableReview && !!window.current.cueId; onTriggered: window.cmd("row.deleteSelected", {ids: (window.state.multiSelectedIds || []).length ? window.state.multiSelectedIds : [window.current.cueId]}) }
        MenuItem { text: "重新匹配原文（保留已锁定句）"; enabled: window.writableReview && window.capabilities["project.rematch"] === true; onTriggered: window.cmd("project.rematch") }
        MenuItem { objectName: "compareRecognitionResults"; text: "对比与应用识别/打轴结果…"; enabled: !!window.state.candidateComparisonAvailable; onTriggered: bridge.compareCandidates() }
        MenuItem { text: "切换/管理保留时间轴…"; enabled: !!window.state.timelineSwitchAvailable; onTriggered: bridge.manageTimelines() }
        MenuItem { text: (window.state.pairingLabel || "与翻译配对") + "…"; enabled: window.writableReview; onTriggered: window.cmd("pairing.workbench") }
        MenuItem { text: "复制为手动时间轴"; enabled: window.writableReview && (window.state.rowCount || 0) > 0; onTriggered: window.cmd("timeline.copyManual") }
    }
    MotionMenu {
        id: viewMenu; objectName: "viewMenu"; parent: viewMenuAnchor; y: viewMenuAnchor.height + 4
        MenuItem { objectName: "togglePicturePanel"; text: "画面预览"; checkable: true; checked: window.previewShown; enabled: window.reviewWorkspace; onTriggered: window.togglePanel("previewVisible", !window.previewShown) }
        MenuItem { objectName: "toggleReviewPanel"; text: window.state.entryMode === "edit" ? "歌词编辑" : "歌词核对"; checkable: true; checked: window.reviewShown; enabled: window.reviewWorkspace && (!checked || window.shownPanelCount > 1); onTriggered: window.togglePanel("reviewVisible", !window.reviewShown) }
        MenuItem { objectName: "toggleSpectrumPanel"; text: "频谱图"; checkable: true; checked: window.audioShown; enabled: window.reviewWorkspace && (!checked || window.shownPanelCount > 1); onTriggered: window.togglePanel("audioVisible", !window.audioShown) }
        MenuItem { objectName: "toggleTimelinePanel"; text: "时间轴"; checkable: true; checked: window.timelineShown; enabled: window.reviewWorkspace && (!checked || window.shownPanelCount > 1); onTriggered: window.togglePanel("timelineVisible", !window.timelineShown) }
        MenuSeparator { }
        MenuItem { objectName: "resetWorkspaceLayout"; text: "恢复默认布局"; enabled: window.reviewWorkspace; onTriggered: window.resetLayout() }
        MenuSeparator { }
        MenuItem { text: "时间轴放大"; enabled: window.reviewWorkspace && window.timelineShown && timeline.total > 0; onTriggered: timeline.zoom(1 / 1.5) }
        MenuItem { text: "时间轴缩小"; enabled: window.reviewWorkspace && window.timelineShown && timeline.total > 0; onTriggered: timeline.zoom(1.5) }
        MenuItem { text: "时间轴适应窗口"; enabled: window.reviewWorkspace && window.timelineShown && timeline.total > 0; onTriggered: timeline.fit() }
    }
    MotionMenu {
        id: toolsMenu; objectName: "toolsMenu"; parent: toolsMenuAnchor; y: toolsMenuAnchor.height + 4
        MenuItem { text: "KTV 字幕转换"; enabled: window.capabilities["ktv.convert"] === true; onTriggered: window.cmd("ktv.convert") }
    }
    MotionMenu {
        id: settingsMenu; objectName: "settingsMenu"; parent: settingsMenuAnchor; y: settingsMenuAnchor.height + 4
        MenuItem { objectName: "settingsMenuAction"; text: "常规与播放设置"; onTriggered: window.workspace = "settings" }
        MenuSeparator { }
        MenuItem { text: "OCR 识别设置"; enabled: window.capabilities["settings.ocr"] === true; onTriggered: window.cmd("settings.ocr") }
        MenuItem { text: "字幕样式设置"; enabled: !window.state.candidateReadOnly; onTriggered: window.cmd("style.manager") }
        MenuItem { text: "本地模型管理"; onTriggered: window.cmd("vocal.configure") }
    }
    MotionMenu {
        id: helpMenu; objectName: "helpMenu"; parent: helpMenuAnchor; y: helpMenuAnchor.height + 4
        MenuItem { text: "快捷键说明"; onTriggered: shortcutsDialog.open() }
        MenuSeparator { }
        MenuItem { text: "运行日志"; enabled: window.capabilities["log.show"] === true; onTriggered: window.cmd("log.show") }
        MenuItem { objectName: "exportDiagnosticsMenuAction"; text: "导出诊断日志"; enabled: window.capabilities["diagnostics.export"] === true; onTriggered: window.cmd("diagnostics.export") }
        MenuSeparator { }
        MenuItem { objectName: "softwareUpdateMenuAction"; text: "软件更新"; onTriggered: window.cmd("software.update") }
        MenuItem { text: "关于 Lyria"; onTriggered: aboutDialog.open() }
    }
    ThemeDialog {
        id: aboutDialog; modal: true; anchors.centerIn: parent; title: "关于 Lyria"; standardButtons: Dialog.Close
        Label { text: "Lyria\n本地歌词编辑、字幕识别与自动打轴工作室"; textFormat: Text.PlainText; color: Theme.label }
    }
    ThemeDialog {
        id: closeDialog
        objectName: "closeDialog"
        anchors.centerIn: parent; width: Math.min(490, window.width - 40); modal: true
        readonly property var details: window.state.closeDialog || ({})
        readonly property bool shutdownFailure: details.shutdownFailure === true
        title: details.newFromReview ? "新建工程" : shutdownFailure ? "保存工程失败" : details.switching ? "切换工程确认" : "关闭窗口确认"
        closePolicy: Popup.NoAutoClose
        ColumnLayout {
            width: parent.width; spacing: 14
            Label { objectName: "closeDialogMessage"; textFormat: Text.PlainText; text: closeDialog.details.newFromReview ? "当前工程有未保存的修改。要先保存再新建工程吗？" : closeDialog.shutdownFailure ? String(closeDialog.details.message || "保存工程失败，请重试保存，或选择不保存并退出。") : (window.project.dirty ? "• 工程尚未保存\n" : "") + (window.project.unexported ? "• 当前修改尚未导出字幕\n" : "") + (closeDialog.details.switching ? "切换工程前" : "关闭窗口前") + "是否先保存工程或导出主时间轴字幕？\n导出目标始终是主时间轴，不是正在预览的只读候选。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 12 }
            Flow {
                Layout.fillWidth: true; spacing: 8
                ActionButton { objectName: "closeSave"; text: closeDialog.shutdownFailure ? "重试保存" : window.project.dirty ? "保存工程" : "工程已保存"; enabled: closeDialog.shutdownFailure || window.project.dirty; primary: true; onClicked: { closeDialog.close(); window.cmd("close.resolve", {choice: "save"}) } }
                ActionButton { objectName: "closeExport"; visible: !closeDialog.shutdownFailure && !closeDialog.details.newFromReview; text: "导出主时间轴字幕"; onClicked: { closeDialog.close(); window.cmd("close.resolve", {choice: "export"}) } }
                ActionButton { objectName: "closeDiscard"; destructive: true; text: closeDialog.details.newFromReview ? "放弃修改" : closeDialog.shutdownFailure ? "不保存并退出" : (window.project.dirty ? "不保存并" : "不导出并") + (closeDialog.details.switching ? "切换工程" : "关闭窗口"); onClicked: { closeDialog.close(); window.cmd("close.resolve", {choice: "discard"}) } }
                ActionButton { objectName: "closeCancel"; visible: !closeDialog.shutdownFailure; text: "取消"; onClicked: { closeDialog.close(); window.cmd("close.resolve", {choice: "cancel"}) } }
            }
        }
    }
    ThemeDialog {
        id: shortcutsDialog; anchors.centerIn: parent; width: Math.min(450, window.width - 40); modal: true; title: "快捷键说明"; standardButtons: Dialog.Close
        Label { textFormat: Text.PlainText; width: parent.width; text: "Space   播放 / 暂停\n← / →   后退 / 前进 1 帧\nShift+← / Shift+→   后退 / 前进 5 帧\n[ / ]   将当前帧设为入点 / 出点\nM   在当前帧添加标记点\nF2 / 双击歌词   编辑当前句文字\nEsc   退出文字编辑\nF6   在光标处按最近标记点切分句子\nAlt+↑ / Alt+↓   切换上一句 / 下一句\nCtrl+Enter   确认当前句并跳至下一句\nV / H / C   选择 / 手形 / 剃刀\nD   依次打入点、出点\nAlt+V   切换原声与人声\nAlt+1–9 / Alt+0   指定或清除当前句说话人并跳至下一句\nCtrl+A   全选歌词行\nCtrl+S   保存工程\nCtrl+Z / Ctrl+Shift+Z   撤销 / 重做"; color: Theme.label; lineHeight: 1.7; wrapMode: Text.Wrap }
    }
}
