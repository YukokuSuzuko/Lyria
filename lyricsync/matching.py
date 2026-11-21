"""Evidence-bearing mappings. Cross-language order is a suggestion, not semantics."""
from __future__ import annotations

from statistics import median
from collections import Counter, defaultdict
import uuid

from .model import Mapping, normalized, stable_id, valid_time
from .vision import ocr_similarity


def associate_visuals(lyrics, visuals, manual_edges=()):
    from .sequence_alignment import align_observations, associations_from_edges
    return associations_from_edges(lyrics, visuals, align_observations(lyrics, visuals, manual_edges))


def _legacy_associate_visuals(lyrics, visuals):
    # Invalid raw evidence remains in the project for diagnosis, but cannot
    # supply a zero/negative/NaN duration to any proposed timeline row.
    invalid_ids = [v.id for v in visuals if not valid_time(v.start, v.end)]
    visuals = [v for v in visuals if valid_time(v.start, v.end)]
    # Repeated source lines are separate occurrences, not interchangeable text.
    # Only use interval evidence when every occurrence has valid timing.
    repeated = {}
    for lyric in lyrics:
        repeated.setdefault(normalized(lyric.text), []).append(lyric)
    timed_repeats = {key for key, group in repeated.items() if key and len(group) > 1
                     and all(valid_time(item.start, item.end) for item in group)}
    offsets = []
    for v in visuals:
        ranked = sorted(((ocr_similarity(v.text, l.text), l) for l in lyrics),
                        key=lambda x:x[0], reverse=True)
        complete = (bool(ranked) and min(len(normalized(v.text)), len(normalized(ranked[0][1].text)))
                    / max(1, len(normalized(v.text)), len(normalized(ranked[0][1].text))) >= .9)
        if complete and ranked[0][1].start is not None and ranked[0][0] > .88 and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>.12):
            offsets.append(v.start-ranked[0][1].start)
    offset = median(offsets) if offsets else 0.0
    associations = {l.id:[] for l in lyrics}
    unknown = list(invalid_ids)
    joined = _joined_visual_evidence(lyrics, visuals, offset)
    for visual in visuals:
        if visual.id in joined:
            lyric = joined[visual.id]
            notes = list(visual.warnings) + ['多个 OCR 片段合并后与唯一原文实例精确匹配，保留各片段实际画面区间']
            associations[lyric.id].append((visual, min(.9, visual.confidence), notes))
            continue
        # A complete OCR line may correspond to consecutive imported half-lines.
        # Share evidence only for one exact normalized occurrence, never a fuzzy
        # concatenation or a guessed placement among repeated choruses.
        key = normalized(visual.text)
        exact = [group for group in _groups(lyrics) if key and
                 normalized(''.join(item.text for item in group)) == key]
        if len(exact) == 1 and len(exact[0]) > 1 and all(
                item.start is None and item.end is None for item in exact[0]):
            notes = list(visual.warnings) + ['整句画面文字对应连续多行原文，共享整句视觉区间，不代表各半句的独立起止时间']
            for item in exact[0]:
                associations[item.id].append((visual, min(.9, visual.confidence), notes))
            continue
        text_scores = [ocr_similarity(visual.text, lyric.text) for lyric in lyrics]
        best_text = max(text_scores, default=0)
        best_keys = {normalized(lyric.text) for lyric, score in zip(lyrics, text_scores)
                     if abs(score - best_text) < 1e-9}
        repeat_key = None
        if best_text >= .6 and best_keys.intersection(timed_repeats):
            # The strongest textual evidence names a repeated source group.
            # Timing rejection must not silently downgrade to a different text.
            if len(best_keys) != 1:
                unknown.append(visual.id)
                continue
            repeat_key = next(iter(best_keys))
        ranked=[]
        for index,lyric in enumerate(lyrics):
            text_score=text_scores[index]
            if repeat_key is not None and normalized(lyric.text) != repeat_key:
                continue
            if text_score<.6:
                continue
            gap=abs(visual.start-(lyric.start+offset)) if lyric.start is not None else None
            temporal=max(0,1-gap/20) if gap is not None else max(0,1-abs(index/max(1,len(lyrics)-1)-visuals.index(visual)/max(1,len(visuals)-1)))
            if normalized(lyric.text) in timed_repeats:
                overlap = max(0, min(visual.end, lyric.end + offset)
                              - max(visual.start, lyric.start + offset))
                # A nearest start can be the *next* occurrence when a source
                # interval starts early. No overlap means insufficient evidence,
                # not permission to stretch a later mapping across a blank gap.
                if overlap <= 0:
                    continue
                # Overlap dominates; distance disambiguates nested intervals.
                # Equal overlap and equal distance remain unknown below.
                temporal = .9 * overlap / max(.001, visual.end - visual.start) + .1 * temporal
            ranked.append((.88*text_score+.12*temporal,text_score,gap,index,lyric))
        ranked.sort(key=lambda x:x[0],reverse=True)
        if not ranked:
            unknown.append(visual.id)
            continue
        if repeat_key is not None and len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 1e-6:
            unknown.append(visual.id)
            continue
        score,text_score,gap,index,lyric=ranked[0]
        reasons=[]
        if text_score<.86:
            reasons.append('OCR 识别文字与参考原文仅部分匹配，请核对')
        if len(ranked)>1 and score-ranked[1][0]<.045:
            reasons.append('存在重复歌词或相近片段，请确认先后顺序')
        if gap is not None and gap>20:
            reasons.append('画面出现时间与参考原文时间相差较大，请核对是否配错句')
        if visual.warnings:
            reasons.extend(visual.warnings)
        associations[lyric.id].append((visual, min(score,visual.confidence),reasons))
    return associations,unknown


def _groups(sequence, maximum=3):
    for index in range(len(sequence)):
        for size in range(1,min(maximum,len(sequence)-index)+1):
            yield sequence[index:index+size]


def _joined_visual_evidence(lyrics, visuals, offset):
    """Recover a short fragment only with complete, unique adjacent evidence."""
    sources = defaultdict(list)
    for lyric in lyrics:
        sources[normalized(lyric.text)].append(lyric)
    observations = defaultdict(list)
    for group in _groups(visuals):
        key = normalized(''.join(item.text for item in group))
        observations[key].append(group)
    candidates = []
    for key, occurrences in observations.items():
        references = sources.get(key, [])
        if len(key) < 4 or not references:
            continue
        timed = all(valid_time(lyric.start, lyric.end) for lyric in references)
        for group in occurrences:
            if len(group) < 2 or not all(valid_time(item.start, item.end) for item in group):
                continue
            if any(right.start < left.start or right.start-left.end > 1.
                   for left, right in zip(group, group[1:])):
                continue
            if timed:
                supported = [lyric for lyric in references if all(
                    min(item.end, lyric.end+offset) > max(item.start, lyric.start+offset)
                    for item in group)]
            else:
                supported = references if (len(occurrences) == len(references) == 1
                    and references[0].start is None and references[0].end is None) else []
            if len(supported) == 1:
                candidates.append((group, supported[0]))
    uses = Counter(item.id for group, _ in candidates for item in group)
    return {item.id: lyric for group, lyric in candidates
            if all(uses[item.id] == 1 for item in group) for item in group}


def _reference_mapping(translation, lyrics, index, count):
    candidates=[]
    for group in _groups(lyrics):
        content=" ".join(l.text for l in group)
        text_score=ocr_similarity(translation.reference,content)
        if text_score<.62:
            continue
        position=lyrics.index(group[0])/max(1,len(lyrics)-1)
        if translation.start is not None and group[0].start is not None:
            temporal=max(0,1-abs(translation.start-group[0].start)/12)
        else:
            temporal=max(0,1-abs(position-index/max(1,count-1)))
        score=.94*text_score+.06*temporal-.025*(len(group)-1)
        candidates.append((score,group))
    candidates.sort(key=lambda r:r[0],reverse=True)
    if not candidates:
        return [],0,['双语对照原文未能匹配到参考原文']
    score,group=candidates[0]
    reasons=[]
    if score<.85:
        reasons.append('双语对照原文匹配度偏低，请核对')
    if len(candidates)>1 and score-candidates[1][0]<.025:
        reasons.append('歌词重复出现，请确认对应段落顺序')
    return group,score,reasons


def _exact_text_groups(lyrics, translations):
    """Return noncrossing, unique textual anchors without interpreting languages.

    A supplied bilingual reference wins over display text. Exact concatenations
    support local splits/merges, including unequal split positions. Short tokens
    and repeated groups cannot establish an occurrence by themselves. No visual
    interval, imported text, or user decision is changed here.
    """
    def index_groups(items, text):
        indexed = defaultdict(list)
        for i in range(len(items)):
            for count in range(1, min(3, len(items) - i) + 1):
                key = normalized(''.join(text(item) for item in items[i:i+count]))
                if len(key) >= 4:
                    indexed[key].append((i, count))
        return indexed

    source = index_groups(lyrics, lambda item: item.text)
    target = index_groups(translations, lambda item: item.reference or item.text)
    candidates = []
    for key in source.keys() & target.keys():
        if len(source[key]) != 1 or len(target[key]) != 1:
            continue
        i, a = source[key][0]
        j, b = target[key][0]
        source_breaks = {len(normalized(''.join(item.text for item in lyrics[i:i+part])))
                         for part in range(1, a)}
        target_breaks = {len(normalized(''.join(item.reference or item.text
                                              for item in translations[j:j+part])))
                         for part in range(1, b)}
        if source_breaks & target_breaks:
            # A common interior boundary is two independent alignments. In
            # particular, do not merge repeated complete lines merely to make
            # the concatenation unique and steal their separate identities.
            continue
        # Positive character coverage prefers anchored text. A small grouping
        # cost preserves independently matching sentence boundaries on ties.
        candidates.append((i, j, a, b, len(key) - .05 * (a+b-2)))
    candidates.sort()
    paths = []
    for index, (i, j, a, b, weight) in enumerate(candidates):
        best_score, previous = 0., None
        for before, (pi, pj, pa, pb, _) in enumerate(candidates[:index]):
            if pi+pa <= i and pj+pb <= j and paths[before][0] > best_score:
                best_score, previous = paths[before][0], before
        paths.append((best_score+weight, previous))
    if not paths:
        return []
    cursor = max(range(len(paths)), key=lambda index: paths[index][0])
    result = []
    while cursor is not None:
        result.append(candidates[cursor][:4])
        cursor = paths[cursor][1]
    return list(reversed(result))


def _ordered_groups(lyrics, translations, order_confirmed):
    anchors = _exact_text_groups(lyrics, translations)
    if not anchors:
        from .translation_alignment import paragraph_groups
        return paragraph_groups(lyrics, translations, order_confirmed)
    result = []
    left = right = 0
    for i, j, a, b in anchors:
        from .translation_alignment import paragraph_groups
        result.extend(paragraph_groups(lyrics[left:i], translations[right:j], order_confirmed))
        reasons = ['原文或双语对照文字精确匹配；请核对画面起止时间']
        if a > 1 or b > 1:
            reasons.append('连续多行文字合并后精确匹配，时间来自整组画面，不代表各半句的独立起止时间')
        result.append((lyrics[i:i+a], translations[j:j+b], .93, reasons))
        left, right = i+a, j+b
    from .translation_alignment import paragraph_groups
    result.extend(paragraph_groups(lyrics[left:], translations[right:], order_confirmed))
    return result


def _order_only_groups(lyrics,translations,order_confirmed):
    n,m=len(lyrics),len(translations)
    # Global sequence DP supports skips and 1..3 : 1..3 groups. Numerical
    # structure only proposes groupings; it never proves cross-language meaning.
    dp={(0,0):(0,None)}
    def update(i,j,score,previous):
        if (i,j) not in dp or score>dp[(i,j)][0]:
            dp[(i,j)]=(score,previous)
    for i in range(n+1):
        for j in range(m+1):
            if (i,j) not in dp:
                continue
            value=dp[(i,j)][0]
            if i<n:
                update(i+1,j,value-.65,(i,j,0,0))
            if j<m:
                update(i,j+1,value-.85,(i,j,0,1))
            for a in range(1,min(3,n-i)+1):
                for b in range(1,min(3,m-j)+1):
                    group_l,group_t=lyrics[i:i+a],translations[j:j+b]
                    timed=all(valid_time(t.start, t.end) for t in group_t) and all(valid_time(l.start, l.end) for l in group_l)
                    if timed:
                        left=min(t.start for t in group_t)
                        right=max(t.end for t in group_t)
                        source_left=min(l.start for l in group_l)
                        source_right=max(l.end for l in group_l)
                        overlap=max(0,min(right,source_right)-max(left,source_left))
                        union=max(right,source_right)-min(left,source_left)
                        evidence=overlap/max(.01,union)
                        delta=abs(left-source_left)+abs(right-source_right)
                        score=1.2*evidence+.3*max(0,1-delta/8)
                    else:
                        score=.8-.4*abs((i+a/2)/max(1,n)-(j+b/2)/max(1,m))
                    score-=.30*(a+b-2)
                    update(i+a,j+b,value+score,(i,j,a,b))
    cursor=(n,m)
    result=[]
    while cursor!=(0,0):
        _,back=dp[cursor]
        if back is None:
            break
        i,j,a,b=back
        if a and b:
            group_l,group_t=lyrics[i:i+a],translations[j:j+b]
            reasons=[]
            confidence=.9 if order_confirmed and n==m and a==b==1 else .66
            if a!=1 or b!=1 or n!=m:
                reasons.append('原文与译文句数不一致，当前配对仅供参考，请核对句意')
                if not (all(valid_time(t.start, t.end) for t in group_t)
                        and all(valid_time(l.start, l.end) for l in group_l)):
                    reasons.append('当前仅按行序和数量猜测分组，没有跨语言句意依据；所附时间来自候选原文画面，并非译文已对齐，请逐句确认')
                confidence=.5
            elif not (all(valid_time(t.start, t.end) for t in group_t)
                      and all(valid_time(l.start, l.end) for l in group_l)):
                reasons.append('当前仅按行序和数量猜测分组，没有跨语言句意依据；句数相同也可能因拆句和漏词抵消而错配，请逐句确认')
                confidence = min(confidence, .5)
            elif not order_confirmed:
                reasons.append('已按行序或原时间初步配对，请核对原文与译文是否对应')
            result.append((group_l,group_t,confidence,reasons))
        elif b:
            result.append(([],translations[j:j+1],0,["无法确定译文对应的原文"] ))
        cursor=(i,j)
    return list(reversed(result))



def _coalesce_shared_observation_groups(assignments, associations):
    """One indivisible OCR observation should not clone its full time interval.

    Only adjacent proposed groups with exactly the same observation IDs and
    explicit whole-line/half-line evidence are combined. Independent same-time
    lines have different IDs and stay independent. Protected user rows never
    enter this function. Keep all source and translation IDs for manual regroup.
    """
    def evidence_key(group):
        lyrics, _, _, _ = group
        return {visual.id for lyric in lyrics for visual,_,notes in associations.get(lyric.id,[])
                if any('\u5171\u4eab\u6574\u53e5\u89c6\u89c9\u533a\u95f4' in note for note in notes)}

    result = []
    keys = []
    for group in assignments:
        key = evidence_key(group)
        if result and key and key.intersection(keys[-1]):
            keys[-1] |= key
            old_l, old_t, old_conf, old_notes = result[-1]
            new_l, new_t, confidence, notes = group
            lyrics = old_l + [item for item in new_l if item.id not in {l.id for l in old_l}]
            translations = old_t + [item for item in new_t if item.id not in {t.id for t in old_t}]
            result[-1] = (lyrics, translations, min(old_conf, confidence),
                          list(dict.fromkeys(old_notes + notes + [
                              '多个半句共用同一条 OCR 画面证据，合为一个时间组；未按字数臆造分句时间'])))
        else:
            result.append(group)
            keys.append(key)
    return result

def match_project(project):
    protected=[m for m in project.mappings if project.alignment_graph.manual_locked(m.id) or m.locked or m.confirmed or m.text_segments or m.human_edited]
    manual_edges=[dict(lyric_ids=m.lyric_ids,visual_ids=m.visual_ids) for m in protected]
    associations,unknown=associate_visuals(project.lyrics,project.visuals,manual_edges)
    project.warnings = [w for w in project.warnings if not w.startswith('OCR 无效时间证据')]
    invalid_count = sum(not valid_time(v.start, v.end) for v in project.visuals)
    if invalid_count:
        project.warnings.append(f'OCR 无效时间证据 {invalid_count} 条：原始记录已保留，不把零时长或异常区间写入时间轴；请重新核对。')
    locked=[m for m in project.mappings if project.alignment_graph.manual_locked(m.id) or m.locked or m.confirmed or m.text_segments or m.human_edited or m.text_role != 'translation']
    used={i for m in locked for i in m.translation_ids}
    translations=[t for t in project.translations if t.id not in used]
    assignments=[]
    if translations and all(t.reference for t in translations):
        for index,t in enumerate(translations):
            group,confidence,reasons=_reference_mapping(t,project.lyrics,index,len(translations))
            assignments.append((group,[t],confidence,reasons))
    else:
        locked_lyrics={i for m in locked for i in m.lyric_ids}
        available=[l for l in project.lyrics if l.id not in locked_lyrics]
        assignments=_ordered_groups(available,translations,project.settings.get("order_confirmed",False))
    assignments = _coalesce_shared_observation_groups(assignments, associations)
    # Identity follows immutable import-occurrence IDs, never text/timing or
    # row position. Re-pairing an unchanged unique group is not a new sentence.
    # Ambiguous groups and actual structural changes get fresh identities with
    # ancestry, rather than arbitrarily stealing one repeated sentence's ID.
    old_groups=defaultdict(list)
    for row in project.mappings:
        if row not in locked:old_groups[tuple(sorted(row.translation_ids))].append(row)
    new_groups=Counter(tuple(sorted(t.id for t in group)) for _,group,_,_ in assignments)
    reserved={row.id for row in project.mappings}
    emitted={row.id for row in locked}
    result=list(locked)
    for lyrics,translation_group,confidence,reasons in assignments:
        reasons=list(reasons)
        candidates=[item for lyric in lyrics for item in associations[lyric.id]]
        visuals=[]
        for visual,score,notes in candidates:
            if visual.id not in {v.id for v in visuals}:
                visuals.append(visual)
            confidence=min(confidence,score)
            reasons.extend(notes)
        for lyric in lyrics:
            if not associations[lyric.id]:
                reasons.append('未在画面中识别到本句歌词，请手动打轴')
            reasons.extend(note for note in lyric.warnings if "重复事件已合并" not in note)
        start=end=None
        if visuals and all(associations[l.id] for l in lyrics):
            start,end=min(v.start for v in visuals),max(v.end for v in visuals)
            ordered=sorted(visuals,key=lambda v:v.start)
            frontier=ordered[0].end
            for visual in ordered[1:]:
                if visual.start-frontier>1:
                    reasons.append('本句关联的多段识别时间之间有较长空档，建议拆句或核对起止范围')
                frontier=max(frontier,visual.end)
        else:
            confidence=0
        tids=[t.id for t in translation_group]
        key=tuple(sorted(tids));previous=old_groups.get(key,[])
        if len(previous)==1 and new_groups[key]==1 and previous[0].id not in emitted:
            identity=previous[0].id;lineage=list(previous[0].lineage_ids)
        else:
            ancestors=[row for rows in old_groups.values() for row in rows
                       if set(row.translation_ids).intersection(tids)]
            lineage=list(dict.fromkeys(i for row in ancestors for i in row.lineage_ids+[row.id]))
            identity=stable_id("map",*tids) if not ancestors else 'map-'+uuid.uuid4().hex
            while identity in reserved or identity in emitted:identity='map-'+uuid.uuid4().hex
        emitted.add(identity)
        result.append(Mapping(identity,tids,[l.id for l in lyrics],[v.id for v in visuals],
                              start,end,confidence,list(dict.fromkeys(reasons)),lineage_ids=lineage))
    # Preserve the user's editing order across rescans, including split/inserted rows.
    old_rank={tid:i for i,m in enumerate(project.mappings) for tid in m.translation_ids}
    exact_rank={m.id:i for i,m in enumerate(project.mappings)}
    translation_rank={t.id:i for i,t in enumerate(project.translations)}
    result.sort(key=lambda m:exact_rank.get(m.id,min((old_rank.get(t,len(exact_rank)+translation_rank.get(t,0)) for t in m.translation_ids),default=len(exact_rank))))
    project.mappings=result
    unmatched=[l.id for l in project.lyrics if not any(l.id in m.lyric_ids for m in result)]
    project.warnings=[w for w in project.warnings if not w.startswith(("存在未配对", "存在无法对应"))]
    if unmatched:
        project.warnings.append(f'存在未配对的参考原文 {len(unmatched)} 句（可能省略了重复副歌或缺少译文），请留意核对')
    if unknown:
        project.warnings.append(f'存在无法对应原文的 OCR 识别结果 {len(unknown)} 条（可能是背景文字或误识），请留意核对')
    return result
