import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    implicitHeight: 34
    font.family: Theme.fontFamily
    font.pixelSize: 11
    leftPadding: 8; rightPadding: 23
    indicator: Image {
        source: "../assets/icons/chev-down.svg"
        x: control.width - width - 6
        y: (control.height - height) / 2
        width: 14; height: 14
    }
    contentItem: Text { textFormat: Text.PlainText;
        text: control.displayText
        font: control.font
        color: Theme.label
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        color: Theme.paper; radius: 7
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.lilac : Theme.line
    }
}
