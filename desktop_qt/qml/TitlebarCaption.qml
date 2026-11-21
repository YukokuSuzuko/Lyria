import QtQuick
import QtQuick.Controls
import QtQuick.Window

// Only this explicitly reserved gap receives caption gestures. Never place a
// drag MouseArea over the menu, toolbar buttons, or window-control buttons.
Item {
    id: caption
    objectName: "titlebarCaption"
    required property var targetWindow
    required property var chrome
    readonly property bool available: !!chrome && chrome.enabled
    Label {
        anchors.fill: parent
        anchors.leftMargin: 6; anchors.rightMargin: 6
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: Text.AlignHCenter
        textFormat: Text.PlainText
        text: caption.available ? caption.targetWindow.title : ""
        elide: Text.ElideRight
        font.pixelSize: 10
        color: Theme.muted
    }
    MouseArea {
        id: gesture
        objectName: "titlebarCaptionMouse"
        anchors.fill: parent
        enabled: caption.available
        acceptedButtons: Qt.LeftButton
        property point pressPoint
        property bool moveRequested: false
        onPressed: mouse => { pressPoint = Qt.point(mouse.x, mouse.y); moveRequested = false }
        onPositionChanged: mouse => {
            if (!pressed || moveRequested) return
            if (Math.abs(mouse.x - pressPoint.x) + Math.abs(mouse.y - pressPoint.y) < Qt.styleHints.startDragDistance) return
            moveRequested = true
            caption.chrome.startMove(caption.targetWindow)
        }
        onReleased: moveRequested = false
        onCanceled: moveRequested = false
        onDoubleClicked: {
            if (caption.chrome && caption.chrome.toggleMaximize(caption.targetWindow)) return
            if (caption.targetWindow.visibility === Window.Maximized) caption.targetWindow.showNormal()
            else caption.targetWindow.showMaximized()
        }
    }
}
