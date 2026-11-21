import QtQuick

// Canvas text with a moving lilac light band, not a looping progress estimate.
// Runs only for a real busy label. Reduced motion leaves ordinary readable text.
Item {
    id: root
    property string text: ""
    property bool running: false
    property int fontPixelSize: 12
    property string fontFamily: Theme.fontFamily
    property color color: Theme.label
    property real phase: 0
    readonly property bool animating: shine.running
    implicitWidth: fullMetrics.width
    implicitHeight: metrics.height + 2
    Accessible.role: Accessible.StaticText
    Accessible.name: text
    TextMetrics { id: fullMetrics; text: root.text; font.family: root.fontFamily; font.pixelSize: root.fontPixelSize }
    TextMetrics { id: metrics; text: root.text; font.family: root.fontFamily; font.pixelSize: root.fontPixelSize }
    onTextChanged: canvas.requestPaint()
    onPhaseChanged: canvas.requestPaint()
    onColorChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onRunningChanged: canvas.requestPaint()
    Connections { target: Theme; function onMotionEnabledChanged() { canvas.requestPaint() } }
    NumberAnimation on phase {
        id: shine; from: 0; to: 1; duration: 1700; loops: Animation.Infinite
        running: root.running && root.visible && Theme.motionEnabled
    }
    Canvas {
        id: canvas; anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            ctx.font = root.fontPixelSize + 'px "' + root.fontFamily + '"'
            ctx.textBaseline = "middle"
            if (root.running && Theme.motionEnabled) {
                // Travel across glyphs, not the empty remainder of a wide layout row.
                var textWidth = Math.min(width, fullMetrics.width)
                var center = -65 + root.phase * (textWidth + 130)
                var gradient = ctx.createLinearGradient(center - 65, 0, center + 65, 0)
                gradient.addColorStop(0, "#9276B6"); gradient.addColorStop(.35, "#B298D6")
                gradient.addColorStop(.5, "#E4D4FA"); gradient.addColorStop(.65, "#B298D6")
                gradient.addColorStop(1, "#9276B6"); ctx.fillStyle = gradient
            } else ctx.fillStyle = root.color
            ctx.fillText(metrics.elidedText, 0, height / 2)
        }
    }
    Binding { target: metrics; property: "elide"; value: Text.ElideRight }
    Binding { target: metrics; property: "elideWidth"; value: root.width }
}
