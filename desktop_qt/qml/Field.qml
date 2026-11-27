import QtQuick
import QtQuick.Controls

TextField {
    id: control
    implicitHeight: 34
    font.family: Theme.fontFamily
    font.pixelSize: 12
    color: Theme.ink
    selectByMouse: true
    placeholderTextColor: Theme.faint
    leftPadding: 10; rightPadding: 10
    background: Rectangle {
        radius: 7
        color: control.enabled ? Theme.paper : Theme.slab
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.lilac : Theme.line
    }
}
