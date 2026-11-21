import QtQuick
import QtQuick.Controls

CheckBox {
    id: control
    implicitHeight: 28
    spacing: 7
    font.family: Theme.fontFamily
    font.pixelSize: 11
    opacity: enabled ? 1 : 0.45
    indicator: Rectangle {
        x: 0; y: (control.height - height) / 2
        width: 16; height: 16; radius: 4
        color: control.checked ? Theme.lilac : Theme.paper
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.lilacDark : control.checked ? Theme.lilac : "#CFC4DE"
        Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "✓"; color: "white"; font.pixelSize: 12; visible: control.checked }
    }
    contentItem: Text { textFormat: Text.PlainText;
        text: control.text
        font: control.font
        color: Theme.label
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter
    }
}
