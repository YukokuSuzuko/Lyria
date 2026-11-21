import QtQuick
import QtQuick.Controls

ActionButton {
    id: control
    property int delta: 0
    property bool windowActive: true
    signal step(int delta)
    glyphSize: glyph === "play-cue" ? 18 : 15
    compact: true
    implicitWidth: 32
    implicitHeight: 31
    background: Rectangle {
        radius: 7
        color: fillMotion.value
        MotionColor { id: fillMotion; targetValue: (control.down || control.checked) ? Theme.lilacSoft : control.hovered ? "#EAE3F6" : "transparent"; motion: control.motion }
        border.width: control.visualFocus || control.checked ? 1 : 0
        border.color: Theme.lilac
    }
    onPressed: {
        if (delta !== 0) { step(delta); delay.restart() }
    }
    onReleased: stop()
    onCanceled: stop()
    onWindowActiveChanged: if (!windowActive) stop()
    onActiveFocusChanged: if (!activeFocus) stop()
    onVisibleChanged: if (!visible) stop()
    onDownChanged: if (!down) stop()
    onEnabledChanged: if (!enabled) stop()
    function stop() { delay.stop(); repeat.stop() }
    Timer { id: delay; interval: 330; onTriggered: if (control.down && control.windowActive) repeat.start() }
    Timer { id: repeat; interval: 90; repeat: true; onTriggered: { if (control.down && control.windowActive) control.step(control.delta); else control.stop() } }
    Component.onDestruction: stop()
}
