import QtQuick
import QtQuick.Controls

Menu {
    id: menu
    property bool motion: Theme.motionEnabled
    property bool closing: false
    property bool ownerActive: true
    property bool settled: false
    property bool reopenPending: false
    property real visualOpacity: 1
    property real visualScale: 1
    readonly property bool animating: motion && !settled && (enter.running || exit.running)
    // Transition jobs are owned by Popup and cannot be completed through the
    // NumberAnimation API. Bypass their visual values immediately when motion
    // is disabled, and keep them bypassed until the next open/close request.
    // Qt still owns closing, focus restoration, Escape, and outside dismissal.
    opacity: (!motion || settled) ? (closing ? 0 : 1) : visualOpacity
    scale: (!motion || settled) ? (closing ? 0.96 : 1) : visualScale
    enabled: !closing && ownerActive
    popupType: Popup.Item
    transformOrigin: Item.TopRight
    padding: 5
    font.family: Theme.fontFamily
    font.pixelSize: 12
    palette.text: Theme.label
    palette.windowText: Theme.label
    palette.highlight: Theme.lilacSoft
    palette.highlightedText: Theme.lilacDark
    function showMenu() {
        // Qt 6.8 ignores open() while its native exit transition is running.
        if (closing && visible) reopenPending = true
        else open()
    }
    onAboutToShow: { closing = false; settled = !motion; reopenPending = false }
    onClosed: if (reopenPending) Qt.callLater(function() {
        if (menu.reopenPending && menu.ownerActive) menu.open()
        menu.reopenPending = false
    })
    onAboutToHide: { closing = true; settled = !motion }
    onMotionChanged: if (!motion) settled = true
    onOwnerActiveChanged: if (!ownerActive) { reopenPending = false; close() }
    Shortcut {
        sequence: "Escape"
        enabled: menu.reopenPending
        onActivated: menu.reopenPending = false
    }
    enter: Transition {
        NumberAnimation { target: menu; property: "visualOpacity"; from: 0; to: 1; duration: menu.motion ? 120 : 0; easing.type: Easing.OutCubic }
        NumberAnimation { target: menu; property: "visualScale"; from: 0.94; to: 1; duration: menu.motion ? 140 : 0; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { target: menu; property: "visualOpacity"; to: 0; duration: menu.motion ? 110 : 0; easing.type: Easing.OutCubic }
        NumberAnimation { target: menu; property: "visualScale"; to: 0.96; duration: menu.motion ? 120 : 0; easing.type: Easing.OutCubic }
    }
    background: Rectangle {
        implicitWidth: 240
        radius: 10
        color: Theme.paper
        border.color: Theme.line
    }
}
