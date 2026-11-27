import QtQuick

// Animate a value, not a bound layout/property. Explicit animation ownership
// means the off switch can stop immediately without removing the source binding.
QtObject {
    id: tween
    property real targetValue: 0
    property bool motion: Theme.motionEnabled
    property int duration: 180
    property int easingType: Easing.OutCubic
    property real currentValue: targetValue
    property bool initialized: false
    readonly property real value: motion ? currentValue : targetValue
    readonly property bool running: animation.running
    function update() {
        animation.stop()
        if (!initialized || !motion || currentValue === targetValue) currentValue = targetValue
        else { animation.from = currentValue; animation.to = targetValue; animation.duration = duration; animation.start() }
    }
    onTargetValueChanged: update()
    onDurationChanged: if (initialized && currentValue !== targetValue) update()
    onMotionChanged: { animation.stop(); currentValue = targetValue }
    Component.onCompleted: { currentValue = targetValue; initialized = true }
    property NumberAnimation animation: NumberAnimation {
        target: tween; property: "currentValue"; to: tween.targetValue
        duration: tween.duration; easing.type: tween.easingType
    }
}
