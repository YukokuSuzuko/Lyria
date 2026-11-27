"""Frozen pre-optimization boundary reference for equivalence regression."""
import math
from lyricsync.audit_service import _cancelled,_recognized,make_ocr,ocr_similarity,box_overlap,neighbor_relation
def refine_cues(video, visuals, clock, region, settings, *, cancel=lambda: False,
                progress=lambda p, m: None, provider=None, track='chinese'):
    """OCR each decoded frame in candidate-boundary neighborhoods.

    Empty OCR is *absence evidence*, not proof: all uncertainty stays attached.
    No interpolation, whole-track one-frame shift, or average-FPS conversion.
    """
    # Explicit per-track language wins; do not let a coarse OCR hypothesis
    # select the recognition language or bias its own boundary recheck.
    provider = provider or make_ocr(settings)
    sample_fps = float(settings.get('sample_fps', 3))
    if not math.isfinite(sample_fps) or not .5 <= sample_fps <= 15:
        raise ValueError('粗扫采样率无效')
    radius = max(2 / sample_fps, .2)
    observations = {}
    def neighborhood(center):
        left, right = max(0, center-radius), min(video.info['duration'], center+radius)
        rows = []
        iterator = video.frames(left, right, cancel=cancel)
        try:
            for stamp, frame in iterator:
                _cancelled(cancel)
                number = clock.frame(stamp)
                if number not in observations:
                    observations[number] = {**clock.evidence(number),
                                            'ocr': _recognized(provider, frame, region['box'])}
                rows.append(observations[number])
        finally:
            if hasattr(iterator, 'close'):
                iterator.close()
        return rows
    cues = []
    for i, visual in enumerate(visuals):
        _cancelled(cancel)
        notes = list(visual.warnings)
        boundary, raw_rows = {}, {}
        chosen_first, chosen_last = None, None
        for side, center in (('start', visual.start), ('end', visual.end)):
            rows = neighborhood(center)
            for row in rows:
                raw_rows[row['frame']] = row
            def matches(row):
                return [(d, ocr_similarity(d['text'], visual.text)) for d in row['ocr']
                        if ocr_similarity(d['text'], visual.text) >= .7 and box_overlap(d['box'],visual.box)>=.3]
            visible = [j for j, row in enumerate(rows) if matches(row)]
            if not visible:
                boundary[side] = {'status': 'unresolved', 'observations': rows}
                notes.append(side+' 边界精扫未确认文字；保留粗扫候选，不宣称精确')
                continue
            anchor = min(visible, key=lambda j: abs(rows[j]['seconds']-center))
            direction = -1 if side == 'start' else 1
            edge = anchor
            while 0 <= edge+direction < len(rows) and matches(rows[edge+direction]):
                edge += direction
            visible_row = rows[edge]
            neighbor = edge + direction
            adjacent = rows[neighbor] if 0 <= neighbor < len(rows) else None
            adjacent_ok = adjacent is not None and abs(adjacent['frame']-visible_row['frame']) == 1
            clip_last=side=='end' and visible_row['frame']==len(clock.stamps)
            limit_ok = side == 'start' and visible_row['frame'] == 1 or clip_last and clock.end is not None
            confidence = max(d['confidence'] for d, score in matches(visible_row))
            status = 'adjacent_decoded_frames' if (adjacent_ok or limit_ok) and confidence >= .8 else 'needs_review'
            if adjacent and any(d['confidence'] < .8 for d in adjacent['ocr']):
                status = 'needs_review'
            if not adjacent_ok and not limit_ok and not clip_last:
                notes.append(side+' 字幕持续到精扫窗口外；边界尚未确定')
            if confidence < .8:
                notes.append(side+' 进出点附近识别置信度偏低（可能有渐变或遮挡）')
            boundary[side] = {'status': status, 'visible': visible_row,
                              'adjacent_nonmatching': adjacent,
                              'clip_edge': bool(limit_ok or clip_last),'clip_endpoint_known':not clip_last or clock.end is not None, 'confidence': confidence,
                              'rule': 'local OCR match >=0.7; actual adjacent decoded frame; absence is not proof'}
            if side == 'start':
                chosen_first = visible_row['frame'] if adjacent_ok or limit_ok else None
            else:
                chosen_last = visible_row['frame'] if adjacent_ok or limit_ok or clip_last else None
        precise = chosen_first is not None and chosen_last is not None and chosen_first <= chosen_last
        start = clock.stamps[chosen_first-1] if chosen_first is not None else visual.start
        end = (clock.stamps[chosen_last] if chosen_last < len(clock.stamps) else clock.end) if chosen_last is not None else visual.end
        if end is None:
            notes.append('最后一帧缺少可验证 duration；结束 PTS 未知')
        if not precise:
            notes.append('起止帧未闭合；时间仅为候选，须看证据帧核对')
        status = 'adjacent_decoded_frames' if precise and all(boundary[s]['status'] == 'adjacent_decoded_frames' for s in ('start', 'end')) else 'needs_review'
        raw_conf = [d['confidence'] for row in raw_rows.values() for d in row['ocr'] if ocr_similarity(d['text'], visual.text) >= .7]
        confidence = min([visual.confidence] + raw_conf) if raw_conf else visual.confidence
        representative=clock.evidence(chosen_first) if chosen_first is not None else min(raw_rows.values(),key=lambda row:abs(row['seconds']-visual.start)) if raw_rows else None
        cue = {'id': track+'-'+visual.id, 'track': track, 'text': visual.text,
               'start': start, 'end': end, 'first_frame': chosen_first, 'last_frame': chosen_last,
               'confidence': confidence, 'raw_confidences': raw_conf,
               'ocr_text_variants':sorted({d['text'] for row in raw_rows.values() for d in row['ocr'] if ocr_similarity(d['text'],visual.text)>=.7 and box_overlap(d['box'],visual.box)>=.3}),
               'evidence': '本地选区 OCR 与进出点逐帧识别',
               'evidence_kind': 'local_ocr', 'boundary_status': status,
               'content_status':'ocr_completeness_unverified',
               'boundary_evidence': boundary, 'raw_observations': sorted(raw_rows.values(), key=lambda r:r['frame']),
               'coarse_estimate': {'start': visual.start, 'end': visual.end, 'sample_fps': sample_fps},
               'warnings': list(dict.fromkeys(notes)), 'review_status': 'needs_review',
               'representative_frame':representative['frame'] if representative else None,
               'representative_seconds':representative['seconds'] if representative else None,
               'jump': {'frame': chosen_first if chosen_first is not None else representative['frame'] if representative else None,
                        'seconds': start if chosen_first is not None else representative['seconds'] if representative else None,
                        'kind':'boundary' if chosen_first is not None else 'sample_only','track': track}}
        cues.append(cue)
        progress((i+1)/max(1,len(visuals)), f'{track}：真实边界邻域核验 {i+1}/{len(visuals)}')
    cues.sort(key=lambda c: c['start'])
    for a, b in zip(cues, cues[1:]):
        b['previous_relation'] = neighbor_relation(a, b)
    return cues
