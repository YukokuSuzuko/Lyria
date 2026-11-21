.pragma library

// All coordinates are frame boundaries: an inclusive last frame ends at last+1.
// Keeping the search in this space preserves duration and works with VFR PTS.
function targets(rows, excluded, total, markers) {
    var result = []
    for (var i = 0; i < rows.length; ++i) {
        var row = rows[i]
        if (excluded.indexOf(row.cueId) !== -1) continue
        if (row.firstFrame > 0 && row.firstFrame <= total)
            result.push({frame: row.firstFrame, kind: "start", number: row.number, priority: 1})
        if (row.lastFrame > 0 && row.lastFrame <= total)
            result.push({frame: row.lastFrame + 1, kind: "end", number: row.number, priority: 1})
    }
    markers = markers || []
    for (var m = 0; m < markers.length; ++m) {
        var frame = markers[m].frame === undefined ? markers[m] : markers[m].frame
        if (frame > 0 && frame <= total) result.push({frame: frame, kind: "marker", number: 0, priority: 1})
    }
    return result
}

function resolve(edges, rawDelta, minimum, maximum, candidates, playhead, pixelsPerFrame, enabled) {
    var delta = Math.max(minimum, Math.min(maximum, Math.round(rawDelta)))
    var answer = {delta: delta, snapped: false, frame: 0, kind: "", number: 0}
    if (!enabled || !(pixelsPerFrame > 0)) return answer
    var all = candidates
    if (playhead > 0) all = [{frame: playhead, kind: "playhead", number: 0, priority: 0}].concat(candidates)
    var best = null
    for (var i = 0; i < all.length; ++i) {
        var target = all[i]
        for (var j = 0; j < edges.length; ++j) {
            var candidateDelta = target.frame - edges[j]
            if (candidateDelta < minimum || candidateDelta > maximum) continue
            var distance = Math.abs(candidateDelta - delta) * pixelsPerFrame
            if (distance > 8 + 1e-6) continue
            // Deterministic ties: playhead, earlier boundary, then the first edge.
            if (!best || distance < best.distance - 1e-6 || Math.abs(distance - best.distance) <= 1e-6 &&
                    (target.priority < best.priority || target.priority === best.priority && target.frame < best.frame)) {
                best = {distance: distance, priority: target.priority, frame: target.frame}
                answer = {delta: candidateDelta, snapped: true, frame: target.frame, kind: target.kind, number: target.number}
            }
        }
    }
    return answer
}
