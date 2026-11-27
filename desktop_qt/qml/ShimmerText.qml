import QtQuick
import QtQuick.Window

// Crisp native text when idle; moving lilac shimmer canvas only when actively running.
Item {
    id: root
    property string text: ""
    property bool running: false
    property int fontPixelSize: 12
    property string fontFamily: Theme.fontFamily
    property color color: Theme.label
    property real phase: 0
    readonly property bool useShimmer: root.running && root.visible && Theme.motionEnabled
    readonly property bool animating: shine.running
    implicitWidth: fullMetrics.width
    implicitHeight: Math.max(metrics.height + 2, staticText.implicitHeight)
    Accessible.role: Accessible.StaticText
    Accessible.name: text
    TextMetrics { id: fullMetrics; text: root.text; font.family: root.fontFamily; font.pixelSize: root.fontPixelSize }
    TextMetrics { id: metrics; text: root.text; font.family: root.fontFamily; font.pixelSize: root.fontPixelSize }
    onTextChanged: if (useShimmer) canvas.requestPaint()
    onPhaseChanged: if (useShimmer) canvas.requestPaint()
    onColorChanged: if (useShimmer) canvas.requestPaint()
    onWidthChanged: if (useShimmer) canvas.requestPaint()
    onUseShimmerChanged: if (useShimmer) canvas.requestPaint()
    NumberAnimation on phase {
        id: shine; from: 0; to: 1; duration: 1700; loops: Animation.Infinite
        running: root.useShimmer
    }
    Text {
        id: staticText
        anchors.fill: parent
        visible: !root.useShimmer
        text: root.text
        textFormat: Text.PlainText
        renderType: Text.NativeRendering
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        color: root.color
        font.family: root.fontFamily
        font.pixelSize: root.fontPixelSize
    }
    Canvas {
        id: canvas
        anchors.fill: parent
        visible: root.useShimmer
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.clearRect(0, 0, width, height)
            ctx.font = root.fontPixelSize + 'px "' + root.fontFamily + '"'
            ctx.textBaseline = "middle"
            var textWidth = Math.min(width, fullMetrics.width)
            var center = -65 + root.phase * (textWidth + 130)
            var gradient = ctx.createLinearGradient(center - 65, 0, center + 65, 0)
            gradient.addColorStop(0, "#7559A3"); gradient.addColorStop(.35, "#9C7DC9")
            gradient.addColorStop(.5, "#D1B9F5"); gradient.addColorStop(.65, "#9C7DC9")
            gradient.addColorStop(1, "#7559A3"); ctx.fillStyle = gradient
            ctx.fillText(metrics.elidedText, 0, height / 2)
        }
    }
    Binding { target: metrics; property: "elide"; value: Text.ElideRight }
    Binding { target: metrics; property: "elideWidth"; value: root.width }
}

