import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts
import Lyria 1.0

// Dedicated audio view above the lyric lanes; all timing comes from one timeline.
Item {
    id: panel
    required property var controller
    required property var state
    required property var media
    property var timelineView: null
    // Real Bridge takes only the scalar path; isolated legacy stubs may fall back.
    readonly property var analysis: controller && controller.audioDisplaySource !== undefined ? controller.audioDisplaySource : controller && controller.audioAnalysis !== undefined ? controller.audioAnalysis : state.audioAnalysis || ({})
    readonly property string status: analysis.status || "idle"
    readonly property string mode: (state.workspaceLayout || {}).audioMode || "spectrum"
    readonly property real duration: Number(analysis.durationSeconds || 0)
    readonly property real sourceOffset: Number(analysis.sourceOffsetSeconds || 0)
    readonly property bool available: duration > 0 && (status === "ready" || (mode === "spectrum" && !!analysis.spectrogramSourceReady))
    readonly property var waveform: analysis.waveform || []
    readonly property int analysisRevision: Number(analysis.revision || 0)
    readonly property real firstFrame: timelineView ? timelineView.viewFirst : 1
    readonly property real frameSpan: timelineView ? timelineView.span : Math.max(1, media.totalFrames || 1)
    readonly property real playheadFrame: timelineView ? timelineView.cursorFrame || timelineView.playheadFrame || 1 : media.playheadFrame || media.displayedFrame || 1
    readonly property var layout: state.workspaceLayout || ({})
    readonly property real gainDb: Number(layout.spectrumGain === undefined ? 7.2 : layout.spectrumGain)
    readonly property string displayPreset: layout.spectrumPreset || "detail"
    readonly property int windowSamples: Number(layout.spectrumWindow || 384)
    readonly property real blackLevel: Number(layout.spectrumBlack || 0)
    readonly property real tiltDb: Number(layout.spectrumTilt || 0)
    readonly property int frequencyMaximum: Number(layout.spectrumFrequency || 10000)
    readonly property string frequencyAxis: layout.spectrumAxis || "balanced"
    readonly property var windowOptions: [{label:"短窗·抓字头",value:384},{label:"长窗·看谐波",value:1024}]
    function applyPreset(name) {
        // Contrast is orthogonal to source, window, EQ and the user's gain.
        displaySettings({spectrumPreset:name})
    }
    function frequencyLabels() {
        var upper = frequencyAxis === "balanced" ? .46 : frequencyAxis === "speech" ? .25 : .32
        function y(hz) { return frequencyAxis === "linear" ? 1-hz/frequencyMaximum : hz >= 4000 ? upper*(frequencyMaximum-hz)/(frequencyMaximum-4000) : 1-(1-upper)*hz/4000 }
        return [{hz: (frequencyMaximum/1000) + "k",p:0}, {hz:"4k",p:y(4000)}, {hz:"2k",p:y(2000)}, {hz:"0 Hz",p:1}]
    }
    function toggleSource() { controller.selectAudioAnalysisSource(analysis.source === "vocal" ? "original" : "vocal") }
    Shortcut { sequence: "Alt+V"; enabled: panel.visible && !!panel.media.ready; onActivated: panel.toggleSource() }
    function displaySettings(values) { controller.command("view.layout", values) }
    function resetDisplay() { displaySettings({spectrumGain: 7.2, spectrumPreset: "detail", spectrumWindow: 384, spectrumBlack:0, spectrumTilt:3, spectrumFrequency:10000, spectrumAxis:"balanced"}) }
    onVisibleChanged: { if (!visible) { audioMenu.close(); advanced.close() } }
    function secondsAt(frame) {
        return controller && typeof controller.timelineFrameSeconds === "function" ? controller.timelineFrameSeconds(Math.round(frame)) : (frame - 1) / Math.max(1, media.totalFrames || 1) * duration
    }
    function xAt(seconds) {
        var frame = controller && typeof controller.timelineSecondsFrame === "function" ? controller.timelineSecondsFrame(seconds) : 1 + seconds / Math.max(.001, duration) * (media.totalFrames || 1)
        return (frame - firstFrame) / frameSpan * width
    }
    Rectangle { anchors.fill: parent; color: panel.mode === "waveform" ? "#161224" : "#0A0812" }
    SpectrogramSurface {
        id: spectrumSurface; objectName: "timelineSpectrogram"; anchors.fill: parent
        function reportViewportActivity() {
            if (panel.controller && typeof panel.controller.spectrumViewportActivity === "function")
                panel.controller.spectrumViewportActivity(panel.analysisRevision, active && displayMode === "spectrum")
        }
        onActiveChanged: reportViewportActivity()
        onDisplayModeChanged: reportViewportActivity()
        onSourceRevisionChanged: reportViewportActivity()
        onStatusChanged: {
            if ((status === "updating" || status === "ready" || status === "error") && panel.available
                    && panel.controller && typeof panel.controller.spectrumViewportReady === "function")
                panel.controller.spectrumViewportReady(panel.analysisRevision)
        }
        visible: panel.available
        active: panel.visible && panel.available
        displayMode: panel.mode
        controller: panel.controller
        sourcePath: panel.analysis.sourcePath || ""
        durationSeconds: panel.duration
        sourceOffsetSeconds: panel.sourceOffset
        sourceRevision: panel.analysisRevision
        vocalSource: panel.analysis.source === "vocal"
        firstFrame: panel.firstFrame; frameSpan: panel.frameSpan
        pixelRatio: panel.Window.window ? panel.Window.window.screen.devicePixelRatio : 1
        gainDb: panel.gainDb; displayPreset: panel.displayPreset; windowSamples: panel.windowSamples
        blackLevel: panel.blackLevel; tiltDbPerOctave: panel.tiltDb
        frequencyMaximum: panel.frequencyMaximum; frequencyAxis: panel.frequencyAxis
    }
    Label {
        objectName: "audioOverviewPending"
        anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 4
        visible: panel.available && panel.status === "loading" && !!panel.analysis.spectrogramSourceReady
        text: "全曲概览后台生成中"; color: "#D3E4EE"; font.pixelSize: 10
        background: Rectangle { color: "#A0100A20"; radius: 3 }
    }
    Label {
        objectName: "audioStatusMessage"; anchors.centerIn: parent; width: parent.width - 24
        visible: !panel.available || spectrumSurface.busyVisible && spectrumSurface.status === "loading" || spectrumSurface.status === "error"
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: !panel.available ? panel.status === "loading" ? "正在分析音轨…" : panel.status === "error" ? "音频分析失败：" + (panel.analysis.error || "无法读取音轨") : panel.status === "no_audio" ? "当前素材没有音轨" : "导入音视频后显示音轨" : spectrumSurface.status === "error" ? "音轨显示失败：" + spectrumSurface.error : panel.mode === "spectrum" ? "正在生成频谱…" : "正在生成波形…"
        color: "#D3E4EE"; font.pixelSize: 11
    }
    Label {
        objectName: "audioResolutionUpdating"
        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 4
        visible: panel.available && spectrumSurface.busyVisible && spectrumSurface.status === "updating"
        text: "预览 · 正在更新清晰度"; color: "#D3E4EE"; font.pixelSize: 10
        background: Rectangle { color: "#A0100A20"; radius: 3 }
    }
    Label {
        objectName: "spectrumCalibrationPending"
        anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 4
        visible: panel.available && panel.mode === "spectrum" && !!spectrumSurface.metrics.calibrationPending
        text: "当前视野预览 · 全曲亮度标定后台补全中"
        color: "#D3E4EE"; font.pixelSize: 10
        width: Math.max(0, parent.width - 8); elide: Text.ElideRight
    }
    Rectangle {
        objectName: "audioPlayhead"
        x: (panel.playheadFrame - panel.firstFrame) / panel.frameSpan * panel.width - width / 2
        width: 1.5; height: parent.height; color: "#EA8FB7"
        visible: !!panel.media.ready && x >= 0 && x < panel.width
    }
    // Display controls never alter PCM playback, lyric times or alignment input.
    Menu {
        id: audioMenu; objectName: "audioModeMenu"
        width: 220
        popupType: Popup.Item
        padding: 5; margins: 6
        font.family: Theme.fontFamily; font.pixelSize: 12
        palette.windowText: Theme.label; palette.text: Theme.label
        palette.highlight: Theme.lilacSoft; palette.highlightedText: Theme.lilacDark
        background: Rectangle { radius: 8; color: Theme.paper; border.color: Theme.line }
        property bool fromPlot: true
        component DisplayItem: MenuItem { implicitHeight: 28 }
        DisplayItem {
            visible: audioMenu.fromPlot; height: visible ? implicitHeight : 0
            text: panel.mode === "waveform" ? "显示频谱" : "显示波形"
            onTriggered: panel.displaySettings({audioMode:panel.mode === "waveform" ? "spectrum":"waveform"})
        }
        DisplayItem {
            visible: audioMenu.fromPlot; height: visible ? implicitHeight : 0
            text: (panel.analysis.source === "vocal" ? "切换到原声" : "切换到分离人声") + "   Alt+V"
            onTriggered: panel.toggleSource()
        }
        component PresetItem: DisplayItem {
            required property string preset
            objectName: "spectrumPreset_" + preset
            checkable: true; autoExclusive: true
            checked: panel.displayPreset === preset
            onTriggered: panel.applyPreset(preset)
        }
        PresetItem { text: "保留细节"; preset: "detail" }
        PresetItem { text: "标准均衡"; preset: "balanced" }
        PresetItem { text: "深黑高对比"; preset: "contrast" }
        DisplayItem { objectName: "advancedSpectrumMenuItem"; text: "高级画面调节…"; onTriggered: panel.openAdvanced() }
        DisplayItem { objectName: "spectrumReset"; text: "恢复默认显示"; onTriggered: panel.resetDisplay() }
        DisplayItem {
            visible: panel.displayPreset === "physical"; height: visible ? implicitHeight : 0
            text: "原始对照"; checkable: true; checked: visible; enabled: false
        }
        MenuSeparator { }
        Control {
            width: audioMenu.availableWidth; padding: 9
            contentItem: ColumnLayout {
                spacing: 3
                Label { text: "画面亮度  " + Math.round((panel.gainDb + 12) / 24 * 100) + "%"; color: Theme.label; font.pixelSize: 11 }
                Slider {
                    objectName: "spectrumGainSlider"; Accessible.name: "画面亮度"
                    Layout.fillWidth: true; from: -12; to: 12; stepSize: 0.6; value: panel.gainDb
                    onMoved: panel.displaySettings({spectrumGain:value})
                }
                ChoiceBox {
                    objectName: "spectrumWindowSelector"; Accessible.name: "分析窗长"
                    Layout.fillWidth: true; implicitHeight: 27; model: panel.windowOptions; textRole: "label"
                    currentIndex: model.findIndex(option => option.value === panel.windowSamples)
                    displayText: currentIndex < 0 ? "清晰度 · " + panel.windowSamples : currentText
                    onActivated: index => panel.displaySettings({spectrumWindow:model[index].value})
                }
            }
        }
    }
    SpectrumAdvanced { id: advanced; settingsPanel: panel }
    function openAdvanced() { advanced.open() }
    function openMenu(anchor) {
        audioMenu.fromPlot = !anchor
        if (anchor) audioMenu.popup(anchor, 0, anchor.height + 3)
        else audioMenu.popup(panel)
    }
    function closeMenu() { audioMenu.close() }
}
