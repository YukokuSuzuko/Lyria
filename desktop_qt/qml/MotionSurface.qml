import QtQuick

// Keep layout geometry fixed: only the scene node moves during page entry.
// The old page loses visibility and input ownership immediately, never after a fade.
Item {
    id: surface
    property bool active: false
    property string pageKey: ""
    property bool motion: Theme.motionEnabled
    property bool initialized: false
    readonly property bool animating: entrance.running
    readonly property real entryOffset: offset.y
    visible: active
    enabled: active
    transform: Translate { id: offset; y: 0 }

    function settle() {
        entrance.stop()
        opacity = 1
        offset.y = 0
    }
    function enterPage() {
        settle()
        if (active && motion) entrance.start()
    }
    onActiveChanged: if (initialized) enterPage()
    onPageKeyChanged: if (initialized && active) enterPage()
    onMotionChanged: if (!motion) settle()
    Component.onCompleted: { initialized = true; enterPage() }
    ParallelAnimation {
        id: entrance
        NumberAnimation { target: surface; property: "opacity"; from: 0; to: 1; duration: 150; easing.type: Easing.OutCubic }
        NumberAnimation { target: offset; property: "y"; from: 6; to: 0; duration: 180; easing.type: Easing.OutCubic }
    }
}
