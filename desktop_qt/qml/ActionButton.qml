import QtQuick
import QtQuick.Window
import QtQuick.Controls

Button {
    id: control
    property string glyph: ""
    property real glyphSize: 15
    property bool primary: false
    property bool emphasized: false
    property bool quiet: false
    property bool destructive: false
    property bool compact: false
    // Toggle activation is independent from focus. Keyboard focus remains visible
    // as a separate inset dotted cue, never as the activated outer outline.
    readonly property bool stateOutline: checkable && checked
    property string tip: text
    property bool motion: Theme.motionEnabled
    implicitHeight: compact ? 27 : 34
    implicitWidth: Math.ceil(Math.max(glyph && !text ? implicitHeight : 0, implicitContentWidth + leftPadding + rightPadding))
    padding: compact ? 6 : 10
    horizontalPadding: glyph && !text ? 3 : padding + 2
    spacing: 5
    opacity: enabled ? 1 : 0.42
    scale: pressMotion.value
    MotionNumber { id: pressMotion; targetValue: control.down ? 0.97 : 1.0; motion: control.motion; duration: control.down ? 70 : 150 }
    hoverEnabled: true
    Accessible.name: text || tip
    readonly property bool toolTipVisible: helpTip.visible
    ToolTip {
        id: helpTip
        objectName: control.objectName + "Tooltip"
        visible: control.hovered && control.visible && control.enabled && !control.down && !!control.Window.window && control.Window.window.active && control.tip.length > 0
        text: control.tip
        delay: 650
        timeout: 6000
        // One owner per popup; shared attached tooltips can outlive a hidden
        // or replaced owner during workspace/splitter changes.
        enter: Transition { }
        exit: Transition { }
    }
    background: Rectangle {
        radius: control.primary ? 9 : 7
        color: fillMotion.value
        MotionColor {
            id: fillMotion; motion: control.motion
            targetValue: control.primary ? (control.down ? Theme.lilacDark : Theme.lilac) :
               control.destructive ? (control.down ? "#F2DBE0" : control.hovered ? "#FAEBEF" : "#FCF5F7") :
               control.emphasized ? (control.down ? Theme.emphasisPressed : control.hovered ? Theme.emphasisHover : Theme.emphasis) :
               control.checked || control.down ? Theme.lilacSoft : control.hovered ? "#FAF8FE" : control.quiet ? "transparent" : Theme.paper
        }
        border.width: control.stateOutline || (!control.checkable && control.visualFocus) ? 2 : (control.quiet && !control.hovered && !control.checked ? 0 : 1)
        border.color: borderMotion.value
        MotionColor { id: borderMotion; motion: control.motion; targetValue: control.stateOutline || (!control.checkable && control.visualFocus) ? Theme.lilac : control.primary ? "transparent" : control.destructive ? "#E8C8D1" : control.emphasized ? Theme.emphasisBorder : control.hovered ? "#CFC0E8" : Theme.line }
    }
    Rectangle {
        objectName: control.objectName + "KeyboardFocus"
        anchors.fill: parent; anchors.margins: 4; radius: 4
        color: "transparent"; border.color: Theme.label; opacity: 0.55
        visible: control.checkable && control.visualFocus
    }
    contentItem: Item {
        implicitWidth: contents.naturalWidth
        implicitHeight: contents.implicitHeight
        Row {
            id: contents
            objectName: control.objectName + "Content"
            spacing: control.spacing
            readonly property real iconSpace: control.glyph.length ? control.glyphSize + (control.text.length ? spacing : 0) : 0
            readonly property real naturalWidth: buttonLabel.implicitWidth + iconSpace
            width: Math.min(parent.width, naturalWidth)
            x: (parent.width - width) / 2
            y: (parent.height - height) / 2
            Image {
                visible: control.glyph.length > 0
                width: visible ? control.glyphSize : 0; height: control.glyphSize
                y: (parent.height - height) / 2
                source: control.glyph ? "../assets/icons/" + control.glyph + (control.primary ? "-white" : "") + ".svg" : ""
                sourceSize.width: 24; sourceSize.height: 24
                opacity: 1
            }
            Text { textFormat: Text.PlainText;
                id: buttonLabel
                text: control.text
                width: Math.max(0, contents.width - contents.iconSpace)
                elide: Text.ElideRight
                visible: text.length > 0
                y: (parent.height - height) / 2
                font.family: Theme.fontFamily
                font.pixelSize: control.compact ? 11 : 12
                font.weight: control.primary || control.emphasized ? Font.DemiBold : Font.Medium
                color: control.primary ? "white" : control.destructive ? "#A55C70" : control.checked || control.emphasized ? Theme.lilacDark : Theme.label
            }
        }
    }
}
