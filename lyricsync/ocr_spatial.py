"""Frame-local spatial line assembly, independent of reference lyrics.

Raw components remain evidence. A vertical slot is never made from string
newlines alone; separated Y bands keep independent lifetimes in Tracker.
"""
from copy import deepcopy
from dataclasses import replace
from statistics import median
from .model import Detection, normalized

SPATIAL_VERSION = 'spatial-lines-v1'


def channels(detection):
    return list(detection.evidence.get('source_channels', []))


def component(detection):
    """A component plus flat original-reading provenance, never nested copies.

    Assembly may combine already deduplicated/spatially split detections. Keep
    every original text/box/channel and split marker across those stages. Walk
    existing provenance iteratively with identity guards, then emit flat leaves:
    a repeated assembly cannot grow recursive evidence chains or follow cycles.
    """
    keys=('text','box','confidence','source_channels','spatial_split','line_index',
          'observation_status','eligible_for_alignment')
    def leaf(record):
        return {key:deepcopy(record[key]) for key in keys if key in record}
    current={'text':detection.text,'box':list(detection.box),
             'confidence':detection.confidence,'source_channels':channels(detection)}
    for key in keys[4:]:
        if key in detection.evidence:current[key]=deepcopy(detection.evidence[key])
    pending=[detection.evidence];visited=set();records=[]
    while pending:
        node=pending.pop()
        if not isinstance(node,(dict,list,tuple)) or id(node) in visited:continue
        visited.add(id(node))
        if isinstance(node,(list,tuple)):
            pending.extend(reversed(node));continue
        if 'text' in node and 'box' in node:
            item=leaf(node)
            if item!=current and item not in records:records.append(item)
        for key in ('alternatives','components','block_original','provenance','evidence'):
            if key in node:pending.append(node[key])
    if records:current['provenance']=records
    return current


def tagged(detections, channel):
    result=[]
    for d in detections:
        evidence=deepcopy(d.evidence)
        evidence['source_channels']=list(dict.fromkeys(channels(d)+[channel]))
        result.append(replace(d,box=list(d.box),evidence=evidence))
    return result


def vertical_overlap(a,b):
    return max(0,min(a[3],b[3])-max(a[1],b[1]))/max(1,min(a[3]-a[1],b[3]-b[1]))


def same_slot(a,b):
    ha,hb=max(1,a[3]-a[1]),max(1,b[3]-b[1])
    return vertical_overlap(a,b)>=.65 and abs((a[1]+a[3]-b[1]-b[3])/2)<=.45*max(ha,hb)


def contained_text(whole, envelope, part, box, minimum_overlap):
    """A proper text fragment inside the same physical subtitle line."""
    outer, inner = normalized(whole), normalized(part)
    if not inner or len(inner) >= len(outer) or inner not in outer or not same_slot(envelope, box):
        return False
    width = box[2] - box[0]
    overlap = max(0, min(envelope[2], box[2]) - max(envelope[0], box[0]))
    return 0 < width < envelope[2] - envelope[0] and overlap >= minimum_overlap * width


def duplicate_region(a,b):
    """Near-coincident boxes, not just text equality or touching characters."""
    if not same_slot(a,b):return False
    overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))
    wa,wb=max(1,a[2]-a[0]),max(1,b[2]-b[0])
    return overlap/min(wa,wb)>=.72


def deduplicate(detections):
    result=[]
    # Wide boxes take precedence over subword detections of the same region.
    for item in sorted(detections,key=lambda d:((d.box[2]-d.box[0]),d.confidence),reverse=True):
        other=next((d for d in result if duplicate_region(d.box,item.box)),None)
        if other is None:
            result.append(replace(item,box=list(item.box),evidence=deepcopy(item.evidence)))
            continue
        original=component(other)
        alternative=component(item)
        evidence=deepcopy(other.evidence)
        evidence['alternatives']=list(evidence.get('alternatives',[]))+[alternative]
        evidence['source_channels']=list(dict.fromkeys(channels(other)+channels(item)))
        if (abs((other.box[2]-other.box[0])-(item.box[2]-item.box[0]))<=.2*(other.box[2]-other.box[0])
                and item.confidence>other.confidence):
            evidence['alternatives'].append(original)
            selected=replace(item,box=list(item.box),evidence=evidence)
        else:selected=replace(other,evidence=evidence)
        result[result.index(other)]=selected
    return sorted(result,key=lambda d:(d.box[1],d.box[0]))


def _split_multiline_box(d,image):
    """Require real horizontal whitespace bands, otherwise abstain.

    This handles providers returning a block with actual newlines, but does not
    assign equal-height fake boxes. Each resulting crop goes to its own track.
    """
    lines=[text for text in d.text.splitlines() if text.strip()]
    if len(lines)<=1:return [d]
    import cv2
    import numpy as np
    x1,y1,x2,y2=map(int,d.box);crop=image[max(0,y1):y2,max(0,x1):x2]
    if crop.size:
        gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
        edges=cv2.Canny(gray,80,180)
        active=(edges>0).sum(axis=1)>=max(3,crop.shape[1]*.015)
        # Close only sub-glyph holes, not the whitespace between text lines.
        for i in range(1,len(active)-1):
            if active[i-1] and active[i+1]:active[i]=True
        bands=[];start=None
        for i,on in enumerate(list(active)+[False]):
            if on and start is None:start=i
            elif not on and start is not None:
                if i-start>=3:bands.append((start,i))
                start=None
        if len(bands)==len(lines) and all(b-a>=max(4,crop.shape[0]/(len(lines)*4)) for a,b in bands):
            result=[]
            for number,(text,(a,b)) in enumerate(zip(lines,bands)):
                evidence=deepcopy(d.evidence)
                evidence.update(spatial_split='observed_horizontal_edge_bands',block_original=component(d),line_index=number)
                result.append(replace(d,text=text,box=[x1,y1+a,x2,y1+b],evidence=evidence))
            return result
    evidence=deepcopy(d.evidence)
    evidence.update(observation_status='unresolved_multiline_block',eligible_for_alignment=False)
    return [replace(d,evidence=evidence)]


def spatial_lines(detections,image,recognize_line=None):
    expanded=[part for d in detections for part in _split_multiline_box(d,image)]
    clean=deduplicate(expanded)
    slots=[]
    for d in sorted(clean,key=lambda d:((d.box[1]+d.box[3])/2,d.box[0])):
        if d.evidence.get('observation_status')=='unresolved_multiline_block':
            slots.append([d]);continue
        group=next((g for g in slots if all(same_slot(d.box,p.box) for p in g)
                    and all(p.evidence.get('observation_status')!='unresolved_multiline_block' for p in g)),None)
        if group is None:slots.append([d])
        else:group.append(d)
    result=[]
    for group in slots:
        group.sort(key=lambda d:d.box[0]);runs=[]
        for d in group:
            if not runs:runs.append([d]);continue
            last=runs[-1][-1]
            # Widely separated same-height captions remain separate regions.
            maximum_gap=2.5*median([p.box[3]-p.box[1] for p in runs[-1]+[d]])
            if d.box[0]-last.box[2]>maximum_gap:runs.append([d])
            else:runs[-1].append(d)
        for run in runs:
            box=[min(d.box[0] for d in run),min(d.box[1] for d in run),max(d.box[2] for d in run),max(d.box[3] for d in run)]
            evidence=deepcopy(run[0].evidence) if len(run)==1 else {}
            evidence.update(line_slot=box[1::2],spatial_version=SPATIAL_VERSION,
                            components=[component(d) for d in run],
                            source_channels=list(dict.fromkeys(c for d in run for c in channels(d))))
            text=''.join(d.text for d in run);confidence=min(d.confidence for d in run)
            if len(run)>1:
                evidence['assembly']='same_frame_same_vertical_slot'
                # Prefer direct recognition of the whole observed spatial line.
                # Keep the component concatenation too; no reference text used.
                if recognize_line is not None:
                    recognized=recognize_line(box)
                    if recognized and normalized(recognized[0]) and recognized[1]>=.55:
                        evidence['component_text']=text
                        text,confidence=recognized
                        evidence['assembly']='whole_spatial_line_rerecognition'
            result.append(Detection(text,box,confidence,evidence))
    return sorted(result,key=lambda d:(d.box[1],d.box[0]))


def classify_events(visuals, decoded_stamps, duration):
    """Keep suspicious fragments as evidence, not automatic lyric landing spots.

    Only a low-confidence partial reading adjacent to a stable same-slot event
    is called transition noise. Isolated uncertain flashes are not called noise.
    High-confidence real one-frame events remain eligible and are flagged.
    """
    from bisect import bisect_left
    from .model import similarity
    if not visuals:
        return visuals
    if not decoded_stamps:
        raise ValueError('OCR 帧时间证据为空，不能把未知长度当作过渡碎片。')
    for v in visuals:
        first=bisect_left(decoded_stamps,v.start-1e-6)
        end=bisect_left(decoded_stamps,v.end-1e-6)
        count=max(0,end-first)
        v.evidence.update(frame_count=count,first_frame_in_scan=first+1,
                          end_frame_in_scan_exclusive=end+1,interval='half-open-pts')
    active = []
    for item in sorted(visuals, key=lambda v: (v.start, -v.end)):
        active = [prior for prior in active if prior.end > item.start]
        for prior in active:
            for child, parent in ((item, prior), (prior, item)):
                duration = child.end - child.start
                overlap = min(child.end, parent.end) - max(child.start, parent.start)
                if (child.evidence.get('roi_owner') == parent.evidence.get('roi_owner')
                        and 0 < duration <= parent.end-parent.start and overlap >= .8*duration
                        and parent.evidence.get('eligible_for_alignment', True)
                        and contained_text(parent.text, parent.box, child.text, child.box, .80)):
                    child.evidence.update(observation_status='subline_fragment', eligible_for_alignment=False,
                                          whole_line_id=parent.id)
        active.append(item)
    for v in visuals:
        count=v.evidence['frame_count']
        status=v.evidence.get('observation_status','normal')
        eligible=v.evidence.get('eligible_for_alignment',True)
        confidence=v.evidence.get('recognition_confidence',v.confidence)
        if count<=2 and confidence<.85 and status not in ('unresolved_multiline_block','subline_fragment'):
            neighbors=[]
            for other in visuals:
                if other is v or other.evidence['frame_count']<5 or not same_slot(v.box,other.box):continue
                overlap=max(0,min(v.box[2],other.box[2])-max(v.box[0],other.box[0]))
                gap=min(abs(v.end-other.start),abs(other.end-v.start))
                partial=normalized(v.text) in normalized(other.text) or similarity(v.text,other.text)>=.45
                if overlap>0 and gap<=max(.08,2*(v.end-v.start)) and partial:neighbors.append(other.id)
            status='transition_noise' if neighbors else 'short_uncertain'
            eligible=False
            v.evidence['neighbor_stable_event_ids']=neighbors
            v.warnings.append('低置信极短片段，仅保留证据，未自动分配歌词；请核对是否为真实闪现字幕')
        elif count<5:
            v.warnings.append('极短画面片段，请人工核对，不按字数延长')
        v.evidence.update(observation_status=status,eligible_for_alignment=eligible)
    return visuals
