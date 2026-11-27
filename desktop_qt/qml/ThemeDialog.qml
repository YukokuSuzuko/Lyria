import QtQuick
import QtQuick.Controls

Dialog {
    id: dialog
    implicitWidth: Math.max(280, implicitContentWidth + leftPadding + rightPadding)
    padding: 16
    font.family: Theme.fontFamily
    background: Rectangle { color: Theme.paper; radius: 14; border.width: 1; border.color: Theme.line }
    Overlay.modal: Rectangle { color: "#5529213A" }
    header: Label {
        visible: dialog.title.length > 0
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        background: Rectangle { color: Theme.lilacSoft; radius: 8 }
        text: dialog.title
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        leftPadding: 16; rightPadding: 16; topPadding: 10; bottomPadding: 10
        font.family: Theme.fontFamily; font.pixelSize: 16; font.weight: Font.DemiBold
        color: Theme.ink
    }
    footer: DialogButtonBox {
        visible: count > 0
        padding: 10; spacing: 8
        background: Rectangle { color: "transparent" }
        delegate: ActionButton {
            compact: true
            primary: DialogButtonBox.buttonRole === DialogButtonBox.AcceptRole || DialogButtonBox.buttonRole === DialogButtonBox.YesRole
            destructive: DialogButtonBox.buttonRole === DialogButtonBox.DestructiveRole
        }
    }
}
