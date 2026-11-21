import QtQuick
import QtQuick.Controls
import QtQuick.Window

Item {
    id: marker
    property int frame: 1
    property string markerId: ""
    objectName: "fixedMarker_" + frame
    signal seek(int frame)
    signal remove(string markerId, int frame)
    width: 22; height: 42
    z: dragArea.drag.active ? 1000 : 10
    Accessible.role: Accessible.Button
    Accessible.name: "标记点 F" + frame + "，拖到歌词字间可切分句子；按 Delete 删除"
    activeFocusOnTab: true
    Keys.onDeletePressed: remove(markerId, frame)
    Keys.onReturnPressed: seek(frame)
    Image { id: markerHead; objectName: marker.objectName + "Head"; x: (parent.width - width) / 2; y: 4; width: 14; height: 14; source: "../assets/icons/fixed-marker.svg"; sourceSize.width: 28; sourceSize.height: 28 }
    Rectangle { visible: marker.activeFocus; x: (parent.width - width) / 2; y: 2; width: 18; height: 18; radius: 3; color: "transparent"; border.color: Theme.ink }
    // fixed-marker.svg has its actual lower tip at 15/16 of the image height.
    // Start half a logical pixel before it to avoid a fractional-DPI seam;
    // the lower dash still stops above the playhead thumb paint/hit band.
    readonly property real headTipY: markerHead.y + markerHead.height * 15 / 16
    Rectangle { objectName: marker.objectName + "GuideTop"; x: (parent.width - width) / 2; y: marker.headTipY - 0.5; width: 1; height: 4; color: Theme.lilac; opacity: .75 }
    Rectangle { objectName: marker.objectName + "GuideBottom"; x: (parent.width - width) / 2; y: 23; width: 1; height: 1; color: Theme.lilac; opacity: .75 }
    Item {
        id: proxy
        width: 22; height: 26
        visible: dragArea.drag.active
        Drag.active: dragArea.drag.active
        Drag.keys: ["lyria-fixed-marker"]
        Drag.source: marker
        Drag.hotSpot.x: dragArea.grabX
        Drag.hotSpot.y: dragArea.grabY
        Drag.supportedActions: Qt.CopyAction
        Image { x: (parent.width - width) / 2; y: 0; width: 14; height: 14; source: "../assets/icons/fixed-marker.svg" }
        Badge { text: "F" + marker.frame; y: -24; anchors.horizontalCenter: parent.horizontalCenter }
    }
    MouseArea {
        id: dragArea
        width: parent.width; height: 18
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        property real grabX: 11
        property real grabY: 13
        drag.target: proxy
        drag.threshold: 5
        drag.smoothed: false
        onPressed: mouse => { grabX = mouse.x; grabY = mouse.y }
        onClicked: mouse => { marker.forceActiveFocus(); if (mouse.button === Qt.RightButton) markerMenu.popup(); else marker.seek(marker.frame) }
        onReleased: { if (drag.active) proxy.Drag.drop(); proxy.x = 0; proxy.y = 0 }
        onCanceled: { proxy.Drag.cancel(); proxy.x = 0; proxy.y = 0 }
    }
    ToolTip.visible: dragArea.containsMouse && marker.visible && !dragArea.drag.active && !!Window.window && Window.window.active
    ToolTip.text: "标记点 F" + frame + " · 拖到歌词字间可切分句子，右键或按 Delete 删除"
    ToolTip.delay: 600
    Menu {
        id: markerMenu
        MenuItem { text: "跳至 F" + marker.frame; onTriggered: marker.seek(marker.frame) }
        MenuItem { text: "删除标记点"; onTriggered: marker.remove(marker.markerId, marker.frame) }
    }
}
