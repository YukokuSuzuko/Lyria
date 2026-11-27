import QtQuick

// A real staggered construction sequence. Navigation and reduced motion settle
// synchronously; no task or document actions are coupled to the animation.
Item {
    id: reveal
    property bool active: true
    property int delay: 0
    property real entryOffset: 0
    readonly property bool animating: build.running
    transform: Translate { y: reveal.entryOffset }
    function settle() { build.stop(); opacity = 1; entryOffset = 0 }
    function restart() {
        build.stop()
        if (!active || !Theme.motionEnabled) { settle(); return }
        opacity = 0; entryOffset = 18; build.start()
    }
    onActiveChanged: restart()
    Component.onCompleted: restart()
    Connections { target: Theme; function onMotionEnabledChanged() { if (!Theme.motionEnabled) reveal.settle() } }
    SequentialAnimation {
        id: build
        PauseAnimation { duration: reveal.delay }
        ParallelAnimation {
            NumberAnimation { target: reveal; property: "opacity"; to: 1; duration: 300; easing.type: Easing.OutCubic }
            NumberAnimation { target: reveal; property: "entryOffset"; to: 0; duration: 380; easing.type: Easing.OutCubic }
        }
    }
}
