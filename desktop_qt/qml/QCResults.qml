import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: results
    required property var controller
    required property var state
    readonly property var qc: state.qc || ({})
    readonly property var caps: state.capabilities || ({})
    readonly property var cues: qc.cues || []
    readonly property var unmatched: qc.unmatched || []
    readonly property var displayedCues: tab === "unmatched" ? unmatched : cues
    readonly property var issues: qc.issues || []
    readonly property var rescans: qc.rescans || []
    property string tab: "cues"
    function cmd(name, payload) { controller.command(name, payload || {}) }
    function reviewLabel(status) {
        return status === "reviewed" ? "已人工复核" : status === "skipped" ? "已跳过" : "待人工复核"
    }
    function contentLabel(status, bilingual) {
        return status === "sample_text_matches" ? (bilingual ? "双语抽样一致" : "中文抽样一致") : status === "text_difference" ? "发现文字差异" : status === "not_checked" ? "未检查" : "画面无法确认"
    }
    function boundaryLabel(status) {
        return status === "adjacent_decoded_frames" ? "已核对出入点相邻帧" : status === "reference_only" ? "未识别到画面字幕" : "出入点待核对"
    }
    function sampleText(samples, frame) {
        var list=samples || []
        for (var i=0;i<list.length;i++) if (list[i].frame===frame) {
            var value=list[i]
            return (value.text || "此帧未识别到文字") + (value.confidence !== undefined ? "（置信度 " + Math.round(Number(value.confidence)*100) + "%）" : "")
        }
        return qc.sentenceMode === false ? "暂无此帧文字记录，请查看完整报告" : "此帧暂无该路 OCR 识别结果"
    }
    function rescanLabel(status) {
        return status === "running" ? "正在重新识别…" : status === "failed" ? "重查失败（原记录已保留）" : status === "cancelled" ? "已取消重查" : "重查完成 · 待核对"
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 9
        Flow {
            Layout.fillWidth: true; spacing: 5
            ActionButton { text: (results.qc.sentenceMode === false ? "检出片段 " : "逐句结果 ") + results.cues.length; compact: true; checked: results.tab === "cues"; onClicked: results.tab = "cues" }
            ActionButton { objectName: "qcUnmatchedTab"; text: "未匹配画面 " + results.unmatched.length; compact: true; checked: results.tab === "unmatched"; onClicked: results.tab = "unmatched" }
            ActionButton { text: "疑似问题 " + results.issues.length; compact: true; checked: results.tab === "issues"; onClicked: results.tab = "issues" }
            ActionButton { text: "局部重查 " + results.rescans.length; compact: true; checked: results.tab === "rescans"; onClicked: results.tab = "rescans" }
        }
        Label { textFormat: Text.PlainText; text: "提示：标记为“已人工复核”仅用于记录检查进度，不会修改工程字幕或时间轴。"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 10 }
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                id: cueList
                objectName: "qcCueList"
                anchors.fill: parent; visible: results.tab === "cues" || results.tab === "unmatched"
                model: results.displayedCues; spacing: 9; clip: true
                ScrollBar.vertical: ScrollBar { }
                delegate: Rectangle {
                    id: card
                    required property int index
                    required property var modelData
                    readonly property var row: modelData
                    property int selectedEvidence: 0
                    readonly property var evidence: row.evidenceFrames || []
                    readonly property var chosen: evidence.length ? evidence[Math.min(selectedEvidence,evidence.length-1)] : ({})
                    objectName: "qcCue_" + row.cueId
                    width: cueList.width; height: cueContent.implicitHeight + 24
                    radius: 9; color: Theme.slab; border.color: Theme.lineSoft
                    ColumnLayout {
                        id: cueContent
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 9
                        RowLayout {
                            Layout.fillWidth: true
                            Badge { text: results.tab === "unmatched" ? "未匹配 · 可能漏句 / 误检" : results.qc.sentenceMode === false ? (card.row.track === "source" ? "原文识别片段" : "中文识别片段") : "第 " + (card.row.number || (index+1)) + " 句" }
                            Label { textFormat: Text.PlainText; text: results.reviewLabel(card.row.reviewStatus); color: Theme.label; font.pixelSize: 10; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight }
                        }
                        Label { objectName: "qcContentStatus_" + card.row.cueId; textFormat: Text.PlainText; text: results.contentLabel(card.row.contentStatus, card.row.chineseContentStatus !== undefined); color: card.row.contentStatus === "sample_text_matches" ? Theme.green : Theme.amber; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Label { visible: results.qc.sentenceMode === false; textFormat: Text.PlainText; text: "F" + (card.row.firstFrame || "?") + "–F" + (card.row.lastFrame || "?") + " · " + results.boundaryLabel(card.row.boundaryStatus); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 10 }
                        GridLayout {
                            Layout.fillWidth: true; columns: width >= 540 ? 2 : 1; columnSpacing: 14; rowSpacing: 10
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: 360; spacing: 7
                                Repeater {
                                    model: [{label:"工程原文",text:card.row.source_text || "未提供原文"},
                                            {label:"工程译文",text:card.row.expected_text || "未提供译文"},
                                            {label:results.qc.sentenceMode === false ? "画面识别原文" : "画面识别原文",text:card.row.source_ocr || (results.qc.sentenceMode === false && card.row.track === "source" ? card.row.text : "未识别到有效文字")},
                                            {label:results.qc.sentenceMode === false ? "画面识别中文" : "画面识别中文",text:results.qc.sentenceMode === false && card.row.track === "source" ? "当前为原文轨，不含中文识别结果" : (card.row.text || "未识别到有效文字")}]
                                    ColumnLayout {
                                        required property var modelData
                                        Layout.fillWidth: true; spacing: 2
                                        Label { textFormat: Text.PlainText; text: modelData.label; color: Theme.muted; font.pixelSize: 10 }
                                        Label { textFormat: Text.PlainText; text: modelData.text; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.text; font.pixelSize: 12 }
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: 320; spacing: 5
                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: Math.min(180,Math.max(120,width*9/16)); color: "#211C2C"; radius: 5
                                    Image { id: frameImage; objectName: "qcFrame_" + card.row.cueId; anchors.fill: parent; fillMode: Image.PreserveAspectFit; source: card.chosen.url || card.row.screenshotUrl || ""; asynchronous: true; cache: true; readonly property bool evidenceReady: status === Image.Ready }
                                    Label { anchors.centerIn: parent; width: parent.width-20; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; text: frameImage.status === Image.Loading ? "正在加载质检截图…" : "暂无质检截图"; color: "#FFFFFF"; visible: frameImage.status !== Image.Ready; font.pixelSize: 11 }
                                }
                                Label { textFormat: Text.PlainText; text: (results.qc.sentenceMode === false ? "质检画面 F" : "抽样画面 F") + (card.chosen.frame || card.row.representativeFrame || "?") + (card.chosen.seconds !== undefined ? " · " + Number(card.chosen.seconds).toFixed(3) + " 秒" : "") + (results.qc.sentenceMode === false ? "（逐帧精查）" : "（句中抽样）"); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 10 }
                                Label { objectName: "qcSelectedSourceOcr_" + card.row.cueId; textFormat: Text.PlainText; text: "此帧原文 OCR：" + results.sampleText(card.row.source_samples,card.chosen.frame || card.row.representativeFrame); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 10 }
                                Label { objectName: "qcSelectedChineseOcr_" + card.row.cueId; textFormat: Text.PlainText; text: "此帧中文 OCR：" + results.sampleText(card.row.samples,card.chosen.frame || card.row.representativeFrame); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 10 }
                                Flow {
                                    Layout.fillWidth: true; spacing: 4
                                    Repeater {
                                        model: card.evidence
                                        ActionButton { required property var modelData; required property int index; objectName: "qcFrameChoice_" + card.row.cueId + "_" + index; text: "F" + modelData.frame; compact: true; checked: card.selectedEvidence === index; onClicked: card.selectedEvidence=index; tip: "切换查看不同抽样帧画面" }
                                    }
                                }
                            }
                        }
                        Label { textFormat: Text.PlainText; text: "中文差异：" + ((card.row.differences || []).join("；") || (card.row.contentStatus === "sample_text_matches" ? "无明显差异" : "画面无法确认或未检查")) + "\n原文差异：" + ((card.row.source_differences || []).join("；") || "无对比结果"); color: Theme.amber; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 11 }
                        Label { textFormat: Text.PlainText; visible: !!card.row.recheckStatus; text: "局部重查：" + (card.row.recheckStatus || ""); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.label; font.pixelSize: 10 }
                        Label { textFormat: Text.PlainText; visible: (card.row.warnings || []).length>0; text: (card.row.warnings || []).join("；"); Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 10 }
                        Flow {
                            Layout.fillWidth: true; spacing: 5
                            ActionButton { objectName: "qcInspect_" + card.row.cueId; text: "定位到该帧"; compact: true; enabled: results.caps["qc.inspect"] === true; onClicked: results.cmd("qc.inspect", {cueId: card.row.cueId}) }
                            ActionButton { objectName: "qcRescan_" + card.row.cueId; text: "本句局部重查"; compact: true; enabled: results.caps["qc.rescan"] === true && !(results.state.task || {}).running; tip: "调整本句时间范围、选区或识别语言后重新本地识别"; onClicked: results.cmd("qc.rescan", {cueId: card.row.cueId}) }
                            ActionButton { text: "标为已复核"; compact: true; enabled: results.caps["qc.review"] === true && card.row.reviewStatus !== "reviewed"; onClicked: results.cmd("qc.review", {cueId: card.row.cueId, status: "reviewed", note: ""}) }
                            ActionButton { objectName: "qcSkip_" + card.row.cueId; text: "跳过"; compact: true; enabled: results.caps["qc.review"] === true && card.row.reviewStatus !== "skipped"; onClicked: results.cmd("qc.review", {cueId: card.row.cueId, status: "skipped", note: ""}) }
                            ActionButton { text: "重置为待复核"; compact: true; visible: card.row.reviewStatus === "reviewed" || card.row.reviewStatus === "skipped"; enabled: results.caps["qc.review"] === true; onClicked: results.cmd("qc.review", {cueId: card.row.cueId, status: "needs_review", note: ""}) }
                        }
                    }
                }
                Label { textFormat: Text.PlainText; visible: results.displayedCues.length === 0; anchors.centerIn: parent; width: parent.width - 20; text: results.qc.completed ? "当前分类下暂无项目" : "暂无质检结果\n请在右侧选择成片视频后开始检查"; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; lineHeight: 1.6; color: Theme.muted; font.pixelSize: 12 }
            }
            ListView {
                id: issueList
                anchors.fill: parent; visible: results.tab === "issues"
                model: results.issues; spacing: 8; clip: true
                ScrollBar.vertical: ScrollBar { }
                delegate: Rectangle {
                    required property var modelData
                    width: issueList.width; height: Math.max(80, issueText.implicitHeight + 24)
                    radius: 9; color: Theme.slab; border.color: Theme.lineSoft
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 12; spacing: 10
                        Label { textFormat: Text.PlainText; text: modelData.frame > 0 ? "F " + modelData.frame : "出入点待核对"; Layout.preferredWidth: 72; horizontalAlignment: Text.AlignHCenter; color: Theme.lilacDark; font.pixelSize: 10 }
                        Label { id: issueText; textFormat: Text.PlainText; text: (modelData.message || "") + (modelData.detail ? "\n" + modelData.detail : ""); wrapMode: Text.Wrap; Layout.fillWidth: true; color: Theme.text; font.pixelSize: 12 }
                        ActionButton { text: "查看画面"; compact: true; enabled: (modelData.cue_ids || []).length > 0 || modelData.frame > 0; onClicked: (modelData.cue_ids || []).length ? results.cmd("qc.inspect", {cueId: modelData.cue_ids[0]}) : results.cmd("qc.jump", {id: modelData.id, frame: modelData.frame}) }
                    }
                }
                Label { textFormat: Text.PlainText; visible: results.issues.length === 0; anchors.centerIn: parent; width: parent.width - 20; text: "本次检查未发现明显疑点"; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; lineHeight: 1.6; color: Theme.muted; font.pixelSize: 12 }
            }
            ListView {
                id: rescanList
                anchors.fill: parent; visible: results.tab === "rescans"
                model: results.rescans; spacing: 8; clip: true
                ScrollBar.vertical: ScrollBar { }
                delegate: Rectangle {
                    required property var modelData
                    width: rescanList.width; height: rescanContent.implicitHeight + 24
                    radius: 9; color: Theme.slab; border.color: Theme.lineSoft
                    ColumnLayout {
                        id: rescanContent
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 8
                        Label { textFormat: Text.PlainText; text: results.rescanLabel(modelData.status); color: Theme.ink; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Label { textFormat: Text.PlainText; text: "范围 F" + ((modelData.range || {}).first_frame || "?") + "—F" + ((modelData.range || {}).last_frame || "?") + " · 仅供局部核对，不覆盖原报告" + (modelData.reason ? "\n" + modelData.reason : ""); color: Theme.label; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        ActionButton { text: "查看重查详情"; compact: true; enabled: results.caps["qc.report"] === true && (modelData.status === "needs_review" || modelData.status === "completed"); onClicked: results.cmd("qc.openRescan", {id: modelData.id}) }
                    }
                }
                Label { textFormat: Text.PlainText; visible: results.rescans.length === 0; anchors.centerIn: parent; width: parent.width - 20; text: "暂无局部重查记录\n可在逐句结果中针对单句调整选区或语言重新识别"; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; lineHeight: 1.6; color: Theme.muted; font.pixelSize: 12 }
            }
        }
    }
}
