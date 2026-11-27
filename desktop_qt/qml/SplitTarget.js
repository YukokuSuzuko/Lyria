.pragma library

// One frame convention for lyric drops, F6, highlighting and razor affordances.
// The cut frame starts the right half. The left half must retain a frame too.
function eligible(cue, frame) {
    return !cue.locked && cue.firstFrame > 0 && frame > cue.firstFrame && frame <= cue.lastFrame
}
function nearest(markers, playhead) {
    if (!markers.length) return playhead || 0
    var chosen = markers[0].frame === undefined ? markers[0] : markers[0].frame
    for (var i = 1; i < markers.length; ++i) {
        var frame = markers[i].frame === undefined ? markers[i] : markers[i].frame
        if (Math.abs(frame - playhead) < Math.abs(chosen - playhead)) chosen = frame
    }
    return chosen
}
function range(cue) {
    if (!cue.firstFrame || !cue.lastFrame) return "当前句未打轴，无法按帧切分"
    if (cue.firstFrame >= cue.lastFrame) return "当前句仅 1 帧，无法继续切分"
    return "可切分范围 F" + (cue.firstFrame + 1) + "–F" + cue.lastFrame + "（句首 F" + cue.firstFrame + " 不可切）"
}
