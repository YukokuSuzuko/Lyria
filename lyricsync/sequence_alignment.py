"""Evidence-only monotone alignment, split by unambiguous anchors.

No temporal interpolation. Every returned edge refers to immutable observations
and source occurrence IDs. A score is a text agreement, never acoustic accuracy.
"""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from statistics import median
from .model import normalized, valid_time


def eligible(visual):
    e = visual.evidence
    return (valid_time(visual.start, visual.end) and e.get('eligible_for_alignment', True)
            and e.get('observation_status') not in ('transition_noise', 'unresolved_multiline_block')
            and not (e.get('frame_count', 999) <= 2 and visual.confidence < .65))


def similarity(a, b):
    a, b = normalized(a), normalized(b)
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.


def _paragraph(item):
    return getattr(item, 'subtitle_metadata', {}).get('paragraph_index')


def _same_paragraph(items):
    return len({p for item in items if (p := _paragraph(item)) is not None}) <= 1


def _monotone_anchors(lyrics, visuals, offset):
    """Unique exact lines or unique contiguous 2/3-line contexts, never repeats alone."""
    candidates = []
    for width in (1, 2, 3):
        li, vi = defaultdict(list), defaultdict(list)
        for rows, index in ((lyrics, li), (visuals, vi)):
            for i in range(len(rows)-width+1):
                block = rows[i:i+width]
                key = tuple(normalized(x.text) for x in block)
                if min(map(len, key), default=0) >= (4 if width == 1 else 2):
                    index[key].append(i)
        for key in li.keys() & vi.keys():
            if len(li[key]) != 1 or len(vi[key]) != 1:
                continue
            i, j = li[key][0], vi[key][0]
            if not _same_paragraph(lyrics[i:i+width]):
                continue
            if any(visuals[j+k].confidence < .85 for k in range(width)):
                continue
            if any(valid_time(lyrics[i+k].start, lyrics[i+k].end) and
                   min(visuals[j+k].end, lyrics[i+k].end+offset) <= max(visuals[j+k].start, lyrics[i+k].start+offset)
                   for k in range(width)):
                continue
            candidates.append((i,j,width,sum(map(len,key))))
    # Weighted noncrossing chain. Ties remain deterministic by occurrence order.
    candidates.sort(); paths=[]
    for n,(i,j,w,weight) in enumerate(candidates):
        before=[k for k,(a,b,c,_) in enumerate(candidates[:n]) if a+c<=i and b+c<=j]
        k=max(before,key=lambda k:paths[k][0],default=None)
        paths.append((weight+(paths[k][0] if k is not None else 0),k))
    result=[];cursor=max(range(len(paths)),key=lambda k:paths[k][0],default=None)
    while cursor is not None:
        i,j,w,_=candidates[cursor]
        result.extend((i+k,j+k) for k in reversed(range(w)))
        cursor=paths[cursor][1]
    return list(reversed(result))


def _occurrence(text, visuals, lyrics, offset):
    """A timed repeat must have one strongest interval-supported instance."""
    ranks = [(similarity(text, l.text), l) for l in lyrics]
    best = max((x[0] for x in ranks), default=0)
    keys = {normalized(l.text) for score,l in ranks if abs(score-best)<1e-9}
    repeats = [l for l in lyrics if normalized(l.text) in keys]
    if best < .6 or len(repeats)<2 or not all(valid_time(l.start,l.end) for l in repeats):
        return None
    if len(keys)!=1:return set()
    ranked=[]
    for l in repeats:
        overlaps=[max(0,min(v.end,l.end+offset)-max(v.start,l.start+offset))/(v.end-v.start) for v in visuals]
        if not all(overlaps):continue
        ranked.append((.9*sum(overlaps)/len(overlaps)+.1*max(0,1-abs(visuals[0].start-l.start-offset)/20), l.id))
    ranked.sort(reverse=True)
    if not ranked or (len(ranked)>1 and abs(ranked[0][0]-ranked[1][0])<1e-6):return set()
    return {ranked[0][1]}


def _segment(lyrics, visuals, offset, band=12, universe=None):
    universe = lyrics if universe is None else universe
    n,m=len(lyrics),len(visuals)
    if not n:return [(0,0,0,m,0.,'skip_observation')] if m else []
    if not m:return [(0,0,n,0,0.,'skip_lyric')]
    # The band is relative to the two trusted boundaries, not global line count.
    width=max(band,abs(n-m)+3)
    scores={(0,0):(0.,None)}
    def put(i,j,score,back):
        if abs(j-i*m/max(1,n))>width and (i,j)!=(n,m):return
        if (i,j) not in scores or score>scores[i,j][0]+1e-9:scores[i,j]=(score,back)
    repeats=Counter(normalized(l.text) for l in lyrics)
    for i in range(n+1):
        for j in range(max(0,int(i*m/n-width)-1),min(m,int(i*m/n+width)+1)+1):
            if (i,j) not in scores:continue
            score=scores[i,j][0]
            if i<n:put(i+1,j,score-.43,(i,j,1,0,0.,'skip_lyric'))
            if j<m:put(i,j+1,score-.40,(i,j,0,1,0.,'skip_observation'))
            for a,b in ((1,1),(1,2),(2,1),(1,3),(3,1)):
                if i+a>n or j+b>m:continue
                ls,vs=lyrics[i:i+a],visuals[j:j+b]
                if not _same_paragraph(ls):continue
                if any(v.start>u.end+1.25 for u,v in zip(vs,vs[1:])):continue
                text=''.join(l.text for l in ls);observed=''.join(v.text for v in vs)
                agreement=similarity(text,observed)
                if any((allowed := _occurrence(v.text,[v],universe,offset)) is not None
                       and not set(l.id for l in ls).issubset(allowed) for v in vs):continue
                allowed=_occurrence(observed,vs,universe,offset)
                if allowed is not None and not set(l.id for l in ls).issubset(allowed):continue
                if a>1:
                    # A shared interval is justified only by one exact complete
                    # untimed source group, not fuzzy/ambiguous duplication.
                    if agreement<1 or any(l.start is not None or l.end is not None for l in ls):continue
                    occurrences=sum(normalized(''.join(l.text for l in universe[k:k+a]))==normalized(observed)
                                    for k in range(len(universe)-a+1))
                    if occurrences!=1:continue
                if b>1 and a==1 and allowed is None:
                    if sum(normalized(l.text)==normalized(text) for l in universe)>1:continue
                if agreement < (.62 if a==b==1 else .78):continue
                if a+b>2:
                    single=max(similarity(l.text,v.text) for l in ls for v in vs)
                    if agreement<single+.08:continue
                # Timed repetitions may match only their observed occurrence.
                if any(repeats[normalized(l.text)]>1 and valid_time(l.start,l.end) and
                       not any(min(v.end,l.end+offset)>max(v.start,l.start+offset) for v in vs) for l in ls):continue
                time_bonus=0.
                if all(valid_time(l.start,l.end) for l in ls):
                    distance=abs(vs[0].start-(ls[0].start+offset))
                    time_bonus=.15*max(0.,1-distance/10.)
                reward=2.2*agreement-.8-.13*(a+b-2)+time_bonus
                put(i+a,j+b,score+reward,(i,j,a,b,agreement,'match'))
    if (n,m) not in scores:
        return [(0,0,n,0,0.,'skip_lyric'),(n,0,0,m,0.,'skip_observation')]
    cursor=(n,m);result=[]
    while cursor!=(0,0):
        back=scores[cursor][1];result.append(back);cursor=back[:2]
    return list(reversed(result))


def _align_observations(lyrics, observations):
    visuals=sorted((v for v in observations if eligible(v)),key=lambda v:(v.start, v.box[1] if len(v.box)>1 else 0))
    offsets=[]
    counts=Counter(normalized(l.text) for l in lyrics)
    for v in visuals:
        exact=[l for l in lyrics if normalized(l.text)==normalized(v.text)]
        if len(exact)==1 and len(normalized(v.text))>=4 and exact[0].start is not None:
            offsets.append(v.start-exact[0].start)
    offset=median(offsets) if offsets else 0.
    if lyrics and all(valid_time(l.start,l.end) for l in lyrics):
        lyrics=sorted(lyrics,key=lambda l:(l.start,l.end))
    direct=[]; remaining=[]
    for v in visuals:
        allowed=_occurrence(v.text,[v],lyrics,offset)
        if allowed and max((similarity(v.text,l.text) for l in lyrics if l.id in allowed),default=0)>=.9:
            direct.append(dict(lyric_ids=list(allowed),visual_ids=[v.id],score=.9,state='uncertain',anchor=False))
        else:remaining.append(v)
    visuals=remaining
    universe=lyrics
    # Keep full occurrence constraints for disambiguating repeats, but do not
    # offer directly consumed occurrences to the local alignment a second time.
    assigned={lid for edge in direct for lid in edge['lyric_ids']}
    lyrics=[l for l in lyrics if l.id not in assigned]
    anchors=_monotone_anchors(lyrics,visuals,offset)
    edges=direct;left=right=0
    for i,j in anchors+[(len(lyrics),len(visuals))]:
        for a,b,na,nb,score,kind in _segment(lyrics[left:i],visuals[right:j],offset,universe=universe):
            edges.append(dict(lyric_ids=[l.id for l in lyrics[left+a:left+a+na]],
                visual_ids=[v.id for v in visuals[right+b:right+b+nb]],score=score,
                state='uncertain' if kind=='match' else kind,anchor=False))
        if i<len(lyrics):
            edges.append(dict(lyric_ids=[lyrics[i].id],visual_ids=[visuals[j].id],score=1.,state='auto_high',anchor=True))
        left,right=i+1,j+1
    # Repeated text does not establish an instance on its own; monotonic order
    # and context constrain it, and uncertainty is still presented to the user.
    repeated={l.id for l in universe if counts[normalized(l.text)]>1}
    for edge in edges:
        if not edge['anchor'] and repeated.intersection(edge['lyric_ids']):
            edge['score']=min(.59,edge['score']);edge['repeated_instance']=True
    excluded=[v.id for v in observations if not eligible(v)]
    edges.extend(dict(lyric_ids=[],visual_ids=[i],score=0.,state='excluded_observation',anchor=False) for i in excluded)
    return edges


def associations_from_edges(lyrics, visuals, edges):
    by_id={v.id:v for v in visuals};associations={l.id:[] for l in lyrics};unknown=[]
    for edge in edges:
        lids,vids=edge['lyric_ids'],edge['visual_ids']
        if not lids or not vids:
            unknown.extend(vids);continue
        notes=['OCR 与原词采用锚点分区、带状动态规划；保留原始画面入出点']
        if edge['anchor']:notes.append('唯一文字或连续上下文锚点；仍需人工核对')
        else:notes.append('局部配对待确认，文字分数不等于时间准确率')
        if edge.get('repeated_instance'):notes.append('重复副歌按实例顺序约束，缺少独立锚点仍存疑')
        if len(lids)>1:notes.append('\u5171\u4eab\u6574\u53e5\u89c6\u89c9\u533a\u95f4\uff1b\u4e0d\u4f2a\u9020\u5206\u53e5\u7684\u72ec\u7acb\u8d77\u6b62\u65f6\u95f4')
        if len(vids)>1:notes.append('一句原词对应多个连续画面事件；保留各事件独立时间')
        for lid in lids:
            for vid in vids:
                v=by_id[vid];associations[lid].append((v,min(edge['score'],v.confidence),notes+list(v.warnings)))
    return associations,unknown


def align_observations(lyrics, observations, manual_edges=()):
    """Manual associations partition proposals. Crossed barriers stop automation."""
    if not manual_edges:return _align_observations(lyrics,observations)
    visuals=sorted(observations,key=lambda v:(v.start if v.start is not None else float('inf'),v.id))
    lp={l.id:i for i,l in enumerate(lyrics)};vp={v.id:i for i,v in enumerate(visuals)}
    blocks=[];fixed=[]
    for edge in manual_edges:
        li=sorted(lp[i] for i in edge['lyric_ids'] if i in lp)
        vi=sorted(vp[i] for i in edge['visual_ids'] if i in vp)
        if not li or not vi:continue
        fixed.append(dict(lyric_ids=[lyrics[i].id for i in li],visual_ids=[visuals[i].id for i in vi],score=1.,state='manual_locked',anchor=True))
        blocks.append((li[0],li[-1]+1,vi[0],vi[-1]+1))
    if not blocks:return _align_observations(lyrics,observations)
    blocks.sort();left=right=0;result=[]
    for a,b,c,d in blocks:
        if a<left or c<right:
            used={v for edge in fixed for v in edge['visual_ids']}
            return fixed+[dict(lyric_ids=[],visual_ids=[v.id],score=0.,state='manual_anchor_conflict',anchor=False) for v in visuals if v.id not in used]
        left,right=b,d
    left=right=0
    for a,b,c,d in blocks:
        result.extend(_align_observations(lyrics[left:a],visuals[right:c]));left,right=b,d
    result.extend(_align_observations(lyrics[left:],visuals[right:]));return result+fixed
