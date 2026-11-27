import QtQuick

Rectangle {
    id: badge
    property string text: ""
    property color foreground: Theme.lilacDark
    property color fill: Theme.lilacSoft
    implicitWidth: label.implicitWidth + 16
    implicitHeight: 25
    radius: 6
    color: fillMotion.value
    MotionColor { id: fillMotion; targetValue: badge.fill; duration: 150 }
    MotionColor { id: textMotion; targetValue: badge.foreground; duration: 150 }
    Text { textFormat: Text.PlainText;
        id: label
        anchors.centerIn: parent
        text: badge.text
        color: textMotion.value
        font.family: Theme.fontFamily
        font.pixelSize: 11
        font.weight: Font.Medium
    }
}
