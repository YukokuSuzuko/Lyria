import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Rectangle {
    id: bar
    objectName: "candidateSourceBar"
    required property var controller
    property var s: controller.uiState
    readonly property var ocrTask: s.candidateTask || ({})
    implicitHeight: barContents.implicitHeight + 10
    color: Theme.paper
    ColumnLayout {
        id: barContents
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 5; spacing: 4
        RowLayout {
            Layout.fillWidth: true; spacing: 4; visible: bar.s.timelineSwitchAvailable === true
            ComboBox {
                objectName: "timelineSourceSelector"
                Layout.fillWidth: true; Layout.minimumWidth: 80
                model: bar.s.timelineChoices || []
                textRole: "label"; valueRole: "id"
                currentIndex: {
                    var choices = bar.s.timelineChoices || []
                    for (var n = 0; n < choices.length; n++) if (choices[n].id === bar.s.candidateSource) return n
                    return 0
                }
                onActivated: index => bar.controller.switchCandidateSource(model[index].id)
            }
            Label { objectName: "candidateSourceStatus"; text: bar.s.candidateStatus || "主轴编辑"; Layout.fillWidth: true; elide: Text.ElideRight; color: Theme.label; font.pixelSize: 11 }
            ActionButton { compact: true; text: "更多"; onClicked: sourceMenu.showMenu()
                MotionMenu { id: sourceMenu; y: parent.height + 3; ownerActive: bar.visible
                    MenuItem { text: "对比与应用结果"; enabled: bar.s.candidateAvailable === true; onTriggered: bar.controller.compareCandidates() }
                    MenuItem { text: "人声自动打轴…"; enabled: (bar.s.capabilities || {})["vocal.start"] === true; onTriggered: bar.controller.command("vocal.start", {}) }
                    MenuItem { text: "本地模型设置…"; onTriggered: bar.controller.command("vocal.configure", {}) }
                    MenuItem { text: "清理识别历史…"; onTriggered: bar.controller.clearOldCandidates() }
                    MenuItem { visible: false; height: 0; text: "载入测试数据…"; onTriggered: bar.controller.loadCandidateFixture() }
                }
            }
        }
        RowLayout {
            objectName: "candidateTaskProgressRow"; visible: !!bar.ocrTask.running; Layout.fillWidth: true
            Label { text: bar.ocrTask.label || "OCR 识别中…"; Layout.fillWidth: true; elide: Text.ElideRight; color: Theme.label; font.pixelSize: 11 }
            ThemeProgressBar { objectName: "candidateTaskProgress"; Layout.preferredWidth: 80; value: bar.ocrTask.progress || 0; indeterminate: bar.ocrTask.progress === undefined }
            ActionButton { objectName: "cancelCandidateTaskButton"; compact: true; text: (bar.ocrTask.cancelling ? "正在取消" : "取消") + (bar.ocrTask.source === "vocal" ? "人声打轴" : " OCR") + (bar.ocrTask.cancelling ? "…" : ""); enabled: !bar.ocrTask.cancelling; onClicked: bar.controller.cancelCandidateJob() }
        }
    }
}
