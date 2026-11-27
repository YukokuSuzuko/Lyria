import QtQuick
import QtQuick.Controls

Panel {
    id: panel
    required property var controller
    required property var state
    required property var media
    property var timelineView: null
    readonly property var analysis: controller && controller.audioAnalysis !== undefined ? controller.audioAnalysis : state.audioAnalysis || ({})
    readonly property string status: analysis.status || "idle"
    readonly property string mode: (state.workspaceLayout || {}).audioMode || "waveform"
    readonly property real duration: Number(analysis.durationSeconds || 0)
    readonly property bool available: status === "ready" && duration > 0
    readonly property var waveform: analysis.waveform || []
    readonly property var spectrum: analysis.spectrum || []
    readonly property int analysisRevision: Number(analysis.revision || 0)
    readonly property real firstFrame: timelineView ? timelineView.viewFirst : 1
    readonly property real frameSpan: timelineView ? timelineView.span : Math.max(1, media.totalFrames || 1)
    readonly property real playheadFrame: timelineView ? timelineView.playheadFrame : media.playheadFrame || media.displayedFrame || 1
    onAvailableChanged: plot.requestPaint()
    onAnalysisRevisionChanged: plot.requestPaint()
    onModeChanged: plot.requestPaint()
    onFirstFrameChanged: plot.requestPaint()
    onFrameSpanChanged: plot.requestPaint()
    onVisibleChanged: if (!visible) seekArea.finish()
    function frameAt(x) { return Math.max(1, Math.min(media.totalFrames || 1, Math.round(firstFrame + x / Math.max(1, track.width) * frameSpan))) }
    function secondsAt(frame) {
        return controller && typeof controller.timelineFrameSeconds === "function" ? controller.timelineFrameSeconds(Math.round(frame)) : (frame - 1) / Math.max(1, media.totalFrames || 1) * duration
    }
    function xAt(seconds) {
        var frame = controller && typeof controller.timelineSecondsFrame === "function" ? controller.timelineSecondsFrame(seconds) : 1 + seconds / Math.max(.001, duration) * (media.totalFrames || 1)
        return (frame - firstFrame) / frameSpan * track.width
    }
    Rectangle {
        id: track; objectName: "audioTrack"
        anchors.fill: parent
        // Exactly the same horizontal origin and width as timeline lanes.
        anchors.leftMargin: timelineView ? 13 + timelineView.labelWidth : 0
        anchors.rightMargin: timelineView ? 25 : 0
        color: "#171126"; clip: true
        Canvas {
            id: plot; objectName: "audioDataCanvas"; anchors.fill: parent; visible: panel.available
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                var ctx = getContext("2d"); ctx.clearRect(0, 0, width, height)
                if (!panel.available) return
                var bins = panel.mode === "spectrum" ? panel.spectrum : panel.waveform
                var begin = Math.max(0, Math.floor(panel.secondsAt(panel.firstFrame) / panel.duration * bins.length) - 1)
                var end = Math.min(bins.length, Math.ceil(panel.secondsAt(panel.firstFrame + panel.frameSpan) / panel.duration * bins.length) + 1)
                if (panel.mode === "spectrum") {
                    // Paint only visible real FFT cells. A continuous dB palette
                    // avoids the old five-level quantization; no invented detail.
                    for (var i = begin; i < end; ++i) {
                        var left = panel.xAt(i / bins.length * panel.duration)
                        var right = panel.xAt((i + 1) / bins.length * panel.duration)
                        var bands = bins[i]
                        for (var j = 0; j < bands.length; ++j) {
                            var v = Math.max(0, Math.min(1, Number(bands[j])))
                            ctx.fillStyle = Qt.rgba((23 + 232 * v) / 255, (17 + 218 * v * v) / 255, (38 + 183 * Math.sqrt(v)) / 255, 1)
                            ctx.fillRect(left, height - (j + 1) * height / bands.length, Math.max(1, right-left), Math.ceil(height / bands.length))
                        }
                    }
                } else {
                    ctx.strokeStyle = "#514566"; ctx.beginPath(); ctx.moveTo(0,height/2); ctx.lineTo(width,height/2); ctx.stroke()
                    ctx.strokeStyle = "#C9A5FF"; ctx.beginPath()
                    for (var k = begin; k < end; ++k) {
                        var px = panel.xAt((k + .5) / bins.length * panel.duration)
                        ctx.moveTo(px, (1-Number(bins[k][1]))*height/2); ctx.lineTo(px,(1-Number(bins[k][0]))*height/2)
                    }
                    ctx.stroke()
                }
            }
        }
        Rectangle {
            objectName: "audioPlayhead"; visible: panel.available
            x: ((seekArea.scrubbing ? seekArea.dragFrame : panel.playheadFrame) - panel.firstFrame) / panel.frameSpan * parent.width
            width: 2; height: parent.height; color: "#F384B0"
        }
        Label {
            objectName: "audioStatusMessage"; anchors.centerIn: parent; width: parent.width - 24
            visible: !panel.available; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: panel.status === "loading" ? "正在分析真实音轨…" : panel.status === "error" ? "音频分析失败：" + (panel.analysis.error || "无法读取音轨") : panel.status === "no_audio" ? "素材没有可分析的音轨" : "载入音频或视频后显示真实音轨"
            color: "#C9B9DE"; font.pixelSize: 11
        }
        MouseArea {
            id: seekArea; objectName: "audioSeekArea"; anchors.fill: parent
            enabled: panel.available; acceptedButtons: Qt.LeftButton | Qt.RightButton; preventStealing: true
            cursorShape: Qt.SizeHorCursor
            property bool scrubbing: false
            property int dragFrame: 1
            function finish() { if (scrubbing) panel.controller.command("media.scrubEnd", {frame: dragFrame}); scrubbing = false }
            onPressed: mouse => {
                if (mouse.button === Qt.RightButton) { audioMenu.popup(); return }
                forceActiveFocus(); dragFrame = panel.frameAt(mouse.x); scrubbing = true
                panel.controller.command("media.scrubBegin", {frame: dragFrame})
            }
            onPositionChanged: mouse => { if (pressed && scrubbing) { dragFrame = panel.frameAt(mouse.x); panel.controller.command("media.scrub", {frame: dragFrame}) } }
            onReleased: finish()
            onCanceled: finish()
            onWheel: wheel => { if (panel.timelineView) panel.timelineView.wheelInput(wheel, false, wheel.x) }
        }
    }
    Menu {
        id: audioMenu; objectName: "audioModeMenu"
        MenuItem { objectName: "audioWaveformMode"; text: "波形"; checkable: true; checked: panel.mode === "waveform"; onTriggered: panel.controller.command("view.layout", {audioMode:"waveform"}) }
        MenuItem { objectName: "audioSpectrumMode"; text: "频谱"; checkable: true; checked: panel.mode === "spectrum"; onTriggered: panel.controller.command("view.layout", {audioMode:"spectrum"}) }
    }
}
