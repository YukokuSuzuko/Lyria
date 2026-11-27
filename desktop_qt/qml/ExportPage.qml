import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Panel {
    id: page
    required property var controller
    required property var state
    readonly property var project: state.project || ({})
    readonly property int count: state.rowCount || 0
    readonly property int confirmed: state.confirmedCount || 0
    readonly property var omissions: (state.export || {}).omissions || []
    readonly property string omissionSignature: JSON.stringify(omissions)
    readonly property var warnings: (state.export || {}).warnings || []
    readonly property var compatibilityWarnings: (state.export || {}).compatibilityWarnings || []
    property string srtMode: "main"
    property bool srtBom: true
    onOmissionSignatureChanged: if (omissionAcknowledged) omissionAcknowledged.checked = false
    signal returnToReview()
    function cmd(name, payload) { controller.command(name, payload || {}) }
    ScrollView {
        id: scroll
        anchors.fill: parent; anchors.margins: 26
        contentWidth: availableWidth; contentHeight: exportContent.height; clip: true
    Item {
        id: exportContent
        width: scroll.availableWidth; height: Math.max(page.height - 52, (page.omissions.length ? 860 : 650) + (page.warnings.length ? 60 : 0) + (compatibilityHint.visible ? compatibilityHint.implicitHeight + 18 : 0))
    ColumnLayout {
        anchors.fill: parent; spacing: 16
        Label { textFormat: Text.PlainText; text: "保存与导出"; font.pixelSize: 20; font.weight: Font.DemiBold; color: Theme.ink }
        Label { textFormat: Text.PlainText; text: "导出字幕文件不会自动保存工程，请按需分别保存与导出。支持一键直出原生草稿 SRT / 正式 SRT 与双语 SRT。"; font.pixelSize: 12; color: Theme.label; Layout.fillWidth: true; wrapMode: Text.Wrap }
        Flow {
            Layout.fillWidth: true; spacing: 10
            Badge { text: page.confirmed + " / " + page.count + " 句已确认"; foreground: page.confirmed === page.count && page.count > 0 ? Theme.green : Theme.amber; fill: page.confirmed === page.count && page.count > 0 ? Theme.greenBg : Theme.amberBg }
            Badge { text: page.project.dirty ? "有修改未保存" : page.project.path ? "工程已保存" : "工程尚未保存"; foreground: page.project.dirty ? Theme.amber : Theme.green; fill: page.project.dirty ? Theme.amberBg : Theme.greenBg }
            Badge { text: page.count === 0 ? "暂无字幕" : page.project.unexported ? "最新修改未导出" : "已导出最新字幕" }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 94; radius: 10; color: Theme.slab
            Label { textFormat: Text.PlainText; anchors.fill: parent; anchors.margins: 14; text: "时间轴说明\n帧号从 1 开始，入点与出点均包含在内。导出 ASS / SRT 按视频画面时间精确转换（支持可变帧率 VFR），结束时间对齐出点帧末尾。"; color: Theme.label; font.pixelSize: 12; wrapMode: Text.Wrap; lineHeight: 1.55 }
        }
        Rectangle {
            objectName: "srtOptionsCard"
            Layout.fillWidth: true; implicitHeight: srtCol.implicitHeight + 24; radius: 10; color: Theme.paper; border.color: Theme.lineSoft
            ColumnLayout {
                id: srtCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 8
                Label { textFormat: Text.PlainText; text: "原生 SRT 导出设置"; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.ink }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    Repeater {
                        model: [
                            {key: "main", label: "当前主轨文字"},
                            {key: "bilingual_trans_first", label: "双语（译文在上 · 原文在下）"},
                            {key: "bilingual_source_first", label: "双语（原文在上 · 译文在下）"},
                            {key: "source_only", label: "仅原文歌词"}
                        ]
                        ActionButton {
                            required property var modelData
                            objectName: "srtMode_" + modelData.key
                            text: modelData.label
                            compact: true
                            checked: page.srtMode === modelData.key
                            onClicked: page.srtMode = modelData.key
                        }
                    }
                }
                Check {
                    id: srtBomCheck
                    objectName: "srtBomCheck"
                    text: "UTF-8 带 BOM 编码（默认，优先兼容本地播放器；取消勾选用标准无 BOM UTF-8）"
                    checked: page.srtBom
                    onToggled: page.srtBom = checked
                }
            }
        }
        Label { textFormat: Text.PlainText; text: page.project.path || "尚未保存工程文件"; font.pixelSize: 11; color: Theme.muted; Layout.fillWidth: true; elide: Text.ElideMiddle }
        Flow { Layout.fillWidth: true; spacing: 8
            ActionButton { text: "保存工程"; glyph: "save"; onClicked: page.cmd("project.save") }
            ActionButton { text: "工程另存为…"; onClicked: page.cmd("project.saveAs") }
        }
        Flow { visible: page.warnings.length > 0; Layout.fillWidth: true; spacing: 8
            Badge { text: page.warnings.length + " 条导入警告" + ((page.state.export || {}).warningsAcknowledged ? " · 已查看" : " · 待查看"); foreground: Theme.amber; fill: Theme.amberBg }
            ActionButton { text: "查看导入警告…"; compact: true; onClicked: page.cmd("project.reviewWarnings"); tip: "查看导入时的格式警告，确认无误后即可导出正式字幕" }
        }
        Item { Layout.fillHeight: true }
        Label { textFormat: Text.PlainText; text: page.count - page.confirmed > 0 ? "还有 " + (page.count - page.confirmed) + " 句待确认，可返回继续核对，或随时先导出草稿 ASS / 草稿 SRT。" : "全部句子已确认，可直接导出正式字幕。"; font.pixelSize: 12; color: page.count - page.confirmed > 0 ? Theme.amber : Theme.label; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Check { id: skipMissing; objectName: "skipMissingTimes"; text: "导出草稿时跳过未打轴的句子"; onToggled: omissionAcknowledged.checked = false }
        Rectangle {
            objectName: "exportOmissions"
            visible: page.omissions.length > 0
            Layout.fillWidth: true; Layout.preferredHeight: visible ? Math.min(200, 45 + page.omissions.length * 52) : 0
            radius: 9; color: Theme.amberBg; border.color: "#EDDABF"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 10; spacing: 6
                Label { textFormat: Text.PlainText; text: (skipMissing.checked ? "导出草稿将跳过以下 " : "以下 ") + page.omissions.length + " 句" + (skipMissing.checked ? "：" : "缺少有效时间轴，请先补全或勾选上方“跳过”"); color: Theme.amber; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: page.omissions
                    ScrollBar.vertical: ScrollBar { }
                    delegate: Label {
                        required property var modelData
                        textFormat: Text.PlainText
                        width: ListView.view.width
                        text: "第 " + modelData.number + " 句 · " + (modelData.chinese || "（空白译文）") + "\n原因：" + modelData.reason
                        color: Theme.label; font.pixelSize: 11; wrapMode: Text.Wrap; padding: 5
                    }
                }
            }
        }
        Check { id: omissionAcknowledged; objectName: "omissionAcknowledged"; visible: skipMissing.checked && page.omissions.length > 0; text: "确认跳过以上句子并导出草稿" }
        Label { textFormat: Text.PlainText; text: "提示：SRT / LRC 为纯文本格式，不保存 ASS 特效样式；标准 LRC 仅记录每句入点。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Label {
            id: compatibilityHint; objectName: "exportCompatibilityHint"
            textFormat: Text.PlainText; text: page.compatibilityWarnings.join("\n")
            visible: page.compatibilityWarnings.length > 0
            color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true
        }
        Flow { Layout.fillWidth: true; spacing: 8
            ActionButton { text: "返回逐句核对"; glyph: "arrow-l"; onClicked: page.returnToReview() }
            ActionButton { objectName: "exportDraftButton"; text: "导出草稿 ASS"; enabled: page.count > 0 && (page.omissions.length === 0 || skipMissing.checked && omissionAcknowledged.checked); onClicked: page.cmd("export.ass", {draft: true, skipMissingTimes: skipMissing.checked}) }
            ActionButton { objectName: "exportDraftSrtButton"; text: "导出草稿 SRT"; enabled: page.count > 0 && (page.omissions.length === 0 || skipMissing.checked && omissionAcknowledged.checked); onClicked: page.cmd("export.ass", {draft: true, skipMissingTimes: skipMissing.checked, format: "srt", srtMode: page.srtMode, srtBom: page.srtBom, directSrt: true}); tip: "无需逐句点满确认，按上方选项立即导出原生 SRT 草稿" }
            ActionButton { objectName: "exportFinalButton"; text: "导出 ASS 字幕"; glyph: "export"; emphasized: true; enabled: page.count > 0 && page.confirmed === page.count && page.omissions.length === 0 && (page.warnings.length === 0 || (page.state.export || {}).warningsAcknowledged === true); onClicked: page.cmd("export.ass", {draft: false}) }
            ActionButton { objectName: "exportFinalSrtButton"; text: "导出正式 SRT"; emphasized: true; enabled: page.count > 0 && page.confirmed === page.count && page.omissions.length === 0 && (page.warnings.length === 0 || (page.state.export || {}).warningsAcknowledged === true); onClicked: page.cmd("export.ass", {draft: false, format: "srt", srtMode: page.srtMode, srtBom: page.srtBom, directSrt: true}); tip: "仅导出纯净时间轴与文字（按上方内容模式与 BOM 设置）" }
            ActionButton { text: "导出 LRC"; enabled: page.count > 0 && page.confirmed === page.count && page.omissions.length === 0 && (page.warnings.length === 0 || (page.state.export || {}).warningsAcknowledged === true); onClicked: page.cmd("export.ass", {draft: false, format: "lrc"}); tip: "标准 LRC 仅记录每句入点，不含出点与样式" }
        }
    }
    }
    }
}
