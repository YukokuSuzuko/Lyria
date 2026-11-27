import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia
import Lyria 1.0

Panel {
    id: panel
    color: "transparent"
    border.width: 0
    required property var controller
    required property var media
    required property var state
    property real controlsExtraHeight: 0
    property real controlsResizePreview: NaN
    readonly property real requestedControlsHeight: isNaN(controlsResizePreview) ? controlsExtraHeight : controlsResizePreview
    readonly property real compression: Math.min(1, Math.max(0, -requestedControlsHeight / 52))
    readonly property int playerButtonHeight: Math.round(31 - 7 * compression)
    readonly property bool compactControls: true // Reflow depends on available width, never a drag-height threshold.
    readonly property real effectiveControlsExtraHeight: Math.max(-52, Math.min(0, requestedControlsHeight))
    signal controlsHeightEdited(real value)
    property bool review: false
    property bool windowActive: true
    property bool cropVisible: false
    readonly property bool hasStoredRegions: !!state.roi || (state.regionCards || []).length > 0
    onShowRoiToolsChanged: {
        roiSelecting = false
        cropVisible = showRoiTools && hasStoredRegions
    }
    onHasStoredRegionsChanged: if (showRoiTools) cropVisible = hasStoredRegions
    property bool roiSelecting: false
    property bool drawingNewRegion: false
    property var stableMarkers: []
    property string markersSignature: ""
    property var stableSubtitleRows: []
    property var subtitlePreviewOffsets: []
    property string subtitlePreviewLayoutKey: ""
    property bool subtitlePreservePreviewLayout: false
    property real subtitleDragX: 0
    property real subtitleDragY: 0
    property bool subtitleDragging: false
    property bool subtitleCommitPending: false
    property string subtitleCommitCues: ""
    property bool subtitleSnapX: false
    property bool subtitleSnapY: false
    property string subtitleSignature: ""
    property string subtitleCueSignature: ""
    property string dismissedLayoutWarning: ""
    readonly property string layoutWarningKey: String(media.subtitleLayoutRevision || 0) + ":" + (media.subtitleLayoutError || "")
    readonly property var selected: state.selection ? state.selection.current || ({}) : ({})
    readonly property var previousCue: state.selection ? state.selection.previousTimed || state.selection.previous || ({}) : ({})
    readonly property var nextCue: state.selection ? state.selection.next || ({}) : ({})
    readonly property var settings: state.settings || ({})
    readonly property var fallbackStyle: settings.subtitleFallbackStyle || ({})
    readonly property bool ready: !!media.ready
    readonly property int frame: media.displayedFrame || 0
    readonly property int total: media.totalFrames || 0
    readonly property bool showRoiTools: !review && (state.entryMode || "ocr") === "ocr" && !nonVideo
    readonly property bool nonVideo: media.mediaKind === "audio" || media.mediaKind === "virtual"
    readonly property bool hasPicture: !nonVideo && ready && frame > 0 && video.contentRect.width > 0 && video.contentRect.height > 0
    onWindowActiveChanged: if (!windowActive) { if (seekMouse.dragging) seekMouse.finish(); cancelSubtitleDrag() }
    onVisibleChanged: if (!visible) { if (seekMouse.dragging) seekMouse.finish(); cancelSubtitleDrag() }
    onHasPictureChanged: if (!hasPicture) cancelSubtitleDrag()
    onRoiSelectingChanged: { if (roiSelecting) cancelSubtitleDrag(); else drawingNewRegion = false }
    onSettingsChanged: if (settings.subtitlePreview === false) cancelSubtitleDrag()
    function beginRoiSetup() { if (!ready || !showRoiTools) return; regionSetup.open() }
    function cmd(name, payload) { return controller.command(name, payload || {}) }
    function cancelSubtitleDrag(keepPreviewLayout) {
        if (!keepPreviewLayout) subtitlePreservePreviewLayout = false
        subtitleDragging = false; subtitleCommitPending = false
        subtitleDragX = 0; subtitleDragY = 0; subtitleSnapX = false; subtitleSnapY = false
    }
    function scheduleSubtitlePreviewLayout() { Qt.callLater(layoutSubtitlePreview) }
    function layoutSubtitlePreview() {
        if (subtitleDragging || subtitleCommitPending) return
        var rows = [], keyRows = []
        var area = video.contentRect, gap = 4
        for (var n = 0; n < subtitleRepeater.count; ++n) {
            var item = subtitleRepeater.itemAt(n)
            rows.push({top: item.sourceTop, height: item.height})
            keyRows.push([item.sourceTop, item.height])
        }
        var key = JSON.stringify([subtitleCueSignature, area.x, area.y, area.width, area.height, keyRows])
        if (key === subtitlePreviewLayoutKey) { subtitlePreservePreviewLayout = false; return }
        // Only simultaneous cues receive reading offsets. A single cue always
        // returns to its exact user/export anchor, including intentional overflow.
        var offsets = [], occupied = [], needsPacking = false
        function fits(top, height) {
            if (top < area.y - .01 || top + height > area.y + area.height + .01) return false
            for (var k = 0; k < occupied.length; ++k)
                if (top < occupied[k].bottom + gap - .01 && top + height > occupied[k].top - gap + .01) return false
            return true
        }
        var preserve = subtitlePreservePreviewLayout && subtitlePreviewOffsets.length === rows.length
        if (preserve) {
            for (n = 0; n < rows.length; ++n) {
                var kept = rows[n].top + subtitlePreviewOffsets[n]
                if (!fits(kept, rows[n].height)) { preserve = false; break }
                occupied.push({top: kept, bottom: kept + rows[n].height})
            }
        }
        occupied = []
        for (n = 0; n < rows.length; ++n) {
            var row = rows[n], chosen = row.top
            if (rows.length > 1) {
                if (preserve) chosen += subtitlePreviewOffsets[n]
                else {
                    var candidates = [Math.max(area.y, Math.min(area.y + area.height - row.height, row.top))]
                    for (var j = 0; j < occupied.length; ++j) {
                        candidates.push(occupied[j].top - gap - row.height)
                        candidates.push(occupied[j].bottom + gap)
                    }
                    var distance = Infinity, found = false
                    for (j = 0; j < candidates.length; ++j) {
                        var candidate = candidates[j], d = Math.abs(candidate - row.top)
                        if (fits(candidate, row.height) && (d < distance || (d === distance && candidate < chosen))) {
                            chosen = candidate; distance = d; found = true
                        }
                    }
                    if (!found) {
                        needsPacking = true
                        // Establish a stable visual order first. The second pass
                        // below repairs fragmented space whenever all rows fit.
                        if (occupied.length) chosen = Math.min.apply(null, occupied.map(function(r) { return r.top })) - gap - row.height
                    }
                }
            }
            offsets.push(chosen - row.top)
            occupied.push({index: n, top: chosen, bottom: chosen + row.height})
        }
        var totalHeight = Math.max(0, rows.length - 1) * gap
        for (n = 0; n < rows.length; ++n) totalHeight += rows[n].height
        if (needsPacking && rows.length > 1 && totalHeight <= area.height) {
            // A short row at the anchor can divide ample space into two gaps
            // too small for a tall row. Pack the full group, keeping its visual
            // order, before considering the text genuinely too tall to fit.
            var order = occupied.slice().sort(function(a, b) { return a.top - b.top || a.index - b.index })
            var packedTop = Math.max(area.y, Math.min(area.y + area.height - totalHeight, order[0].top))
            for (n = 0; n < order.length; ++n) {
                var index = order[n].index
                offsets[index] = packedTop - rows[index].top
                packedTop += rows[index].height + gap
            }
        }
        subtitlePreviewOffsets = offsets
        subtitlePreviewLayoutKey = key
        subtitlePreservePreviewLayout = false
    }
    function formatTime(seconds) { var n = Math.max(0, Math.floor(seconds)); var h = Math.floor(n / 3600); var m = Math.floor(n / 60) % 60; var s = n % 60; function pad(v) { return (v < 10 ? "0" : "") + v } return (h ? pad(h) + ":" : "") + pad(m) + ":" + pad(s) }
    function syncMarkers() {
        var values = state.markers || []
        var signature = JSON.stringify(values)
        if (signature !== markersSignature) { markersSignature = signature; stableMarkers = values }
    }
    function syncSubtitles() {
        var cues = JSON.stringify(media.activeCueIds || [])
        // Repeated lyric text can have identical layout rows in two cues.
        // Identity, not only text/geometry, invalidates a grabbed preview.
        if (cues !== subtitleCueSignature) { cancelSubtitleDrag(); subtitleCueSignature = cues; subtitlePreviewOffsets = []; subtitlePreviewLayoutKey = "" }
        var values = media.subtitleRows || []
        var signature = JSON.stringify(values)
        // Keep the exact dragged pixels while the committed layout is built.
        // A different active cue, failure or source must never inherit them.
        if (subtitleCommitPending && signature === "[]" && media.subtitleLayoutPending &&
                subtitleCommitCues === JSON.stringify(media.activeCueIds || []) && hasPicture) return
        var committedLayout = subtitleCommitPending && signature !== "[]"
        if (committedLayout) cancelSubtitleDrag(true)
        // Repeater replacement destroys the grabbed MouseArea without always
        // sending onCanceled. Never carry its uncommitted offset to a new cue.
        if (signature !== subtitleSignature) { cancelSubtitleDrag(committedLayout); subtitleSignature = signature; stableSubtitleRows = values }
        scheduleSubtitlePreviewLayout()
    }
    onStateChanged: syncMarkers()
    onMediaChanged: syncSubtitles()
    Component.onCompleted: { syncMarkers(); syncSubtitles() }
    implicitHeight: 430
    readonly property real minimumPanelHeight: previewHeader.implicitHeight + (roiSetupHint.visible ? roiSetupHint.implicitHeight + 3 : 0) + (review ? 24 : Math.min(150, videoViewport.Layout.preferredHeight)) + controlContent.implicitHeight + 35
    ColumnLayout {
        id: previewContents
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8; anchors.topMargin: 4; spacing: 3
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 0
        RowLayout {
            id: previewHeader; objectName: "previewHeader"
            Layout.fillWidth: true
            Label { objectName: "previewHeading"; textFormat: Text.PlainText; text: (panel.nonVideo ? "播放与定位" : "画面预览") + (panel.media.subtitleTextRoleLabel ? " · " + panel.media.subtitleTextRoleLabel : ""); font.pixelSize: 14; font.weight: Font.DemiBold; color: Theme.ink; Layout.minimumWidth: implicitWidth; Layout.fillWidth: !hiddenCueHint.visible }
            // Reuse the existing title line: a transient hint must neither
            // reserve empty control space nor resize the painted video.
            Label {
                id: hiddenCueHint; objectName: "hiddenCueHint"
                visible: (panel.media.hiddenCueCount || 0) > 0
                textFormat: Text.PlainText; text: visible ? panel.media.frameMessage || "" : ""
                color: Theme.amber; font.pixelSize: 10
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 0
                elide: Text.ElideRight; maximumLineCount: 1
                ToolTip.visible: hintHover.hovered && hiddenCueHint.visible; ToolTip.text: text
                HoverHandler { id: hintHover }
            }
            ThemeDialog {
                id: regionSetup; objectName: "regionSetupDialog"; parent: Overlay.overlay; anchors.centerIn: parent; width: Math.min(380, parent.width - 24); modal: true; title: "设置字幕识别选区"
                standardButtons: Dialog.Cancel
                contentItem: ColumnLayout {
                    Label { text: "可自动检测硬字幕位置，也可直接在画面上手动框选。"; color: Theme.label; wrapMode: Text.Wrap; Layout.preferredWidth: 320 }
                    Flow {
                        Layout.fillWidth: true; spacing: 5
                        ActionButton { objectName: "autoRoiChoice"; text: "自动检测区域"; compact: true; onClicked: { regionSetup.close(); panel.cropVisible = true; panel.cmd("roi.auto") } }
                        ActionButton { objectName: "drawNewRegionChoice"; text: "＋ 手动框选"; compact: true; enabled: (panel.state.regionCards || []).length < 4; onClicked: { regionSetup.close(); panel.drawingNewRegion = true; panel.roiSelecting = true; panel.cropVisible = true } }
                        ActionButton { text: "清空选区"; compact: true; enabled: (panel.state.regionCards || []).length > 0; onClicked: { regionSetup.close(); panel.cmd("roi.multi", {action:"clear"}) } }
                        ActionButton { objectName: "manualRoiChoice"; text: "手动框选"; compact: true; visible: !panel.state.ocrMultiRegion || (panel.state.multiRois || []).length === 1; onClicked: { regionSetup.close(); panel.roiSelecting = true; panel.cropVisible = true } }
                        ActionButton { text: "管理选区…"; compact: true; onClicked: { regionSetup.close(); panel.cropVisible = true; panel.cmd("roi.multi") } }
                    }
                }
            }
        }
        Label {
            id: roiSetupHint; objectName: "roiSetupHint"
            visible: panel.showRoiTools && panel.ready && (!panel.state.roi || panel.roiSelecting)
            Layout.fillWidth: true
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            text: panel.roiSelecting ? "请在画面上拖动鼠标框住硬字幕区域" : "尚未设置字幕选区。可自动检测硬字幕位置，也可直接在画面上手动框选。"
            color: Theme.lilacDark; font.pixelSize: 11
        }
        Item {
            visible: panel.ready && !panel.nonVideo
            Layout.fillHeight: true; Layout.preferredHeight: 0
        }
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.maximumHeight: panel.ready && !panel.nonVideo ? Math.max(Layout.minimumHeight, Layout.preferredHeight) : Infinity
            Layout.preferredHeight: panel.ready && !panel.nonVideo && panel.media.width > 0 ? width * panel.media.height / panel.media.width + tagClearance : 150
            Layout.minimumHeight: panel.review ? 24 : Math.max(24, Math.min(150, Layout.preferredHeight))
            id: videoViewport; objectName: "videoViewport"
            clip: true
            readonly property bool showRegionTags: panel.showRoiTools && panel.cropVisible && ((panel.state.regionCards || []).length > 0 || panel.roiSelecting)
            readonly property int tagClearance: showRegionTags && (panel.state.regionCards || []).some(r => r.y * height < 21 && (r.y+r.height) * height > height-21) ? 22 : 0
            // Fit the complete picture, including intrinsic bars, at the center
            // of the available viewport. No cropping or bottom-only anchoring.
            Rectangle {
            id: stage
            objectName: "videoStage"
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            height: panel.ready && !panel.nonVideo && panel.media.width > 0
                    ? Math.min(Math.max(1, parent.height - videoViewport.tagClearance), width * panel.media.height / panel.media.width) : parent.height
            color: "transparent"; radius: 0
            border.width: 0
            clip: !videoViewport.showRegionTags
            VideoOutput {
                id: video
                objectName: "videoPreview"
                anchors.fill: parent
                fillMode: VideoOutput.PreserveAspectFit
                onContentRectChanged: { panel.cancelSubtitleDrag(); panel.scheduleSubtitlePreviewLayout() }
                Component.onCompleted: panel.controller.attachVideoSink(video.videoSink)
            }
            FrameSurface {
                anchors.fill: video
                visible: panel.controller ? panel.controller.softwareRendering : false
                Component.onCompleted: panel.controller.attachFrameSurface(this)
            }
            Canvas {
                id: cornerMask
                objectName: "videoRoundedCorners"
                // Canvas textures use integer sizes. Extend the paint surface
                // past fractional video edges so no uncovered square strip can
                // remain at the right/bottom edge at fractional Windows DPI.
                x: Math.floor(video.contentRect.x) - 1; y: Math.floor(video.contentRect.y) - 1
                width: Math.ceil(video.contentRect.x + video.contentRect.width - x) + 1
                height: Math.ceil(video.contentRect.y + video.contentRect.height - y) + 1
                readonly property rect pictureRect: Qt.rect(video.contentRect.x-x, video.contentRect.y-y, video.contentRect.width, video.contentRect.height)
                onPictureRectChanged: requestPaint()
                visible: panel.ready && panel.settings.playerRounded !== false && width > 24 && height > 24
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onVisibleChanged: if (visible) requestPaint()
                onPaint: {
                    var c = getContext("2d"); c.reset(); c.fillStyle = Theme.canvas
                    var p = pictureRect, l = p.x, t = p.y, rr = l+p.width, b = t+p.height
                    var r = Math.min(12, p.width / 2, p.height / 2)
                    c.beginPath(); c.moveTo(l, t+r); c.quadraticCurveTo(l, t, l+r, t); c.lineTo(l+r, 0); c.lineTo(0, 0); c.lineTo(0, t+r); c.closePath(); c.fill()
                    c.beginPath(); c.moveTo(rr-r, t); c.quadraticCurveTo(rr, t, rr, t+r); c.lineTo(width, t+r); c.lineTo(width, 0); c.lineTo(rr-r, 0); c.closePath(); c.fill()
                    c.beginPath(); c.moveTo(l, b-r); c.quadraticCurveTo(l, b, l+r, b); c.lineTo(l+r, height); c.lineTo(0, height); c.lineTo(0, b-r); c.closePath(); c.fill()
                    c.beginPath(); c.moveTo(rr-r, b); c.quadraticCurveTo(rr, b, rr, b-r); c.lineTo(width, b-r); c.lineTo(width, height); c.lineTo(rr-r, height); c.closePath(); c.fill()
                }
            }
            Column {
                anchors.centerIn: parent; spacing: 8; visible: !panel.ready || panel.nonVideo
                Rectangle {
                    width: 52; height: 52; radius: 15; color: Theme.lilacSoft; anchors.horizontalCenter: parent.horizontalCenter
                    Image { anchors.centerIn: parent; width: 25; height: 25; source: "../assets/icons/sub.svg" }
                }
                Label { textFormat: Text.PlainText; text: panel.media.mediaKind === "virtual" ? "纯时间轴模式" : panel.media.mediaKind === "audio" ? "音频打轴模式" : "让每一句歌词，落在正确的画面"; color: Theme.label; font.pixelSize: 13; anchors.horizontalCenter: parent.horizontalCenter }
                Label { textFormat: Text.PlainText; text: panel.nonVideo ? "10 毫秒精度时间轴 · 支持波形与频谱对轴" : panel.media.status || "导入本地音视频即可开始 · 本地处理不上传"; color: Theme.muted; font.pixelSize: 11; anchors.horizontalCenter: parent.horizontalCenter }
            }
            OcrRegionOverlay {
                z: panel.roiSelecting ? 0 : 4
                x: video.contentRect.x; y: video.contentRect.y; width: video.contentRect.width; height: video.contentRect.height
                visible: panel.showRoiTools && panel.ready && panel.cropVisible && ((panel.state.regionCards || []).length > 0)
                editable: panel.showRoiTools && visible && !panel.roiSelecting && !panel.state.candidateReadOnly && !(panel.state.candidateTask || {}).running
                controller: panel.controller; regions: panel.state.regionCards || []
            }
            Rectangle {
                id: crop
                objectName: "roiOverlay"
                opacity: (panel.state.regionCards || []).length ? 0 : 1
                visible: panel.showRoiTools && panel.ready && panel.cropVisible && !!panel.state.roi && !panel.state.ocrMultiRegion
                property var roi: panel.state.roi || ({x: 0.1, y: 0.7, width: 0.8, height: 0.2})
                x: video.contentRect.x + roi.x * video.contentRect.width
                y: video.contentRect.y + roi.y * video.contentRect.height
                width: roi.width * video.contentRect.width; height: roi.height * video.contentRect.height
                color: "#108562C5"; radius: 4
                Canvas {
                    anchors.fill: parent
                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()
                    onPaint: {
                        var ctx = getContext("2d"); ctx.reset(); ctx.strokeStyle = "#C1A7F2"; ctx.lineWidth = 1; ctx.setLineDash([5, 4])
                        var r = Math.min(4, width / 2, height / 2)
                        ctx.beginPath(); ctx.moveTo(r, 0.5); ctx.lineTo(width - r, 0.5)
                        ctx.quadraticCurveTo(width - 0.5, 0.5, width - 0.5, r); ctx.lineTo(width - 0.5, height - r)
                        ctx.quadraticCurveTo(width - 0.5, height - 0.5, width - r, height - 0.5); ctx.lineTo(r, height - 0.5)
                        ctx.quadraticCurveTo(0.5, height - 0.5, 0.5, height - r); ctx.lineTo(0.5, r)
                        ctx.quadraticCurveTo(0.5, 0.5, r, 0.5); ctx.closePath(); ctx.stroke()
                    }
                }
                Rectangle { x: -1; y: -1; width: 11; height: 2; color: "#DECFFE" }
                Rectangle { x: -1; y: -1; width: 2; height: 11; color: "#DECFFE" }
                Rectangle { anchors.right: parent.right; anchors.bottom: parent.bottom; width: 11; height: 2; color: "#DECFFE" }
                Rectangle { anchors.right: parent.right; anchors.bottom: parent.bottom; width: 2; height: 11; color: "#DECFFE" }
                Badge { text: "OCR 选区"; y: -27; fill: "#EADFFB" }
            }
            MouseArea {
                id: roiSelector
                anchors.fill: parent
                visible: panel.showRoiTools && panel.roiSelecting && panel.ready
                cursorShape: Qt.CrossCursor
                property real startX: 0
                property real startY: 0
                property real endX: 0
                property real endY: 0
                function boundedX(x) { return Math.max(video.contentRect.x, Math.min(video.contentRect.x + video.contentRect.width, x)) }
                function boundedY(y) { return Math.max(video.contentRect.y, Math.min(video.contentRect.y + video.contentRect.height, y)) }
                onPressed: mouse => { startX = endX = boundedX(mouse.x); startY = endY = boundedY(mouse.y) }
                onPositionChanged: mouse => { if (pressed) { endX = boundedX(mouse.x); endY = boundedY(mouse.y) } }
                onReleased: {
                    if (Math.abs(endX - startX) > 3 && Math.abs(endY - startY) > 3) {
                        var x = (Math.min(startX,endX)-video.contentRect.x)/video.contentRect.width
                        var y = (Math.min(startY,endY)-video.contentRect.y)/video.contentRect.height
                        var w = Math.abs(endX-startX)/video.contentRect.width
                        var h = Math.abs(endY-startY)/video.contentRect.height
                        var rect = [x,y,Math.min(1,x+w),Math.min(1,y+h)]
                        var active = panel.state.multiRois || []
                        if (panel.drawingNewRegion) panel.cmd("roi.multi", {action:"add", box:rect})
                        else if (panel.state.ocrMultiRegion && active.length === 1) panel.cmd("roi.multi", {action:"geometry", id:active[0].id, box:rect})
                        else panel.cmd("roi.set", {x:x,y:y,width:w,height:h})
                        panel.roiSelecting = false
                    }
                }
                Rectangle { visible: roiSelector.pressed; x: Math.min(roiSelector.startX, roiSelector.endX); y: Math.min(roiSelector.startY, roiSelector.endY); width: Math.abs(roiSelector.endX - roiSelector.startX); height: Math.abs(roiSelector.endY - roiSelector.startY); color: "#268562C5"; border.color: "#DECFFE"; border.width: 2; radius: 3 }
                Badge { anchors.horizontalCenter: parent.horizontalCenter; y: 12; text: "在视频画面内拖动框选字幕区域" }
            }
            Rectangle {
                visible: false // Keep frame state available, but do not cover the picture.
                x: 10; y: 10; width: frameCaption.implicitWidth + 18; height: 24; radius: 6; color: "#B32D263B"
                Label { objectName: "presentedFrameCaption"; textFormat: Text.PlainText; id: frameCaption; anchors.centerIn: parent; text: "当前画面  F " + panel.frame + (panel.media.frameMessage ? " · " + panel.media.frameMessage : ""); color: "#EFE5FF"; font.pixelSize: 11 }
            }
            Label { textFormat: Text.PlainText;
                visible: false // Seeking remains active; only its visual status is hidden.
                anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                text: (panel.media.playPending ? "定位后播放 · " : "正在定位 ") + "F" + panel.media.requestedFrame; color: "#E8DDF8"; font.pixelSize: 10
            }
            Label { textFormat: Text.PlainText;
                // The stage can contain large letter/pillar boxes. During
                // asynchronous layout (or its error fallback), keep the text
                // on the actual picture just like the fully laid-out rows.
                x: video.contentRect.x + video.contentRect.width * (panel.fallbackStyle.x || .5) - width/2
                y: video.contentRect.y + video.contentRect.height * (panel.fallbackStyle.y || .9) - (panel.fallbackStyle.alignment === 8 ? 0 : implicitHeight)
                width: video.contentRect.width * 0.87
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: panel.media.subtitleText || ""
                objectName: "subtitleFallback"
                visible: panel.hasPicture && panel.settings.subtitlePreview !== false && !!panel.media.subtitleText && panel.stableSubtitleRows.length === 0
                color: "white"; style: Text.Outline; styleColor: "#29213A"
                font.family: panel.fallbackStyle.font || Theme.fontFamily
                font.pixelSize: Math.max(1, (panel.fallbackStyle.size || .044) * video.contentRect.height)
            }
            Repeater {
                id: subtitleRepeater
                model: panel.hasPicture && panel.settings.subtitlePreview !== false ? panel.stableSubtitleRows : []
                Label { textFormat: Text.PlainText;
                    required property var modelData
                    required property int index
                    objectName: "previewSubtitle_" + index
                    width: video.contentRect.width * 0.87
                    x: video.contentRect.x + (modelData.xNormalized || 0.5) * video.contentRect.width - width / 2 + panel.subtitleDragX
                    readonly property real sourceTop: video.contentRect.y + modelData.yNormalized * video.contentRect.height - (modelData.alignment === 2 ? implicitHeight : 0)
                    y: sourceTop + (panel.subtitlePreviewOffsets[index] || 0) + panel.subtitleDragY
                    onHeightChanged: panel.scheduleSubtitlePreviewLayout()
                    text: modelData.text || ""
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: modelData.color || "white"; style: Text.Outline; styleColor: "#29213A"
                    font.bold: modelData.bold === true
                    font.italic: modelData.italic === true
                    font.family: modelData.fontFamily || Theme.fontFamily
                    font.pixelSize: Math.max(1, modelData.fontSizeNormalized * video.contentRect.height)
                }
            }
            // A stationary receiver keeps pointer displacement independent of snapped text.
            MouseArea {
                id: subtitleDragArea
                // Retain accepted subtitle gestures inside the outer workspace Flickable.
                preventStealing: true
                anchors.fill: parent
                enabled: !panel.roiSelecting && !panel.subtitleCommitPending && !panel.state.candidateReadOnly && !(panel.state.alignmentPreview || {}).active && video.contentRect.width > 0 && video.contentRect.height > 0
                hoverEnabled: true
                cursorShape: pressed || hitRow(mouseX, mouseY) >= 0 ? Qt.SizeAllCursor : Qt.ArrowCursor
                function hitRow(x, y) {
                    for (var n = subtitleRepeater.count - 1; n >= 0; --n) {
                        var row = subtitleRepeater.itemAt(n)
                        var w = Math.min(row.width, Math.max(24, row.implicitWidth))
                        if (x >= row.x + (row.width-w)/2 && x <= row.x + (row.width+w)/2 &&
                                y >= row.y && y <= row.y+row.height) return n
                    }
                    return -1
                }
                objectName: "subtitleDragArea"
                property point startPoint: Qt.point(0,0)
                property point startCenter: Qt.point(0,0)
                property real startTop: 0
                property real startBottom: 0
                property int lockedAxis: 0
                onPressed: mouse => {
                    if (hitRow(mouse.x, mouse.y) < 0) { mouse.accepted = false; return }
                    startPoint = Qt.point(mouse.x, mouse.y)
                    panel.cancelSubtitleDrag(); lockedAxis = 0
                    var top = Infinity, bottom = -Infinity
                    for (var n = 0; n < subtitleRepeater.count; ++n) {
                        var row = subtitleRepeater.itemAt(n)
                        top = Math.min(top, row.y); bottom = Math.max(bottom, row.y + row.height)
                    }
                    startTop = top; startBottom = bottom
                    var first = subtitleRepeater.itemAt(0)
                    startCenter = Qt.point(first.x + first.width / 2, (top + bottom) / 2)
                    panel.subtitleDragging = true
                }
                onPositionChanged: mouse => {
                    if (!pressed || !panel.subtitleDragging) return
                    var pt = Qt.point(mouse.x, mouse.y)
                    var dx = pt.x - startPoint.x, dy = pt.y - startPoint.y
                    if (mouse.modifiers & Qt.ShiftModifier) {
                        if (!lockedAxis && Math.max(Math.abs(dx), Math.abs(dy)) >= 3) lockedAxis = Math.abs(dx) >= Math.abs(dy) ? 1 : 2
                        if (lockedAxis === 1) dy = 0
                        if (lockedAxis === 2) dx = 0
                    } else lockedAxis = 0
                    var cx = video.contentRect.x + video.contentRect.width / 2
                    var cy = video.contentRect.y + video.contentRect.height / 2
                    panel.subtitleSnapX = lockedAxis !== 2 && Math.abs(startCenter.x + dx - cx) <= (panel.subtitleSnapX ? 12 : 8)
                    panel.subtitleSnapY = lockedAxis !== 1 && Math.abs(startCenter.y + dy - cy) <= (panel.subtitleSnapY ? 12 : 8)
                    if (panel.subtitleSnapX) dx = cx - startCenter.x
                    if (panel.subtitleSnapY) dy = cy - startCenter.y
                    // Match the backend anchor limits during the gesture,
                    // so release cannot jump back from an out-of-range preview.
                    var ax = (panel.fallbackStyle.x || .5) * video.contentRect.width
                    var ay = (panel.fallbackStyle.y || .9) * video.contentRect.height
                    panel.subtitleDragX = Math.max(.05 * video.contentRect.width - ax, Math.min(.95 * video.contentRect.width - ax, dx))
                    var minY = .05 * video.contentRect.height - ay, maxY = .95 * video.contentRect.height - ay
                    // Keep the entire readable overlap group inside the picture.
                    if (subtitleRepeater.count > 1 && startBottom - startTop <= video.contentRect.height) {
                        minY = Math.max(minY, video.contentRect.y - startTop)
                        maxY = Math.min(maxY, video.contentRect.y + video.contentRect.height - startBottom)
                    }
                    panel.subtitleDragY = Math.max(minY, Math.min(maxY, dy))
                }
                onReleased: {
                    var commit = panel.subtitleDragging
                    var dx = panel.subtitleDragX / video.contentRect.width, dy = panel.subtitleDragY / video.contentRect.height
                    panel.subtitleDragging = false; panel.subtitleSnapX = false; panel.subtitleSnapY = false
                    if (commit && (Math.abs(dx) > 0.001 || Math.abs(dy) > 0.001)) {
                        panel.subtitleCommitCues = JSON.stringify(panel.media.activeCueIds || [])
                        panel.subtitleCommitPending = true
                        panel.subtitlePreservePreviewLayout = true
                        if (!panel.cmd("subtitle.move", {dx: dx, dy: dy})) panel.cancelSubtitleDrag()
                    } else panel.cancelSubtitleDrag()
                }
                onCanceled: panel.cancelSubtitleDrag()
            }

            Rectangle {
                objectName: "subtitleCenterGuideX"
                visible: panel.subtitleDragging && panel.subtitleSnapX
                x: video.contentRect.x + video.contentRect.width / 2 - width / 2
                y: video.contentRect.y; width: 1; height: video.contentRect.height; color: "#B5DECFFE"
            }
            Rectangle {
                objectName: "subtitleCenterGuideY"
                visible: panel.subtitleDragging && panel.subtitleSnapY
                x: video.contentRect.x; y: video.contentRect.y + video.contentRect.height / 2 - height / 2
                width: video.contentRect.width; height: 1; color: "#B5DECFFE"
            }
            Badge { objectName: "subtitleLayoutPendingBadge"; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 8; visible: panel.hasPicture && !!panel.media.subtitleLayoutPending && panel.settings.subtitlePreview !== false; text: "更新字幕预览…" }
            Row {
                anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 8; spacing: 3
                visible: panel.hasPicture && !panel.media.subtitleLayoutPending && !!panel.media.subtitleLayoutError && panel.settings.subtitlePreview !== false && panel.dismissedLayoutWarning !== panel.layoutWarningKey
                Badge { objectName: "subtitleLayoutErrorBadge"; text: panel.stableSubtitleRows.length ? "字幕超出画面 · 点击调整样式" : "字幕排版异常 · 点击调整样式"; ToolTip.visible: layoutWarningMouse.containsMouse && panel.windowActive; ToolTip.text: panel.media.subtitleLayoutError || ""; MouseArea { id: layoutWarningMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: panel.cmd("settings.subtitleStyle") } }
                ActionButton { objectName: "dismissSubtitleLayoutWarning"; text: "×"; compact: true; implicitWidth: 25; implicitHeight: 25; tip: "关闭本次样式提示"; Accessible.name: "关闭本次样式提示"; onClicked: panel.dismissedLayoutWarning = panel.layoutWarningKey }
            }
        }
        }
        Rectangle {
            id: playerControls
            objectName: "playerControls"
            Layout.fillWidth: true
            implicitHeight: controlContent.implicitHeight + 10 - 5 * panel.compression
            Layout.minimumHeight: implicitHeight
            Layout.maximumHeight: implicitHeight
            radius: 12
            color: "#F8F6FC"
            // Frosted tint, internal reflected light and a bright beveled rim.
            // The application window stays opaque; no platform blur is required.
            gradient: panel.settings.playerGlass !== false ? glassTint : null
            Gradient { id: glassTint; objectName: "playerGlassGradient"
                GradientStop { position: 0; color: "#F5F2FF" }
                GradientStop { position: 0.46; color: "#DDE4E0F5" }
                GradientStop { position: 1; color: "#F7EEF7" }
            }
            Rectangle { anchors.fill: parent; anchors.margins: 2; radius: 10; color: "transparent"; border.color: "#A8FFFFFF"; visible: panel.settings.playerGlass !== false }
            // No full-width white highlight: it read as a stray separator above playback.
            border.width: panel.settings.playerGlass !== false ? 1 : 0
            border.color: "#80FFFFFF"
            MouseArea {
                id: controlsResizeHandle; objectName: "playerControlsResizeHandle"
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                height: 8; z: 30; hoverEnabled: true; preventStealing: true
                cursorShape: Qt.SizeVerCursor
                property real pressY: 0
                property real pressHeight: 0
                onPressed: mouse => { pressY = mapToItem(null, mouse.x, mouse.y).y; pressHeight = panel.effectiveControlsExtraHeight; panel.controlsResizePreview = pressHeight }
                onPositionChanged: mouse => { if (pressed) panel.controlsResizePreview = Math.max(-52, Math.min(0, pressHeight + pressY - mapToItem(null, mouse.x, mouse.y).y)) }
                onReleased: { panel.controlsHeightEdited(panel.effectiveControlsExtraHeight); panel.controlsResizePreview = NaN }
                onCanceled: panel.controlsResizePreview = NaN
                onDoubleClicked: { panel.controlsResizePreview = NaN; panel.controlsHeightEdited(0) }
                ToolTip.visible: containsMouse && !pressed; ToolTip.text: "向下拖动可收紧控制栏以放大画面，双击恢复默认"; ToolTip.delay: 650
                Rectangle { anchors.centerIn: parent; width: 40; height: 3; radius: 1.5; color: Theme.lilac; opacity: parent.containsMouse || parent.pressed ? 1 : 0.7 }
            }
            ColumnLayout {
                id: controlContent
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 5
                anchors.bottomMargin: 5 - 3 * panel.compression
                height: implicitHeight; spacing: 1
        Item {
            id: transportSummary
            objectName: "transportSummary"
            Layout.fillWidth: true
            Layout.preferredHeight: (frameSummary.height + (panel.compactControls ? 2 : 6)) + transportButtons.implicitHeight + (stacked ? Math.max(leftPlayerTools.implicitHeight, rightPlayerTools.implicitHeight) + 3 : 0)
            // Side tools never participate in the central transport's anchoring.
            // On narrow panes they use the next line instead of displacing it.
            readonly property bool stacked: width < transportButtons.width + leftPlayerTools.width + rightPlayerTools.width + 8
            readonly property real sideWidth: Math.max(0, width / 2 - 16)
            Item {
                id: frameSummary
                objectName: "frameSummary"
                anchors.left: parent.left; anchors.leftMargin: 10
                y: 0
                width: transportSummary.sideWidth
                height: Math.max(18, currentFrameLabel.implicitHeight, totalFrameLabel.implicitHeight)
                clip: true
                readonly property real numberWidth: Math.max(0, width - framePrefix.width - 4)
                Row {
                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                    Label { id: framePrefix; textFormat: Text.PlainText; text: "F"; color: Theme.label; font.pixelSize: 11; anchors.baseline: currentFrameLabel.baseline }
                    Label {
                        id: currentFrameLabel; objectName: "currentFrameLabel"
                        textFormat: Text.PlainText; text: panel.frame.toLocaleString(Qt.locale(), "f", 0)
                        color: Theme.label; font.pixelSize: 14
                        width: Math.min(implicitWidth, frameSummary.numberWidth - Math.min(totalFrameLabel.implicitWidth, frameSummary.numberWidth * 0.4))
                        elide: Text.ElideRight
                    }
                    Label {
                        id: totalFrameLabel; objectName: "totalFrameLabel"
                        textFormat: Text.PlainText; text: "/ " + panel.total.toLocaleString(Qt.locale(), "f", 0)
                        color: Theme.label; font.pixelSize: 11
                        width: Math.min(implicitWidth, Math.max(0, frameSummary.numberWidth - currentFrameLabel.width))
                        anchors.baseline: currentFrameLabel.baseline; elide: Text.ElideRight
                    }
                }
            }
            Row {
                id: transportButtons; objectName: "transportButtons"
                readonly property real buttonWidth: Math.min(32 - 8 * panel.compression, Math.max(24, (parent.width - 8 * spacing) / 9))
                x: transportSummary.stacked ? (parent.width - width) / 2 : Math.max(leftPlayerTools.width + 4, Math.min((parent.width - width) / 2, parent.width - rightPlayerTools.width - width - 4))
                y: frameSummary.height + 2; spacing: Math.round(4 - 2 * panel.compression)
            TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportButtons.buttonWidth; objectName: "transportPreviousCue"; glyph: "previous-cue"; tip: "上一句句首"; enabled: panel.ready && !!panel.previousCue.cueId && panel.previousCue.firstFrame > 0; windowActive: panel.windowActive; onClicked: panel.cmd("row.select", {id: panel.previousCue.cueId}) }
            TransportButton { implicitHeight: panel.playerButtonHeight; objectName: "transportCueFirst"; implicitWidth: transportButtons.buttonWidth; glyph: "prev"; tip: "跳到本句入点" + (panel.selected.firstFrame > 0 ? "（F" + panel.selected.firstFrame + "）" : ""); enabled: panel.ready && panel.selected.firstFrame > 0; windowActive: panel.windowActive; onClicked: panel.cmd("media.cueFirst") }
            TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportButtons.buttonWidth; glyph: "back-five"; tip: "后退 5 帧（长按连续）"; delta: -5; enabled: panel.ready; windowActive: panel.windowActive; onStep: delta => panel.cmd("media.step", {delta: delta}) }
            TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportButtons.buttonWidth; glyph: "step-back"; tip: "后退 1 帧（长按连续）"; delta: -1; enabled: panel.ready; windowActive: panel.windowActive; onStep: delta => panel.cmd("media.step", {delta: delta}) }
            TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportButtons.buttonWidth; objectName: "transportPlayPause"; glyph: (panel.media.playing || panel.media.playPending) ? "pause" : "play"; tip: panel.media.playPending ? "正在定位帧，再次点击取消播放" : "播放 / 暂停（Space）"; enabled: panel.ready; windowActive: panel.windowActive; onClicked: panel.cmd("media.play") }
            TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportButtons.buttonWidth; objectName: "transportForwardOne"; glyph: "step-forward"; tip: "前进 1 帧（长按连续）"; delta: 1; enabled: panel.ready; windowActive: panel.windowActive; onStep: delta => panel.cmd("media.step", {delta: delta}) }
            TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportButtons.buttonWidth; glyph: "forward-five"; tip: "前进 5 帧（长按连续）"; delta: 5; enabled: panel.ready; windowActive: panel.windowActive; onStep: delta => panel.cmd("media.step", {delta: delta}) }
            TransportButton { implicitHeight: panel.playerButtonHeight; objectName: "transportCueLast"; implicitWidth: transportButtons.buttonWidth; glyph: "next"; tip: "跳到本句出点" + (panel.selected.lastFrame > 0 ? "（F" + panel.selected.lastFrame + "）" : ""); enabled: panel.ready && panel.selected.lastFrame > 0; windowActive: panel.windowActive; onClicked: panel.cmd("media.cueLast") }
            TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportButtons.buttonWidth; objectName: "transportNextCue"; glyph: "next-cue"; tip: "下一句句首"; enabled: panel.ready && !!panel.nextCue.cueId && panel.nextCue.firstFrame > 0; windowActive: panel.windowActive; onClicked: panel.cmd("row.select", {id: panel.nextCue.cueId}) }
            }
            Label {
                objectName: "playbackTimeLabel"
                anchors.right: parent.right; anchors.rightMargin: 10
                y: 0
                width: transportSummary.sideWidth
                textFormat: Text.PlainText; text: panel.formatTime(panel.media.positionSeconds || 0) + " / " + panel.formatTime(panel.media.durationSeconds || 0)
                color: Theme.label; font.pixelSize: 11; horizontalAlignment: Text.AlignRight; elide: Text.ElideLeft
            }
            Row {
                id: leftPlayerTools; objectName: "playerToolRow"
                anchors.left: parent.left
                y: transportSummary.stacked ? transportButtons.y + transportButtons.height + 3 : transportButtons.y
                spacing: 2
                TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportSummary.width < 350 ? 28 : 32; objectName: "playCurrentCueButton"; glyph: "play-cue"; tip: "播放本句（前后各 5 帧）"; visible: panel.review; enabled: panel.ready && !!panel.selected.cueId; onClicked: panel.cmd("media.playCue") }
                TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportSummary.width < 350 ? 28 : 32; objectName: "muteButton"; glyph: panel.media.muted ? "volume-muted" : "volume"; tip: panel.media.audioStatus === "无音轨" ? "无音轨" : panel.media.muted ? "取消静音" : "静音"; checkable: true; checked: panel.media.muted === true; enabled: panel.ready && panel.media.audioStatus !== "无音轨"; Accessible.description: panel.media.audioStatus || ""; onClicked: panel.cmd("media.mute", {value: checked}) }
                TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportSummary.width < 350 ? 28 : 32; objectName: "addMarkerButton"; glyph: "bookmark"; enabled: panel.ready; tip: "在当前帧添加标记点（M）· 拖至歌词字间可切分句子"; onClicked: panel.cmd("marker.add", {frame: panel.frame}) }
                ThemeDialog {
                    id: clearMarkersDialog; objectName: "clearMarkersDialog"
                    parent: Overlay.overlay; anchors.centerIn: parent; modal: true
                    title: "清空全部标记点？"; standardButtons: Dialog.Yes | Dialog.No
                    Label { text: "将清空所有时间轴标记点，不影响歌词内容（可撤销）。"; color: Theme.label }
                    onAccepted: panel.cmd("marker.clear")
                }
            }
            Row {
                id: rightPlayerTools; objectName: "playerRightTools"
                anchors.right: parent.right
                y: leftPlayerTools.y; spacing: 2
                readonly property bool foldTools: panel.compression > .6 && transportSummary.width < 600
                TransportButton {
                    visible: true; implicitWidth: 25; height: panel.playerButtonHeight
                    objectName: "morePlaybackTools"; glyph: "more"; tip: "更多播放工具"; onClicked: compactTools.open()
                    Menu {
                        id: compactTools
                        MenuItem { objectName: "clearMarkersButton"; text: "清空标记点…"; enabled: (panel.state.markers || []).length > 0; onTriggered: clearMarkersDialog.open() }
                        MenuItem { text: "字幕预览"; checkable: true; checked: panel.settings.subtitlePreview !== false; onTriggered: panel.cmd("settings.set", {key:"subtitlePreview", value:checked}) }
                        MenuItem { text: "字幕位置与排版"; enabled: !(panel.state.alignmentPreview || {}).active; onTriggered: panel.cmd("settings.subtitleStyle") }
                        MenuItem { visible: panel.showRoiTools; height: visible ? implicitHeight : 0; text: panel.roiSelecting ? "取消框选" : "设置选区"; enabled: panel.ready && !panel.nonVideo && !(panel.state.candidateTask || {}).running; onTriggered: { if (panel.roiSelecting) panel.roiSelecting = false; else panel.beginRoiSetup() } }
                        MenuItem { visible: panel.showRoiTools; height: visible ? implicitHeight : 0; text: "显示 OCR 选区"; enabled: !panel.nonVideo; checkable: true; checked: panel.cropVisible; onTriggered: panel.cropVisible = checked }
                    }
                }
                Check { objectName: "subtitlePreviewToggle"; text: "字幕预览"; visible: !rightPlayerTools.foldTools; height: panel.playerButtonHeight; checked: panel.settings.subtitlePreview !== false; font.pixelSize: 10; onToggled: panel.cmd("settings.set", {key: "subtitlePreview", value: checked}) }
                TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportSummary.width < 350 ? 28 : 32; objectName: "subtitleStyleButton"; visible: !rightPlayerTools.foldTools; glyph: "gear"; compact: true; tip: "字幕位置与排版"; Accessible.name: "字幕位置与排版"; enabled: !(panel.state.alignmentPreview || {}).active; onClicked: panel.cmd("settings.subtitleStyle") }
                TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportSummary.width < 350 ? 28 : 32; objectName: "selectRoiButton"; tip: panel.roiSelecting ? "取消框选" : (panel.state.roi ? "重新框选字幕区域" : "设置选区"); glyph: "roi-settings"; compact: true; visible: !rightPlayerTools.foldTools && panel.showRoiTools; enabled: panel.ready && !(panel.state.candidateTask || {}).running; checked: panel.roiSelecting; onClicked: { if (panel.roiSelecting) panel.roiSelecting = false; else panel.beginRoiSetup() } }
                TransportButton { implicitHeight: panel.playerButtonHeight; implicitWidth: transportSummary.width < 350 ? 28 : 32; objectName: "showRoiButton"; visible: !rightPlayerTools.foldTools && panel.showRoiTools; tip: "显示 OCR 选区"; glyph: "ocr-region"; compact: true; checkable: true; checked: panel.cropVisible; onClicked: panel.cropVisible = checked; motion: panel.settings.motion !== false }
            }
        }
        Item {
            id: seekRail
            objectName: "playerSeekRail"
            Layout.fillWidth: true; Layout.preferredHeight: panel.stableMarkers.length ? 40 : 18 - 4 * panel.compression
            property real shownFrame: seekMouse.dragging ? seekMouse.dragFrame : panel.media.playing && panel.controller && panel.controller.playbackCursor !== undefined ? panel.controller.playbackCursor : panel.media.playheadFrame !== undefined ? panel.media.playheadFrame : (panel.media.scrubbing ? panel.media.requestedFrame : panel.frame)
            property real ratio: panel.total > 1 ? (shownFrame - 1) / (panel.total - 1) : 0
            readonly property real axisLeft: 5
            readonly property real axisWidth: Math.max(1, width - 10)
            Rectangle { x: seekRail.axisLeft; anchors.bottom: parent.bottom; anchors.bottomMargin: 6.5; width: seekRail.axisWidth; height: 5; radius: 2.5; color: "#DFD7EE" }
            Rectangle { x: seekRail.axisLeft; anchors.bottom: parent.bottom; anchors.bottomMargin: 6.5; width: seekRail.axisWidth * seekRail.ratio; height: 5; radius: 2.5; color: "#8562C5" }
            Rectangle {
                objectName: "playerSelectedRange"
                visible: !!panel.selected.cueId && panel.total > 0 && panel.selected.firstFrame > 0 && panel.selected.lastFrame >= panel.selected.firstFrame
                x: seekRail.axisLeft + ((panel.selected.firstFrame || 1) - 1) / Math.max(1, panel.total - 1) * seekRail.axisWidth
                width: Math.max(2, Math.min(seekRail.axisWidth - (x - seekRail.axisLeft), ((panel.selected.lastFrame || 1) - (panel.selected.firstFrame || 1) + 1) / Math.max(1, panel.total - 1) * seekRail.axisWidth))
                height: 5; radius: 2.5; anchors.bottom: parent.bottom; anchors.bottomMargin: 6.5; color: "#458562C5"
            }
            MouseArea {
                id: seekMouse
                preventStealing: true
                objectName: "upperSeekMouse"
                anchors.fill: parent; enabled: panel.ready
                property bool dragging: false
                property int dragFrame: 1
                function frameAt(x) { return Math.max(1, Math.min(panel.total, 1 + Math.round((x - seekRail.axisLeft) / seekRail.axisWidth * (panel.total - 1)))) }
                function finish() { if (dragging) panel.cmd("media.scrubEnd", {frame: dragFrame}); dragging = false }
                onPressed: mouse => { dragging = true; dragFrame = frameAt(mouse.x); panel.cmd("media.scrubBegin", {frame: dragFrame}) }
                onPositionChanged: mouse => { if (dragging) { dragFrame = frameAt(mouse.x); panel.cmd("media.scrub", {frame: dragFrame}) } }
                onReleased: finish()
                onCanceled: finish()
            }
            Rectangle { objectName: "upperPlayheadThumb"; x: seekRail.axisLeft + seekRail.ratio * seekRail.axisWidth - width / 2; anchors.bottom: parent.bottom; anchors.bottomMargin: 3; width: 12; height: 12; radius: 6; color: "#FFFFFF"; border.color: "#6E4AB0"; border.width: 3 }
            Repeater {
                model: panel.stableMarkers
                Marker {
                    required property var modelData
                    frame: typeof modelData === "number" ? modelData : modelData.frame
                    markerId: typeof modelData === "number" ? "" : String(modelData.id || "")
                    x: seekRail.axisLeft + (frame - 1) / Math.max(1, panel.total - 1) * seekRail.axisWidth - width / 2
                    y: 0
                    onSeek: frame => panel.cmd("media.seek", {frame: frame})
                    onRemove: (id, frame) => panel.cmd("marker.remove", {id: id, frame: frame})
                }
            }
        }

            }
        }
        Item {
            visible: !panel.review && panel.ready && !panel.nonVideo
            Layout.fillHeight: true; Layout.preferredHeight: 0
        }
    }
}
