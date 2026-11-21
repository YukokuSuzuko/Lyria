import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Opened only by the AE timeline's explicit "分配文字" action. Never modal,
// never opened by C itself; all edits stay in the backend transaction.
Popup {
    id: popup
    objectName: "textAllocationPopup"
    required property var controller
    required property var allocation
    required property Item anchorItem
    property bool ownerActive: true
    property int openGeneration: 0
    readonly property string semanticKey: JSON.stringify([allocation.leftId || "", allocation.rightId || "", allocation.text || ""])
    property var preview: ({valid: false, error: "点击字间位置选择切分点（不影响已打好的时间轴）"})
    modal: false; dim: false; focus: true
    width: parent ? Math.min(680, parent.width - 32) : 680
    height: parent ? Math.min(330, parent.height - 32) : 330
    padding: 14
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.paper; border.color: Theme.pendingTextAccent; radius: 10 }
    function place() {
        if (!parent || !anchorItem) return
        var origin = anchorItem.mapToItem(parent, 0, 0)
        x = Math.max(16, Math.min(parent.width - width - 16, origin.x))
        y = Math.max(16, Math.min(parent.height - height - 16, origin.y - height - 8))
    }
    onAboutToShow: place()
    Connections {
        target: popup.parent
        function onWidthChanged() { if (popup.opened) Qt.callLater(popup.place) }
        function onHeightChanged() { if (popup.opened) Qt.callLater(popup.place) }
    }
    function updatePreview() {
        preview = caret.selectionStart !== caret.selectionEnd
            ? ({valid: false, error: "请单击两个字之间的位置，不要框选文字。"})
            : controller.allocationPreview(caret.cursorPosition)
    }
    function cancelAllocation() {
        openGeneration += 1
        close()
        queueCancel()
    }
    function queueCancel() {
        // Visibility is derived from uiState. Do not emit another uiState
        // synchronously while Qt is evaluating that binding.
        var key = semanticKey
        Qt.callLater(function() {
            if (popup.semanticKey === key && popup.allocation.leftId && (!popup.ownerActive || !popup.opened))
                popup.controller.command("textAllocation.cancel", {})
        })
    }
    onOwnerActiveChanged: if (!ownerActive) cancelAllocation()
    onSemanticKeyChanged: {
        openGeneration += 1
        if (!allocation.leftId) { close(); return }
        // uiState is copied on autosave/progress refreshes. Reset only when
        // the actual pair or literal content changes, not on object identity.
        var generation = openGeneration
        Qt.callLater(function() {
            if (generation !== popup.openGeneration || !popup.ownerActive || !popup.allocation.leftId) return
            caret.cursorPosition = 0
            popup.preview = ({valid: false, error: "点击字间位置选择切分点（不影响已打好的时间轴）"})
            popup.open(); caret.forceActiveFocus()
        })
    }
    onClosed: queueCancel()
    contentItem: ColumnLayout {
        spacing: 8
        RowLayout {
            Label { textFormat: Text.PlainText; text: popup.allocation.rightId ? "分配第 " + (popup.allocation.leftNumber || "") + " / " + (popup.allocation.rightNumber || "") + " 段文字" : "第 " + (popup.allocation.leftNumber || "") + " 段 · 待分配文字"; color: Theme.ink; font.pixelSize: 13; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            ActionButton { objectName: "allocationCancelButton"; text: "取消"; compact: true; onClicked: popup.close() }
        }
        ScrollView {
            Layout.fillWidth: true; Layout.preferredHeight: 72
            TextArea {
                id: caret; objectName: "allocationTextCaret"
                text: popup.allocation.displayText || ""; textFormat: TextEdit.PlainText
                readOnly: true; selectByMouse: true; selectByKeyboard: true; cursorVisible: activeFocus; wrapMode: TextEdit.Wrap
                color: Theme.ink; font.family: Theme.fontFamily; font.pixelSize: 13
                background: Rectangle { color: Theme.slab; radius: 5; border.color: Theme.line }
                onCursorPositionChanged: popup.updatePreview()
                onSelectionStartChanged: popup.updatePreview()
                onSelectionEndChanged: popup.updatePreview()
            }
        }
        Label { textFormat: Text.PlainText; text: !popup.allocation.rightId ? "后一段已删除，请直接编辑本句歌词后点击「已手动分配」。" : popup.preview.valid ? "预览无误后点击「确认分配」，两段时间轴保持不变。" : popup.preview.error || "请点击字间位置选择切分点。"; color: Theme.label; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                Layout.fillWidth: true; Layout.preferredWidth: 1
                Label { textFormat: Text.PlainText; text: "前段"; color: Theme.label; font.pixelSize: 11 }
                ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; TextArea { objectName: "allocationLeftPreview"; text: popup.preview.left || ""; textFormat: TextEdit.PlainText; readOnly: true; wrapMode: TextEdit.Wrap; color: Theme.ink; font.pixelSize: 12; background: Rectangle { color: Theme.slab; radius: 5 } } }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.preferredWidth: 1
                Label { textFormat: Text.PlainText; text: "后段"; color: Theme.label; font.pixelSize: 11 }
                ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; TextArea { objectName: "allocationRightPreview"; text: popup.preview.right || ""; textFormat: TextEdit.PlainText; readOnly: true; wrapMode: TextEdit.Wrap; color: Theme.ink; font.pixelSize: 12; background: Rectangle { color: Theme.slab; radius: 5 } } }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            ActionButton { objectName: "allocationMarkDoneButton"; text: "已手动分配"; compact: true; tip: "已自行改好两段歌词时，点击清除待分配标记"; onClicked: popup.controller.command("textAllocation.markDone", {id: popup.allocation.leftId}) }
            Item { Layout.fillWidth: true }
            ActionButton { objectName: "allocationApplyButton"; text: "确认分配"; compact: true; enabled: !!popup.allocation.rightId && popup.preview.valid === true; onClicked: popup.controller.command("textAllocation.apply", {leftId: popup.allocation.leftId, rightId: popup.allocation.rightId, offsetUtf16: caret.cursorPosition}) }
        }
    }
}
