import QtQuick
import QtQuick.Controls

ProgressBar {
    id: control
    from: 0; to: 1
    implicitHeight: 8
    padding: 0
    background: Rectangle {
        implicitWidth: 160; implicitHeight: 8
        radius: height / 2; color: Theme.lilacSoft
        border.color: Theme.line
    }
    contentItem: Item {
        implicitWidth: 160; implicitHeight: 8
        clip: true
        Rectangle {
            id: fill
            height: parent.height; radius: height / 2
            width: control.indeterminate ? parent.width * 0.28 : parent.width * control.visualPosition
            color: Theme.lilac
            x: control.indeterminate ? phase * (parent.width - width) : 0
            property real phase: 0
            SequentialAnimation on phase {
                running: control.visible && control.indeterminate && Theme.motionEnabled
                loops: Animation.Infinite
                NumberAnimation { from: 0; to: 1; duration: 800; easing.type: Easing.InOutSine }
                NumberAnimation { from: 1; to: 0; duration: 800; easing.type: Easing.InOutSine }
            }
        }
    }
}
