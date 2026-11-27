import QtQuick

QtObject {
    id: tween
    property color targetValue: "transparent"
    property bool motion: Theme.motionEnabled
    property int duration: 120
    property color currentValue: targetValue
    property bool initialized: false
    readonly property color value: motion ? currentValue : targetValue
    function update() {
        animation.stop()
        if (!initialized || !motion || currentValue === targetValue) currentValue = targetValue
        else {
            // QColor "transparent" is transparent BLACK. Straight RGBA
            // interpolation makes a pale hover fill briefly dark grey. Borrow
            // the visible endpoint's RGB while keeping the zero alpha.
            var from = currentValue, to = targetValue
            if (from.a === 0) from = Qt.rgba(to.r, to.g, to.b, 0)
            if (to.a === 0) to = Qt.rgba(from.r, from.g, from.b, 0)
            animation.from = from; animation.to = to
            animation.duration = duration; animation.start()
        }
    }
    onTargetValueChanged: update()
    onDurationChanged: if (initialized && currentValue !== targetValue) update()
    onMotionChanged: { animation.stop(); currentValue = targetValue }
    Component.onCompleted: { currentValue = targetValue; initialized = true }
    property ColorAnimation animation: ColorAnimation {
        target: tween; property: "currentValue"; to: tween.targetValue
        duration: tween.duration; easing.type: Easing.OutCubic
    }
}
