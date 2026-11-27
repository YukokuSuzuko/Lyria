"""Independent source-to-translation links; OCR observations are not source lyrics."""
from copy import deepcopy
from .translation_pairing_draft import protected_row
from .ocr_correspondence import reference_lyrics

LINKS = 'ocr_imported_source_links'
CORRECTIONS = 'ocr_imported_source_corrections'


def imported_translations(project):
    return [row for row in project.translations
            if row.text.strip() and not row.subtitle_metadata.get('capsule_derived')]


def link_ids(value):
    if isinstance(value, str):
        return [value]
    return list(value) if isinstance(value, (list, tuple)) else []


def source_links(project):
    valid = {row.id for row in reference_lyrics(project)}
    targets = {row.id for row in imported_translations(project)}
    links = {}
    for tid, value in project.settings.get(LINKS, {}).items():
        ids = list(dict.fromkeys(s for s in link_ids(value) if isinstance(s, str) and s in valid))
        if tid in targets and ids:
            links[tid] = ids[0] if len(ids) == 1 else ids
    return links


def source_text(project, source):
    return project.settings.get(CORRECTIONS, {}).get(source.id, source.text)


def paired_source_ids(mapping, links):
    return list(dict.fromkeys(s for t in mapping.translation_ids for s in link_ids(links.get(t))))


def edit_source_pair(project, operation, *, translation_id='', source_id='', source_ids=None, text=''):
    links = source_links(project)
    owners = {sid: tid for tid, ids in links.items() for sid in link_ids(ids)}
    if operation == 'edit':
        if source_id not in owners or not text.strip():
            raise ValueError('请选择已配原文，修订文字不能为空。')
        if any((source_id in row.lyric_ids or owners[source_id] in row.translation_ids)
               and protected_row(project, row) for row in project.mappings):
            raise ValueError('这条原文关联了受保护的主轴句。')
        result = deepcopy(project)
        result.settings.setdefault(CORRECTIONS, {})[source_id] = text
        return result
    if operation not in ('pair', 'unpair') or translation_id not in {t.id for t in imported_translations(project)}:
        raise ValueError('请先选择中间的一条译文。')
    if any(translation_id in row.translation_ids and protected_row(project, row) for row in project.mappings):
        raise ValueError('所选译文关联了受保护的主轴句。')
    old = link_ids(links.get(translation_id))
    if operation == 'pair':
        ids = list(source_ids) if source_ids is not None else [source_id]
        available = {s.id for s in reference_lyrics(project)}
        if not ids or any(s not in available or owners.get(s, translation_id) != translation_id for s in ids):
            raise ValueError('请只选择尚未配给其他译文的导入原文。')
        # A legacy single-source call replaces its old counterpart.
        new = list(dict.fromkeys((old if source_ids is not None else []) + ids))
    else:
        new = [s for s in old if s != source_id] if source_id else []
    if new == old:
        return project
    result = deepcopy(project)
    saved = result.settings.setdefault(LINKS, {})
    if new:
        saved[translation_id] = new[0] if len(new) == 1 else new
    else:
        saved.pop(translation_id, None)
        if not saved:
            result.settings.pop(LINKS, None)
    return result


def suggest_source_links(project):
    """Use physical OCR evidence and order. Reserve every existing manual assignment."""
    from collections import Counter
    from difflib import SequenceMatcher
    from .model import normalized
    from .sequence_alignment import eligible
    refs = reference_lyrics(project)
    words = [normalized(source_text(project, s)) for s in refs]
    positions = {s.id: i for i, s in enumerate(refs)}
    known = source_links(project)
    used = {s for ids in known.values() for s in link_ids(ids)}
    observations = {v.id: v for v in project.visuals}
    cues = sorted(project.mappings, key=lambda m: float('inf') if m.start is None else m.start)
    rows = []
    for cue in cues:
        evidence = [observations[i] for i in cue.visual_ids if i in observations]
        key = normalized(''.join(v.text for v in evidence))
        for tid in cue.translation_ids:
            rows.append((tid, key if len(cue.translation_ids) == 1 else '', cue, evidence))
    duplicates = Counter(key for _, key, _, _ in rows if key)
    position_counts = Counter(tid for tid, _, _, _ in rows)
    groups = {}
    keys = set(duplicates)
    max_chars = max((len(key) for key in keys), default=0) + 2
    for lo in range(len(words)):
        token = ''
        for hi in range(lo + 1, len(words) + 1):
            token += words[hi-1]
            if len(token) > max_chars:
                break
            if token and (token in keys or hi-lo <= 4):
                groups.setdefault(token, []).append((lo, hi))
    # Manual links divide the document into independent monotonic ranges.
    # Preserve inconsistent manual work verbatim and let the user resolve it.
    floor, seen_manual, floors = 0, set(), []
    for tid, _, _, _ in rows:
        floors.append(floor)
        if tid in known and tid not in seen_manual:
            indices = [positions[s] for s in link_ids(known[tid])]
            if indices != sorted(indices) or min(indices) < floor:
                return project, {t:'人工链接顺序交叉，请核对后再配对' for t, key, _, _ in rows if key and t not in known}
            seen_manual.add(tid)
            floor = max(indices) + 1
    high = len(refs)
    ceilings = [high] * len(rows)
    for i in reversed(range(len(rows))):
        ceilings[i] = high
        ids = link_ids(known.get(rows[i][0]))
        if ids:
            high = min(high, *(positions[s] for s in ids))
    repeated = Counter((key,floors[i],ceilings[i]) for i,(tid,key,_,_) in enumerate(rows) if key and tid not in known)
    hints = {}
    valid = {t.id for t in imported_translations(project)}
    claims = []
    for i, (tid, key, cue, evidence) in enumerate(rows):
        if tid in known:
            continue
        if not key or tid not in valid or protected_row(project, cue) or position_counts[tid] != 1:
            continue
        exact = groups.get(key, [])
        floor, ceiling = floors[i], ceilings[i]
        available = [(lo,hi) for lo,hi in exact if floor <= lo < hi <= ceiling
                     and not any(s.id in used for s in refs[lo:hi])]
        if (len(available) == 1 and repeated[key,floor,ceiling] == 1
                and all(eligible(v) and v.confidence >= .85 for v in evidence)):
            lo,hi = available[0]
            claims.append((tid, lo, hi, [s.id for s in refs[lo:hi]]))
            continue
        if exact:
            hints[tid] = '有重复、低置信或顺序冲突的对应项，请手动核对'
        elif any(abs(len(token)-len(key)) <= 2 and SequenceMatcher(None,key,token).ratio() >= .78 for token in groups):
            hints[tid] = '有近似原文，请手动核对'
    # An inverted or overlapping pair of equally exact matches has no safe
    # automatic winner. Reserve both for review before staging any changes.
    later_start = len(refs)
    limits = {}
    for tid, lo, hi, selected in reversed(claims):
        limits[tid] = later_start
        later_start = min(later_start, lo)
    result, prior_end = project, 0
    for tid, lo, hi, selected in claims:
        if lo >= prior_end and hi <= limits[tid]:
            if result is project:
                result = deepcopy(project)
            result.settings.setdefault(LINKS, {})[tid] = selected[0] if len(selected)==1 else selected
        else:
            hints[tid] = '原文顺序存在冲突，请手动核对'
        prior_end = max(prior_end, hi)
    return result, hints


def fill_ocr_placeholders(project, incoming):
    """Fill blank OCR cues without retiming, but only for a complete stable match.

    A count mismatch, edited row, mixed timeline, or grouping requiring different
    cue boundaries leaves the imported pool unbound; the caller preserves it.
    """
    if (not project.mappings or imported_translations(project) or not incoming
            or any(row.text.strip() for row in project.translations)):
        return None
    if any(row.method not in ('candidate_ocr', 'ocr_empty') or not row.visual_ids
           or row.text_override not in ('', None) or row.ignored or protected_row(project, row)
           for row in project.mappings):
        return None
    from .ocr_correspondence import match_visual_project
    from .model import valid_time
    def key(row):
        return frozenset(row.visual_ids), row.start, row.end
    rows = project.project_timeline(detached=True)
    fresh = deepcopy(project)
    fresh.replace_timeline([])
    fresh.translations = deepcopy(incoming)
    proposed = match_visual_project(fresh)
    by_key = {key(row): row for row in proposed if row.translation_ids and valid_time(row.start, row.end)}
    expected = {key(row) for row in rows}
    if (len(expected) != len(rows) or by_key.keys() != expected
            or {i for row in by_key.values() for i in row.translation_ids} != {t.id for t in incoming}):
        return None
    for row in rows:
        row.translation_ids = list(by_key[key(row)].translation_ids)
        row.text_override = None
        row.text_role = 'translation'
    result = deepcopy(project)
    result.replace_timeline([])
    result.translations = deepcopy(incoming)
    result.replace_timeline(rows)
    result.settings['input_mode'] = 'translated'
    result.alignment_graph.sync_unbound(result.lyrics, result.translations, result.visuals)
    result.validate_timeline()
    return result
