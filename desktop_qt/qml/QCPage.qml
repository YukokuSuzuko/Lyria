import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    required property var controller
    required property var state
    readonly property var qc: state.qc || ({})
    readonly property var capabilities: state.capabilities || ({})
    readonly property var issues: qc.issues || []
    property double now: Date.now()
    readonly property var task: state.task || ({})
    readonly property bool hasElapsed: !!(task.running && task.startedAt) || typeof ((qc.performance || {}).total_seconds) === "number"
    readonly property double elapsed: task.running && task.startedAt ? Math.max(0,(now-task.startedAt)/1000) : Number((qc.performance || {}).total_seconds || 0)
    function semanticRange() {
        var ids=qc.semanticUnchecked || []; var rows=qc.cues || []; var numbers=[]
        for (var i=0;i<ids.length;i++) {
            var found=false
            for (var j=0;j<rows.length;j++) if (rows[j].cueId===ids[i]) { numbers.push(rows[j].number || (j+1)); found=true; break }
            if (!found) numbers.push(String(ids[i]).indexOf("sentence-")===0 ? "未关联句" : ids[i])
        }
        return numbers.join("、")
    }
    function duration(value) { var n=Math.floor(value); return Math.floor(n/60) + " 分 " + (n%60) + " 秒" }
    Timer { interval: 1000; running: page.task.running === true; repeat: true; onTriggered: page.now=Date.now() }
    function cmd(name, payload) { controller.command(name, payload || {}) }
    function ocrPayload(api) {
        var result={api: api, mode: mode.currentIndex === 0 ? "final" : "temporary", strategy: advanced.checked ? "frame" : "sentence"}
        if (recommended.checked) { result.chinese_engine="rapidocr"; result.source_engine="tesseract" }
        return result
    }
    function start() { if (!apiCheck.checked) noApiDialog.open(); else cmd("qc.start", ocrPayload(true)) }
    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        contentHeight: qcContent.height
        clip: true
    Item {
        id: qcContent
        width: scroll.availableWidth
        height: Math.max(page.height, 760)
    RowLayout {
        anchors.fill: parent
        spacing: 14
        Panel {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1.55
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 18; spacing: 14
                RowLayout {
                    Layout.fillWidth: true
                    Label { textFormat: Text.PlainText; text: page.qc.sentenceMode === false ? "成片质检 · 逐帧边界精查" : "成片质检 · 快速逐句抽检"; font.pixelSize: 15; font.weight: Font.DemiBold; color: Theme.ink; Layout.fillWidth: true }
                    Badge { text: page.qc.reportKind || "未开始" }
                }
                Label { textFormat: Text.PlainText; text: page.qc.sentenceMode === false ? "逐帧扫描视频硬字幕，精确核对每句字幕的出入点帧与文字内容。" : "按工程时间轴逐句抽取成片画面，本地 OCR 快速核对字幕文字（不检查逐帧出入点）。"; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 12; color: Theme.label; lineHeight: 1.5 }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Repeater {
                        model: [{label: page.qc.sentenceMode === false ? "检出片段" : "工程句数", value: page.qc.sentenceMode === false ? (page.qc.cues || []).length : ((page.qc.summary || {}).expected_total || 0)}, {label: "已检查", value: page.qc.sentenceMode === false ? "—" : ((page.qc.summary || {}).sentences_checked || 0)}, {label: "未检查", value: page.qc.sentenceMode === false ? "详见报告" : ((page.qc.summary || {}).sentences_unchecked || 0)}]
                        Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; Layout.preferredHeight: 66
                            radius: 10; color: Theme.slab; border.color: Theme.lineSoft
                            Column { anchors.centerIn: parent; spacing: 4
                                Label { textFormat: Text.PlainText; text: modelData.value; font.pixelSize: 23; font.weight: Font.DemiBold; color: Theme.lilacDark; anchors.horizontalCenter: parent.horizontalCenter }
                                Label { textFormat: Text.PlainText; text: modelData.label; font.pixelSize: 11; color: Theme.muted; anchors.horizontalCenter: parent.horizontalCenter }
                            }
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: progressContent.implicitHeight+24
                    color: page.task.running ? Theme.lilacSoft : Theme.slab; radius: 9; border.color: Theme.line
                    ColumnLayout {
                        id: progressContent; anchors.fill: parent; anchors.margins: 12; spacing: 7
                        RowLayout {
                            Layout.fillWidth: true
                            Label { textFormat: Text.PlainText; text: page.task.running ? (page.task.label || "正在质检…") : (page.qc.completed ? (page.qc.sentenceMode === false ? "逐帧精查已完成" : "逐句抽检已完成") : "等待开始质检"); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.ink; font.pixelSize: 12; font.weight: Font.DemiBold }
                            Label { text: page.hasElapsed ? "耗时 " + page.duration(page.elapsed) : "耗时 —"; color: Theme.label; font.pixelSize: 11 }
                            ActionButton { objectName: "qcCancelTask"; visible: page.task.running === true; text: page.task.cancelling ? "正在取消…" : (advanced.checked ? "取消检查" : "停止并保留结果"); enabled: !page.task.cancelling; compact: true; onClicked: page.cmd("task.cancel") }
                        }
                        Label { textFormat: Text.PlainText; visible: (page.qc.referenceMedia || {}).same_file_path === false; text: "当前成片与工程原视频并非同一文件，请留意剪辑时间轴是否一致。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.amber; font.pixelSize: 11 }
                        ProgressBar { Layout.fillWidth: true; from: 0; to: 1; value: Number(page.task.progress || 0); visible: page.task.running === true }
                        Label { textFormat: Text.PlainText; visible: ((page.qc.scopeDetail || {}).unchecked_sentence_numbers || []).length>0; text: "未检查句号：" + (((page.qc.scopeDetail || {}).unchecked_sentence_numbers || []).join("、")); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.amber; font.pixelSize: 11 }
                        Label { textFormat: Text.PlainText; visible: !!page.qc.apiReason || (page.qc.semanticUnchecked || []).length>0; text: (page.qc.apiReason || "") + ((page.qc.semanticUnchecked || []).length>0 ? " · 未做 AI 核对：" + page.semanticRange() : ""); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.amber; font.pixelSize: 11 }
                    }
                }
                QCResults { Layout.fillWidth: true; Layout.fillHeight: true; controller: page.controller; state: page.state }
                Label { textFormat: Text.PlainText; text: page.qc.status || "未启用 AI 语义核对"; color: Theme.muted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
            }
        }
        Panel {
            Layout.fillHeight: true; Layout.preferredWidth: Math.max(270, Math.min(360, page.width * 0.36))
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 18; spacing: 13
                Label { textFormat: Text.PlainText; text: "质检视频与模式"; font.pixelSize: 14; font.weight: Font.DemiBold; color: Theme.ink }
                Label { textFormat: Text.PlainText; text: page.qc.inputPath || "尚未选择成片视频"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 11 }
                ActionButton { text: "选择成片视频"; glyph: "upload"; Layout.fillWidth: true; enabled: page.capabilities["qc.open"] === true; onClicked: page.cmd("qc.open") }
                Label { textFormat: Text.PlainText; text: "视频类型"; color: Theme.label; font.pixelSize: 11 }
                ChoiceBox { id: mode; Layout.fillWidth: true; model: ["已压制硬字幕 · 最终成片", "裸视频 + 外挂字幕 · 临时预检"]; font.pixelSize: 11 }
                Label { textFormat: Text.PlainText; text: mode.currentIndex === 0 ? "直接识别成片画面中的硬字幕。" : "在本地临时叠加当前工程字幕进行预检，不会修改原视频。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 11 }
                Rectangle { height: 1; Layout.fillWidth: true; color: Theme.lineSoft }
                Label { textFormat: Text.PlainText; text: "字幕识别选区"; color: Theme.ink; font.pixelSize: 12; font.weight: Font.Medium }
                Label { textFormat: Text.PlainText; text: "分别框选中文与原文硬字幕位置（两框不可重叠）；选区时可拖动进度条找有字幕的画面。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 11 }
                ActionButton { objectName: "qcRoiButton"; text: "框选中文 / 原文区域"; glyph: "crop"; Layout.fillWidth: true; enabled: page.capabilities["qc.roi"] === true; onClicked: page.cmd("qc.roi", {mode: mode.currentIndex === 0 ? "final" : "temporary", strategy: advanced.checked ? "frame" : "sentence"}) }
                Check { id: recommended; objectName: "qcRecommendedOcr"; checked: false; enabled: !page.task.running; Layout.fillWidth: true; text: "使用推荐 OCR 组合" }
                Label { textFormat: Text.PlainText; text: recommended.checked ? "中文使用 RapidOCR（支持显卡加速），外文使用 Tesseract（适合英文，日文识别率有限）。" : "勾选后本次质检中文用 RapidOCR、外文用 Tesseract；不勾选则沿用当前 OCR 设置。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 10 }
                Check {
                    id: advanced; objectName: "qcAdvancedStrategy"; Layout.fillWidth: true; checked: false; enabled: !page.task.running
                    text: "逐帧边界精查（耗时较长）"
                    implicitHeight: Math.max(28,contentItem.implicitHeight+6)
                    contentItem: Text { textFormat: Text.PlainText; text: advanced.text; font: advanced.font; color: Theme.label; wrapMode: Text.Wrap; leftPadding: 23; verticalAlignment: Text.AlignVCenter }
                }
                Label { textFormat: Text.PlainText; visible: advanced.checked; text: "逐帧扫描全片耗时较长，建议开启显卡加速；中途取消将保留已完成的轨道结果。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.amber; font.pixelSize: 10 }
                Label { textFormat: Text.PlainText; visible: !!page.qc.ocrSettings; text: "本次 OCR：中文 " + ((page.qc.ocrSettings || {}).chinese_engine || (page.qc.ocrSettings || {}).engine || "未记录") + " / 原文 " + ((page.qc.ocrSettings || {}).source_engine || (page.qc.ocrSettings || {}).engine || "未记录") + " · 加速设备 " + ((page.qc.ocrSettings || {}).ocr_device || "未记录"); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 10 }
                Check { id: warningDetails; text: "显示质检提示与警告"; visible: (page.qc.warnings || []).length>0; Layout.fillWidth: true }
                Label { textFormat: Text.PlainText; visible: warningDetails.checked; text: (page.qc.warnings || []).join("\n"); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.amber; font.pixelSize: 10 }
                Check { id: apiCheck; text: "同时启用 AI 双语语义核对"; font.pixelSize: 11; enabled: page.capabilities["qc.api"] === true }
                Label { textFormat: Text.PlainText; text: "仅向 API 发送字幕文本与时间轴，视频和画面截图均保留在本地。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 10; lineHeight: 1.5 }
                ActionButton { text: "打开完整质检报告"; compact: true; Layout.fillWidth: true; enabled: page.capabilities["qc.report"] === true && !!page.qc.reportDirectory; onClicked: page.cmd("qc.report") }
                Item { Layout.fillHeight: true }
                Label { textFormat: Text.PlainText; visible: page.capabilities["qc.start"] !== true; text: "请先选择成片视频并框选字幕区域。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.amber; font.pixelSize: 11 }
                ActionButton { objectName: "qcStartButton"; text: advanced.checked ? "开始逐帧边界精查" : "开始快速逐句抽检"; glyph: "qc"; primary: true; Layout.fillWidth: true; enabled: page.capabilities["qc.start"] === true && !!page.qc.inputPath && !page.task.running; onClicked: page.start() }
            }
        }
    }
    }
    }
    ThemeDialog {
        id: noApiDialog; objectName: "qcNoApiDialog"; anchors.centerIn: parent; width: Math.min(450, page.width - 30); modal: true; title: "仅运行本地 OCR 质检？"; height: 220
        contentItem: Label { textFormat: Text.PlainText; wrapMode: Text.Wrap; text: "未勾选 AI 双语语义核对，本次将仅在本地核对画面字幕文字。是否继续？"; color: Theme.label }
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: page.cmd("qc.start", page.ocrPayload(false))
    }
}
