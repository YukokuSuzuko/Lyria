import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Panel {
    id: panel
    objectName: "importRecognitionPanel"
    required property var controller
    required property var state
    required property var media
    property bool ocrMode: false // Older hosts can still select this merged page.
    readonly property var task: state.task || ({})
    readonly property var candidateTask: state.candidateTask || ({})
    readonly property var capabilities: state.capabilities || ({})
    signal reviewRequested()
    signal roiSetupRequested()
    signal roiManualRequested()
    property bool regionsVisible: false
    signal visibilityRequested(bool value)
    signal showRegions()
    readonly property var modeNames: ({ocr:"画面字幕 OCR",audio:"原词自动打轴",edit:"手动打轴编辑",separate:"仅人声分离"})
    function cmd(name, payload) { return controller.command(name, payload || {}) }
    readonly property bool hasOcrRegion: state.ocrMultiRegion
        ? (state.regionCards || []).some(r => r.enabled) : !!state.roi
    property bool showScanCounts: false
    onCandidateTaskChanged: if (candidateTask.running) showScanCounts = false
    function startOcr() { if (hasOcrRegion) cmd("ocr.start", {autoAi:false}) }
    ScrollView {
        id: importScroll
        anchors.fill: parent; anchors.margins: 14
        clip: true; contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: importScroll.availableWidth; spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Label { text: "导入 / 识别"; font.pixelSize: 15; font.weight: Font.DemiBold; color: Theme.ink }
                Badge { objectName: "currentEntryMode"; text: panel.modeNames[panel.state.entryMode || "ocr"] }
                Item { Layout.fillWidth: true }
            ActionButton {
                objectName: "changeEntryModeButton"; text: "更换当前工程方式"; compact: true
                enabled: !panel.task.running && !panel.candidateTask.running
                onClicked: entryModeMenu.open()
                Menu {
                    id: entryModeMenu
                    MenuItem { objectName: "ocrEntryButton"; text: panel.modeNames.ocr; checkable: true; checked: (panel.state.entryMode || "ocr") === "ocr"; onTriggered: panel.cmd("editor.start", {mode: "ocr"}) }
                    MenuItem { objectName: "audioAutoTimingEntry"; text: panel.modeNames.audio; checkable: true; checked: (panel.state.entryMode || "ocr") === "audio"; onTriggered: panel.cmd("editor.start", {mode: "audio"}) }
                    MenuItem { objectName: "separationEntryButton"; text: panel.modeNames.separate; checkable: true; checked: (panel.state.entryMode || "ocr") === "separate"; onTriggered: panel.cmd("editor.start", {mode: "separate"}) }
                    MenuItem { objectName: "pureEditingButton"; text: panel.modeNames.edit; checkable: true; checked: (panel.state.entryMode || "ocr") === "edit"; onTriggered: panel.cmd("editor.start", {mode: "edit"}) }
                }
            }
            }
            Label { text: panel.state.entryMode === "separate" ? "选择音视频与 HQ3/HQ1，仅分离人声供试听，不生成歌词时间轴。" : "导入音视频与歌词素材，选择工作方式即可开始打轴或核对。已有时间轴不会被识别结果自动覆盖。"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 12; color: Theme.label }
            Repeater {
                model: {
                    const mode = panel.state.entryMode || "ocr"
                    const media = {title:mode === "ocr" ? "字幕视频" : mode === "edit" ? "音视频素材（可选）" : "音视频素材", detail:"支持常见视频和音频格式", button:mode === "ocr" ? "选择视频" : "选择音视频", action:"media.open", role:""}
                    const original = {title:mode === "audio" ? "演唱原文" : "参考原文（可选）", detail:"支持 TXT、SRT、LRC、ASS 和 SSA。", button:mode === "audio" ? "导入演唱原文" : "导入参考原文", action:"input.import", role:"source"}
                    const translated = {title:mode === "edit" ? "歌词 / 已有字幕（可选）" : "中文字幕 / 译文（可选）", detail:mode === "audio" ? "用于打轴后的对照和配对。" : "支持带时间的字幕或纯文本译文。", button:"导入字幕 / 译文", action:"input.import", role:"translation"}
                    return mode === "separate" ? [media] : mode === "audio" ? [media, original, translated] : [media, translated, original]
                }
                Rectangle {
                    id: importCard
                    required property var modelData
                    readonly property string loadedPath: modelData.action === "media.open"
                        ? ((panel.state.inputs || {}).videoPath || (panel.state.inputs || {}).audio || "")
                        : ((panel.state.inputs || {})[modelData.role === "translation" ? "translationPath" : "sourcePath"] || "")
                    objectName: "importCard_" + (modelData.role || "media")
                    Layout.fillWidth: true; implicitHeight: visible ? cardContents.implicitHeight + 18 : 0
                    radius: 8; color: Theme.slab; border.color: Theme.lineSoft
                    ColumnLayout {
                        id: cardContents
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 9; spacing: 4
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: modelData.title; Layout.fillWidth: true; color: Theme.ink; font.pixelSize: 12; font.weight: Font.Medium }
                            ActionButton { objectName: "chooseMaterial_" + (modelData.role || "media"); text: modelData.action === "media.open" && panel.media.ready ? "重新选择" : modelData.button; glyph: "upload"; compact: true; onClicked: panel.cmd(modelData.action, modelData.role ? {role:modelData.role,poolOnly:panel.state.entryMode === "audio" && modelData.role === "translation"} : {}) }
                            ActionButton { objectName: "clearImportMaterialButton"; visible: !!importCard.loadedPath; text: "清空"; compact: true; enabled: !panel.task.running && !panel.candidateTask.running; onClicked: panel.cmd("input.clear", {role: modelData.action === "media.open" ? "media" : modelData.role}); tip: "仅解除这项素材的引用，保留已有编辑，不删除磁盘文件。" }
                        }
                        Label { text: modelData.detail; color: Theme.muted; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Label { visible: !!importCard.loadedPath; text: importCard.loadedPath; color: Theme.label; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideMiddle; ToolTip.visible: pathHover.hovered; ToolTip.text: text; HoverHandler { id: pathHover } }
                    }
                }
            }
            OcrRegionCards { Layout.fillWidth: true; visible: panel.media.mediaKind === "video" && !!panel.media.ready && (panel.state.entryMode || "ocr") === "ocr"; enabled: !panel.task.running && !panel.candidateTask.running; controller: panel.controller; state: panel.state; regionsVisible: panel.regionsVisible; onVisibilityRequested: value => panel.visibilityRequested(value); onShowRegions: panel.showRegions(); onDrawRequested: panel.roiManualRequested() }
            Label { objectName: "missingRegionHint"; visible: (panel.state.entryMode || "ocr") === "ocr" && !panel.hasOcrRegion; text: "先自动检测或手动框选歌词区域。"; color: Theme.label; font.pixelSize: 11 }
            Flow {
                Layout.fillWidth: true; spacing: 6
                ActionButton { objectName: "startOcrButton"; visible: (panel.state.entryMode || "ocr") === "ocr"; text: "开始 OCR 识别"; primary: true; compact: true; enabled: panel.hasOcrRegion && panel.media.mediaKind === "video" && !!panel.media.ready && !panel.candidateTask.running && !panel.task.running && panel.capabilities["ocr.start"] === true; onClicked: panel.startOcr() }
                ActionButton { objectName: "startLocalVocalAlignment"; visible: panel.state.entryMode === "audio"; text: "开始自动打轴"; primary: true; compact: true; enabled: panel.capabilities["vocal.start"] === true; onClicked: panel.cmd("vocal.start"); tip: "配置本地模型与歌词范围后开始自动打轴" }
                ActionButton { objectName: "startSeparationOnly"; visible: panel.state.entryMode === "separate"; text: "仅人声分离"; primary: true; compact: true; enabled: !panel.task.running && !panel.candidateTask.running; onClicked: panel.cmd("separation.open"); tip: "仅运行所选人声分离模型，不需要歌词，不启动自动打轴。" }
            }
            ActionButton { objectName: "enterLyricEditor"; text: "进入歌词编辑"; visible: panel.state.entryMode === "edit"; primary: true; enabled: !panel.task.running; onClicked: panel.cmd("editor.continue") }
            CheckBox {
                id: ocrAutoAi; objectName: "ocrAutoAiCheckBox"
                visible: false
                text: "识别完成后自动进行 AI 配对"
                checked: false; enabled: !panel.task.running && !panel.candidateTask.running
            }
            Label { objectName: "separationScopeNotice"; visible: panel.state.entryMode === "separate"; text: "不需要歌词；默认处理整首音频，程序自动分段并合并，不需要手动切片。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 11 }
            Label { objectName: "ocrManualAiNotice"; visible: (panel.state.entryMode || "ocr") === "ocr"; text: "仅运行本地 OCR，不自动发送 AI 请求。识别完成后可从字幕菜单重新打开本轮结果。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 11 }
            Label { objectName: "missingOcrEngine"; visible: (panel.state.entryMode || "ocr") === "ocr" && panel.capabilities["ocr.start"] === false; text: "未检测到可用的本地 OCR 引擎，当前仅支持导入字幕或手动编辑。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.amber; font.pixelSize: 11 }
            ColumnLayout {
                objectName: "ocrCompletion"
                visible: (panel.state.entryMode || "ocr") === "ocr" && panel.candidateTask.source === "ocr"
                         && !panel.candidateTask.running && !!panel.candidateTask.resultStatus
                Layout.fillWidth: true
                Label { text: panel.candidateTask.label || ""; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.ink; textFormat: Text.PlainText }
                Flow {
                    Layout.fillWidth: true; spacing: 5
                    ActionButton { objectName: "reopenCurrentOcr"; visible: !!panel.candidateTask.resultId; text: "核对本轮结果"; compact: true; onClicked: panel.controller.reopenOcrResult() }
                    ActionButton { objectName: "ocrLayerDetails"; visible: !!panel.candidateTask.scanCounts; text: "识别详情"; compact: true; onClicked: panel.showScanCounts = !panel.showScanCounts }
                    ActionButton { visible: !panel.candidateTask.resultId; text: "调整选区"; compact: true; enabled: panel.media.mediaKind === "video" && panel.media.ready; onClicked: panel.roiManualRequested() }
                }
                Label {
                    objectName: "ocrLayerCounts"; visible: panel.showScanCounts; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 11
                    readonly property var counts: panel.candidateTask.scanCounts || ({})
                    text: qsTr("选区 %1 个（框外未扫描）").arg(counts.regions || 0)
                          + (counts.boxes !== undefined ? qsTr(" · %1 个采样帧内识别到 %2 个文字框").arg(counts.samples).arg(counts.boxes) : " · 本轮未记录逐帧框数")
                          + qsTr("\n画面记录 %1 条，可参与对时 %2 条；同行残片 %3 条，短低置信 %4 条").arg(counts.tracks || 0).arg(counts.eligible || 0).arg(counts.fragments || 0).arg(counts.short_uncertain || 0)
                          + qsTr("\n有时间的候选 %1 句，本轮写入主轴 %2 句").arg(counts.timed || 0).arg(counts.applied || 0)
                }
            }
            ColumnLayout {
                visible: !!panel.candidateTask.running; Layout.fillWidth: true
                ShimmerText { text: panel.candidateTask.label || "OCR 识别中…"; running: !!panel.candidateTask.running; Layout.fillWidth: true; fontPixelSize: 11 }
                ThemeProgressBar { Layout.fillWidth: true; value: panel.candidateTask.progress || 0; indeterminate: panel.candidateTask.progress === undefined }
                ActionButton { objectName: "importCancelCandidateTaskButton"; text: (panel.candidateTask.cancelling ? "正在取消" : "取消") + (panel.candidateTask.source === "vocal" ? "打轴" : " OCR") + (panel.candidateTask.cancelling ? "…" : ""); compact: true; enabled: !panel.candidateTask.cancelling; onClicked: panel.controller.cancelCandidateJob() }
            }
            ColumnLayout {
                visible: !!panel.task.running; Layout.fillWidth: true
                ShimmerText { text: panel.task.label || "正在处理…"; running: !!panel.task.running; Layout.fillWidth: true; fontPixelSize: 11 }
                ThemeProgressBar { Layout.fillWidth: true; value: panel.task.progress || 0; indeterminate: !panel.task.progress }
                ActionButton { text: panel.task.api ? "停止后续请求" : "取消任务"; enabled: !panel.task.cancelling; compact: true; onClicked: panel.cmd("task.cancel") }
            }
        }
    }
}
