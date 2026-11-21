import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Item {
    id: status
    objectName: "reviewTaskStatus"
    required property var controller
    required property var state
    readonly property var task: state.candidateTask || ({})
    visible: !!task.running
    implicitWidth: visible ? 200 : 0; implicitHeight: visible ? 27 : 0
    RowLayout {
        anchors.fill:parent; spacing:4
        Label { objectName:"reviewTaskLabel"; Layout.fillWidth:true; Layout.minimumWidth:0; text:status.task.label || "处理中"; elide:Text.ElideRight; color:Theme.label; font.pixelSize:10
            ToolTip.visible:hover.hovered;ToolTip.text:text;HoverHandler{id:hover}
        }
        ThemeProgressBar { objectName:"reviewTaskProgress"; Layout.preferredWidth:42; value:status.task.running ? (status.task.progress || 0) : 0; indeterminate:!!status.task.running && status.task.progress===undefined; enabled:!!status.task.running }
        ActionButton { objectName:"reviewCancelCandidateTask"; text:status.task.source === "vocal" ? (status.task.cancelling ? "正在取消打轴…" : "取消打轴") : (status.task.cancelling ? "正在取消 OCR…" : "取消 OCR"); tip:text; compact:true; enabled:!!status.task.running && !status.task.cancelling; onClicked:status.controller.cancelCandidateJob() }
    }
}
