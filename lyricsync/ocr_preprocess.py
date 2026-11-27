"""Non-destructive subtitle input and conservative raw-image recovery.

Minimum-channel grayscale suppresses colored glows around white/light subtitles.
It is not thresholding and is not suitable for every colored font. Raw mode is
always available; uncertain/empty enhanced results get one original-image pass.
"""
from dataclasses import replace
import unicodedata

PREPROCESS_VERSION = "subtitle-min-channel-v6-local-glyph-retry"


def text_key(text):
    # Preserve punctuation and Japanese small kana; never use reference lyrics.
    return "".join(unicodedata.normalize("NFKC", text).split())


def subtitle_image(image):
    import cv2
    import numpy as np
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or not image.size:
        raise ValueError("OCR 图像必须是非空的 uint8 BGR 图像")
    return cv2.cvtColor(image.min(axis=2), cv2.COLOR_GRAY2BGR)


def needs_raw_recovery(detections):
    return not detections or any(d.confidence < .90 for d in detections)


def recover_clipped_subtitle(image, lines, read):
    """Recheck a wide low-confidence edge-joined box on the same frame."""
    from .model import normalized
    from .ocr_spatial import same_slot
    from .vision import box_overlap
    height, width = image.shape[:2]
    if width < 4*height:
        return lines
    def verification_box(line):
        if line.confidence >= .8:
            return None
        box = line.box
        if (box[2]-box[0] > 3*height and box[1] <= 1 and box[3] >= height-2
                and (box[0] <= 1 or box[2] >= width-2)):
            return box
        # Min-channel and raw detections can disagree on the extent of a line
        # when an illustration touches both vertical edges of a subtitle band.
        from .ocr_spatial import component
        evidence = component(line)
        boxes = [part['box'] for part in (evidence, *evidence.get('provenance', []))
                 if part.get('box') and same_slot(part['box'], box)]
        clipped = [b for b in boxes if b[1] <= height*.05 and b[3] >= height*.94
                   and b[2]-b[0] >= height*1.6 and box_overlap(b, box) >= .8]
        return max(clipped, key=lambda b: b[2]-b[0], default=None)

    affected = [(i, envelope) for i, line in enumerate(lines)
                if (envelope := verification_box(line)) is not None]
    if not affected:
        return lines
    x, right = width//4, round(width*.75)
    local = read(image[:,x:right])
    results = list(lines)
    for i, envelope in affected:
        old = lines[i]
        supported = []
        for d in local:
            if (d.confidence < max(.88, old.confidence+.10) or not normalized(d.text)
                    or normalized(d.text).isdecimal() or d.box[0]<=2 or d.box[2]>=right-x-2):
                continue
            box = [d.box[0]+x,d.box[1],d.box[2]+x,d.box[3]]
            if same_slot(box,old.box) and box_overlap(box,envelope) >= .8:
                supported.append((d,box))
        if len(supported)==1:
            d,box=supported[0]
            evidence=dict(d.evidence, local_verification_box=[x,0,right,height],
                          original_edge_reading={'text':old.text,'box':old.box,'confidence':old.confidence})
            # Component boxes in observations are relative to the original crop.
            from copy import deepcopy
            components=deepcopy(evidence.get('components',[]))
            for component in components:
                for part in (component,*component.get('provenance',[])):
                    if 'box' in part:
                        a,b,c,e=part['box'];part['box']=[a+x,b,c+x,e]
            evidence['components']=components
            results[i]=replace(d,box=box,evidence=evidence)
    return results



def recover_empty_wide_subtitle(image, read):
    """One bounded center retry for an empty, very wide subtitle strip.

    Do not split a successful outer detection, or promote a clipped fragment or
    incidental number. The full-width pass remains the source for side lanes.
    Coordinates of both the reading and its evidence are local to the input ROI.
    """
    from copy import deepcopy
    from .model import normalized
    from .ocr_spatial import deduplicate
    height, width = image.shape[:2]
    if width < 6*height:
        return []
    shift, right = round(.29*width), round(.71*width)
    window = [shift, 0, right, height]
    result = []
    for d in read(image[:,shift:right]):
        x1,y1,x2,y2 = d.box
        key = normalized(d.text)
        if (d.confidence < .88 or len(key)<2 or key.isdecimal()
                or not (2 < x1 < x2 < right-shift-2) or y2-y1 < .18*height
                or d.evidence.get('eligible_for_alignment') is False):
            continue
        evidence = deepcopy(d.evidence)
        visited = set()
        def restore(node):
            if not isinstance(node, (dict,list)) or id(node) in visited:
                return
            visited.add(id(node))
            if isinstance(node, list):
                for value in node: restore(value)
            else:
                box = node.get('box')
                if isinstance(box,(list,tuple)) and len(box)==4:
                    node['box'] = [box[0]+shift,box[1],box[2]+shift,box[3]]
                for value in node.values(): restore(value)
        restore(evidence)
        evidence['wide_empty_recovery_box'] = window
        result.append(replace(d, box=[x1+shift,y1,x2+shift,y2], evidence=evidence))
    return deduplicate(result)


def recover_from_raw(enhanced, original):
    from .vision import box_overlap, ocr_similarity
    from .ocr_spatial import component, channels, duplicate_region
    result = [replace(d, box=list(d.box)) for d in enhanced]
    baselines = [d.confidence for d in enhanced]
    for raw in original:
        matches = [(box_overlap(d.box, raw.box), i) for i, d in enumerate(result)]
        overlap, index = max(matches, default=(0, -1))
        if overlap <= .7 and not (index >= 0 and duplicate_region(result[index].box, raw.box)):
            # Original-only text may be colored text erased by min-channel, or
            # a background false positive. Preserve it as uncertain, not truth.
            baselines.append(raw.confidence)
            result.append(replace(raw, box=list(raw.box), confidence=min(raw.confidence, .7)))
            continue
        current = result[index]
        evidence = dict(current.evidence)
        evidence['alternatives'] = list(evidence.get('alternatives', [])) + [component(current), component(raw)]
        evidence['source_channels'] = list(dict.fromkeys(channels(current) + channels(raw)))
        current = replace(current, evidence=evidence)
        if raw.box[2] - raw.box[0] < .75 * (current.box[2] - current.box[0]):
            result[index] = current
            continue
        if text_key(raw.text) == text_key(current.text):
            result[index] = replace(current, confidence=max(current.confidence, raw.confidence))
        elif ocr_similarity(raw.text, current.text) >= .55 and raw.confidence > max(current.confidence, baselines[index]) + .05:
            result[index] = replace(raw, box=list(raw.box), confidence=min(raw.confidence, .7), evidence=evidence)
        else:
            result[index] = replace(current, confidence=min(current.confidence, .7))
    return result
