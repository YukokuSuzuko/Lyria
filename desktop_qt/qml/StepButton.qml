import QtQuick
import QtQuick.Controls

ActionButton {
    id: control
    property int stepNumber: 1
    property bool completed: false
    compact: true
    quiet: true
    // Reserve the indicator width so completion never shifts the step strip.
    contentItem: Item {
        implicitWidth: contents.implicitWidth
        implicitHeight: contents.implicitHeight
        Row {
            id: contents
            objectName: control.objectName + "Content"
            x: (parent.width - width) / 2
            y: (parent.height - height) / 2
            spacing: 5
            Item {
                width: 18; height: 18
                y: (parent.height - height) / 2
                Text {
                    x: (parent.width - width) / 2; y: (parent.height - height) / 2
                    text: control.stepNumber
                    font.family: Theme.fontFamily; font.pixelSize: 10
                    color: control.checked ? Theme.lilacDark : Theme.muted
                    opacity: numberOpacity.value
                    MotionNumber { id: numberOpacity; targetValue: control.completed ? 0 : 1; duration: 120 }
                }
                Rectangle {
                    id: check
                    objectName: control.objectName + "Check"
                    anchors.fill: parent; radius: 9
                    color: Theme.greenBg
                    border.width: 1; border.color: "#D5ECE3"
                    opacity: checkOpacity.value
                    scale: checkScale.value
                    MotionNumber { id: checkOpacity; targetValue: control.completed ? 1 : 0; duration: 150 }
                    MotionNumber { id: checkScale; targetValue: control.completed ? 1 : 0.72; duration: 180 }
                    Image { x: (parent.width - width) / 2; y: (parent.height - height) / 2; width: 11; height: 11; source: "../assets/icons/check-green.svg" }
                }
            }
            Text {
                textFormat: Text.PlainText; text: control.text
                font.family: Theme.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
                color: control.checked ? Theme.lilacDark : Theme.label
                y: (parent.height - height) / 2
            }
        }
    }
    Accessible.name: text + (completed ? "，已完成" : "")
    tip: text + (completed ? " · 已完成" : "")
}
