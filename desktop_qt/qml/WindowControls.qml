import QtQuick
import QtQuick.Controls
import QtQuick.Window

Row {
    id: controls
    objectName: "windowControls"
    required property var targetWindow
    property var chrome: null
    readonly property bool maximized: targetWindow.visibility === Window.Maximized
    spacing: 0
    // Geometry is in logical pixels. No animations delay native window actions.
    Repeater {
        model: ["minimize", "maximize", "close"]
        Button {
            id: button
            required property string modelData
            objectName: "window" + (modelData === "minimize" ? "Minimize" : modelData === "maximize" ? "Maximize" : "Close")
            width: 36; height: 31
            hoverEnabled: true
            // Keyboard traversal remains available, but clicking native-style
            // controls should not steal focus from an editor just to maximize.
            focusPolicy: Qt.TabFocus
            text: modelData === "minimize" ? "最小化" : modelData === "maximize" ? (controls.maximized ? "还原" : "最大化") : "关闭窗口"
            Accessible.name: text
            ToolTip.visible: hovered
            ToolTip.text: text
            ToolTip.delay: 650
            background: Rectangle {
                radius: 6
                color: button.modelData === "close" && (button.hovered || button.down) ? (button.down ? "#BD3F55" : "#D94D63") :
                       button.down ? Theme.lilacSoft : button.hovered ? "#EDE7F7" : "transparent"
                border.width: button.activeFocus ? 2 : 0
                border.color: Theme.lilac
            }
            contentItem: Item {
                readonly property color ink: button.modelData === "close" && (button.hovered || button.down) ? "white" : Theme.label
                Rectangle {
                    visible: button.modelData === "minimize"
                    anchors.centerIn: parent; width: 11; height: 1; color: parent.ink
                }
                Rectangle {
                    visible: button.modelData === "maximize" && controls.maximized
                    x: (parent.width - 9) / 2 + 2; y: (parent.height - 9) / 2 - 2
                    width: 9; height: 9; color: "transparent"; border.color: parent.ink
                }
                Rectangle {
                    visible: button.modelData === "maximize"
                    anchors.centerIn: parent; width: controls.maximized ? 9 : 11; height: width
                    color: button.down ? Theme.lilacSoft : button.hovered ? "#EDE7F7" : Theme.paper
                    border.color: parent.ink
                }
                Rectangle { visible: button.modelData === "close"; anchors.centerIn: parent; width: 13; height: 1; rotation: 45; color: parent.ink }
                Rectangle { visible: button.modelData === "close"; anchors.centerIn: parent; width: 13; height: 1; rotation: -45; color: parent.ink }
            }
            onClicked: {
                if (modelData === "minimize") { if (!controls.chrome || !controls.chrome.minimize(controls.targetWindow)) controls.targetWindow.showMinimized() }
                else if (modelData === "maximize") {
                    if (controls.chrome && controls.chrome.toggleMaximize(controls.targetWindow)) return
                    if (controls.maximized) controls.targetWindow.showNormal()
                    else controls.targetWindow.showMaximized()
                } else controls.targetWindow.close() // Must run Main.qml onClosing.
            }
        }
    }
}
