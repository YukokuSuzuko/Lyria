import QtQuick
import QtQuick.Window

// Eight disjoint edge/corner hit regions; the content area does not receive a
// drag overlay. Narrow edge hit regions overlay the flush opaque client edge.
Item {
    id: frame
    objectName: "windowResizeFrame"
    required property var targetWindow
    required property var chrome
    readonly property real borderSize: 6
    enabled: !!chrome && chrome.enabled && targetWindow.visibility === Window.Windowed
    visible: enabled
    z: 100
    Repeater {
        model: [Qt.TopEdge | Qt.LeftEdge, Qt.TopEdge, Qt.TopEdge | Qt.RightEdge,
                Qt.LeftEdge, Qt.RightEdge,
                Qt.BottomEdge | Qt.LeftEdge, Qt.BottomEdge, Qt.BottomEdge | Qt.RightEdge]
        MouseArea {
            required property int modelData
            readonly property bool edgeLeft: (modelData & Qt.LeftEdge) !== 0
            readonly property bool edgeRight: (modelData & Qt.RightEdge) !== 0
            readonly property bool edgeTop: (modelData & Qt.TopEdge) !== 0
            readonly property bool edgeBottom: (modelData & Qt.BottomEdge) !== 0
            readonly property bool corner: (edgeLeft || edgeRight) && (edgeTop || edgeBottom)
            objectName: "windowResize_" + modelData
            x: edgeLeft ? 0 : edgeRight ? frame.width - frame.borderSize : frame.borderSize
            y: edgeTop ? 0 : edgeBottom ? frame.height - frame.borderSize : frame.borderSize
            width: edgeLeft || edgeRight ? frame.borderSize : Math.max(0, frame.width - 2 * frame.borderSize)
            height: edgeTop || edgeBottom ? frame.borderSize : Math.max(0, frame.height - 2 * frame.borderSize)
            cursorShape: corner ? ((edgeLeft && edgeTop) || (edgeRight && edgeBottom) ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor) :
                         edgeLeft || edgeRight ? Qt.SizeHorCursor : Qt.SizeVerCursor
            acceptedButtons: Qt.LeftButton
            onPressed: mouse => { mouse.accepted = frame.chrome.startResize(frame.targetWindow, modelData) }
        }
    }
}
