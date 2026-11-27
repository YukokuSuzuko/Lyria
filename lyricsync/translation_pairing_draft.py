"""Explicit ID/character-slice proposals for existing translations; no generation."""
from copy import deepcopy
from dataclasses import asdict
from .model import Project, Mapping, Translation, stable_id
from .graphemes import validate_character_gap, character_gaps
from .ocr_correspondence import source_anchors, evidence_fingerprint

def origin_span(identity, translations, seen=None):
    """Resolve immutable slice ancestry, including ranges, fail closed on damage."""
    seen = set() if seen is None else seen
    if identity in seen or identity not in translations: raise ValueError('Invalid translation slice ancestry')
    seen.add(identity)
    item = translations[identity]; meta = item.subtitle_metadata
    if not meta.get('capsule_derived'): return identity, 0, len(item.text)
    parent = meta.get('origin_translation_id')
    a, b = meta.get('char_start'), meta.get('char_end')
    if parent not in translations or type(a) is not int or type(b) is not int:
        raise ValueError('Invalid translation slice provenance')
    original = translations[parent].text
    validate_character_gap(original, a); validate_character_gap(original, b)
    if not 0 <= a < b <= len(original) or item.text != original[a:b]:
        raise ValueError('Derived translation differs from original slice')
    root, begin, end = origin_span(parent, translations, seen)
    return root, begin+a, begin+b


def legacy_origin_spans(identity, translations, seen=None):
    # v0.22 capsules did not store character offsets. Never invent offsets from
    # text matching (repeated words are ambiguous). Reserve whole originals
    # conservatively, so opening the desk cannot crash or reuse protected text.
    seen = set() if seen is None else seen
    if identity in seen or identity not in translations:
        raise ValueError('Invalid translation slice ancestry')
    seen.add(identity)
    meta = translations[identity].subtitle_metadata
    if meta.get('capsule_derived') and 'origin_ids' in meta and 'origin_translation_id' not in meta:
        origins = meta['origin_ids']
        if not isinstance(origins, list) or not origins:
            raise ValueError('Invalid legacy translation origins')
        return [span for origin in origins for span in legacy_origin_spans(origin, translations, set(seen))]
    return [origin_span(identity, translations)]


def row_spans(row, translations):
    if row.text_segments and row.text_override is None:
        result = []
        for part in row.text_segments:
            identity = part['id']; a, b = part['start'], part['end']
            if identity not in translations: raise ValueError('Unknown mapped translation')
            text = translations[identity].text
            validate_character_gap(text,a); validate_character_gap(text,b)
            if a >= b: raise ValueError('Invalid mapped character range')
            spans = legacy_origin_spans(identity, translations)
            if 'origin_ids' in translations[identity].subtitle_metadata:
                result.extend(spans)
            else:
                origin, begin, end = spans[0]
                result.append((origin,begin+a,begin+b))
        return result
    return [span for i in row.translation_ids for span in legacy_origin_spans(i, translations)]


def protected_row(project, row):
    return bool(project.alignment_graph.manual_locked(row.id) or row.locked or row.confirmed
                or row.human_edited or row.text_segments)


def protected_usage(project):
    translations = {t.id: t for t in project.translations}
    spans, visual_ids = {}, set()
    for row in project.mappings:
        if not protected_row(project, row): continue
        visual_ids.update(row.visual_ids)
        for identity, a, b in row_spans(row, translations):
            spans.setdefault(identity, []).append((a,b))
    return spans, visual_ids


def available_slices(project):
    occupied, _ = protected_usage(project)
    result = []
    for t in project.translations:
        if t.subtitle_metadata.get('capsule_derived'): continue
        cursor = 0
        for a, b in sorted(occupied.get(t.id, [])) + [(len(t.text),len(t.text))]:
            if cursor < a: result.append({'translation_id':t.id,'start':cursor,'end':a})
            cursor = max(cursor,b)
    return result


def request_payload(project):
    """Call only after the user requests AI correspondence. Contains no credentials."""
    occupied, used_visuals = protected_usage(project)
    available = available_slices(project)
    return {'anchors': [{'id': a.id, 'text': a.text} for a in source_anchors(project) if not used_visuals.intersection(a.visual_ids)],
            'available_slices': available,
            'translations': [{'id': t.id, 'text': t.text} for t in project.translations
                             if not t.subtitle_metadata.get('capsule_derived')],
            'response_schema': {'groups': [{'anchor_ids': ['existing id'],
                'slices': [{'translation_id': 'existing id', 'start': 0, 'end': 'codepoint gap'}],
                'piece_sizes': [1]}]},
            'instruction': 'Return IDs and grapheme-safe slice offsets only. Do not generate or rewrite text. piece_sizes is the ordered positive number of slices in each visible output capsule; it must sum to the slice count. Only select available_slices ranges; protected occupied text cannot be reused. Unmatched items remain visible and unpaired.'}



def _split_domain(project, anchor_ids, frame_index):
    if frame_index is None:
        raise ValueError('Split timing requires a decoded frame index')
    frame_index.candidate_signature(refresh=True)
    anchors = {a.id: a for a in source_anchors(project)}
    if not anchor_ids or any(i not in anchors for i in anchor_ids):
        raise ValueError('Split timing requires existing source anchors')
    start = min(anchors[i].start for i in anchor_ids)
    end = max(anchors[i].end for i in anchor_ids)
    frames = [i + 1 for i, t in enumerate(frame_index.stamps) if start < t < end]
    return start, end, frames


def estimate_split_frames(project, anchor_ids, slices, frame_index, *, piece_sizes=None):
    """Grapheme-weighted initial boundaries, snapped to actual PTS, not OCR evidence."""
    # Validate all existing-text slices before computing any proposed timing.
    group = {'anchor_ids': anchor_ids, 'slices': slices, **({'piece_sizes': piece_sizes} if piece_sizes is not None else {})}
    make_draft(project, {'groups': [group]})
    pieces = output_pieces(group)
    start, end, frames = _split_domain(project, anchor_ids, frame_index)
    if len(frames) < len(pieces) - 1:
        raise ValueError('Not enough decoded boundaries for positive-duration slots')
    texts = {t.id: t.text for t in project.translations}
    weights = [len(character_gaps(''.join(texts[p['translation_id']][p['start']:p['end']] for p in piece))) - 1
               for piece in pieces]
    cuts = []; previous = -1; cumulative = 0
    for n, weight in enumerate(weights[:-1]):
        cumulative += weight
        target = start + (end - start) * cumulative / sum(weights)
        last = len(frames) - (len(weights) - 1 - n)
        selected = min(range(previous + 1, last + 1),
                       key=lambda j: (abs(frame_index.stamp(frames[j]) - target), j))
        cuts.append(frames[selected]); previous = selected
    return cuts


def output_pieces(group):
    """Ordered capsule boundaries, independent of underlying immutable slices."""
    slices = group['slices']
    sizes = group.get('piece_sizes', [1] * len(slices))
    if (not isinstance(sizes, list) or not sizes or
            any(type(n) is not int or n < 1 for n in sizes) or sum(sizes) != len(slices)):
        raise ValueError('piece_sizes must partition all source slices into nonempty output capsules')
    pieces, cursor = [], 0
    for size in sizes:
        pieces.append(slices[cursor:cursor+size]); cursor += size
    return pieces


def make_draft(project, response, *, frame_index=None):
    """Validate untrusted suggestions; this does NOT change the document."""
    if not isinstance(response, dict) or set(response) != {'groups'} or not isinstance(response['groups'], list):
        raise ValueError('配对草案仅允许 groups，不接受生成文本')
    anchors = {a.id: a for a in source_anchors(project)}
    translations = {t.id: t for t in project.translations if not t.subtitle_metadata.get('capsule_derived')}
    used_anchors = set()
    used_slices = {}
    for group in response['groups']:
        if (not isinstance(group, dict) or not {'anchor_ids', 'slices'} <= set(group)
                or set(group) - {'anchor_ids', 'slices', 'split_frames', 'piece_sizes'}):
            raise ValueError('配对槽字段无效；禁止新译文')
        ids = group['anchor_ids']
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in anchors for i in ids):
            raise ValueError('未知原文锚点')
        if len(set(ids)) != len(ids) or used_anchors.intersection(ids):
            raise ValueError('同一时间锚点不得被隐式克隆；请先在草案中合并译文切片')
        used_anchors.update(ids)
        if not isinstance(group['slices'], list) or not group['slices']:
            raise ValueError('配对槽需要已有译文切片')
        pieces = output_pieces(group)
        for part in group['slices']:
            if not isinstance(part, dict) or set(part) != {'translation_id', 'start', 'end'}:
                raise ValueError('只允许已有译文 ID 与字素切点')
            identity = part['translation_id']
            if not isinstance(identity, str) or identity not in translations:
                raise ValueError('未知译文')
            text = translations[identity].text
            a, b = part['start'], part['end']
            validate_character_gap(text, a); validate_character_gap(text, b)
            if a >= b: raise ValueError('切片不能为空或倒序')
            if any(max(a, x) < min(b, y) for x, y in used_slices.get(identity, [])):
                raise ValueError('译文切片重复使用')
            used_slices.setdefault(identity, []).append((a, b))
        if 'split_frames' in group:
            start, end, available = _split_domain(project, ids, frame_index)
            cuts = group['split_frames']
            if (not isinstance(cuts, list) or len(cuts) != len(pieces) - 1
                    or any(type(c) is not int or c not in available for c in cuts)
                    or any(b <= a for a, b in zip(cuts, cuts[1:]))):
                raise ValueError('Split boundaries must be increasing decoded frames inside source time')
    return {'frame_signature': frame_index.candidate_signature(refresh=True)
            if any('split_frames' in g for g in response['groups']) else None,
            'version': 1, 'fingerprint': evidence_fingerprint(project),
            'groups': deepcopy(response['groups']), 'status': 'pending_confirmation'}


def apply_draft(project, draft, *, confirmed=False, frame_index=None):
    """Return detached validated project; caller adopts in ONE History transaction.

    Cancellation is simply dropping the draft. The current document is untouched.
    Optional split_frames divide output slots only; raw source evidence is unchanged.
    """
    if not confirmed: raise ValueError('请先确认应用配对草案')
    if not isinstance(draft, dict) or draft.get('version') != 1 or draft.get('status') != 'pending_confirmation':
        raise ValueError('无效配对草案')
    if draft.get('fingerprint') != evidence_fingerprint(project):
        raise ValueError('工程已变化，请重新生成或核对草案')
    clean = make_draft(project, {'groups': draft.get('groups')}, frame_index=frame_index)
    if draft.get('frame_signature') != clean['frame_signature']:
        raise ValueError('Decoded frame index changed; rebuild the draft')
    result = Project.from_dict(deepcopy(project.to_dict()))
    anchors = {a.id: a for a in source_anchors(result)}
    translations = {t.id: t for t in result.translations}
    vids = {v for g in clean['groups'] for i in g['anchor_ids'] for v in anchors[i].visual_ids}
    tids = {p['translation_id'] for g in clean['groups'] for p in g['slices']}
    requested = [(p['translation_id'],p['start'],p['end']) for g in clean['groups'] for p in g['slices']]
    def overlaps(row):
        return any(i == j and max(a,c) < min(b,d) for i,a,b in row_spans(row,translations)
                   for j,c,d in requested)
    def row_origins(row): return {i for i,a,b in row_spans(row,translations)}
    affected = [m for m in result.mappings if vids.intersection(m.visual_ids) or overlaps(m)]
    for row in affected:
        if protected_row(result, row):
            raise ValueError('配对涉及已编辑或已确认行；请先明确解除保护')
    rows = [m for m in result.mappings if m.id not in {r.id for r in affected}]
    used = {}
    # Retained timeline rows already cover these original characters. Do not
    # duplicate unrelated or protected mappings while exposing missing imports.
    for row in rows:
        for origin, a, b in row_spans(row, translations):
            used.setdefault(origin, []).append((a, b))
    output_groups = []
    for group in clean['groups']:
        if 'split_frames' not in group:
            output_groups.append((group, None))
            continue
        start, end, _ = _split_domain(result, group['anchor_ids'], frame_index)
        boundaries = [start] + [frame_index.stamp(c) for c in group['split_frames']] + [end]
        for n, parts in enumerate(output_pieces(group)):
            output_groups.append(({'anchor_ids': group['anchor_ids'], 'slices': parts, 'piece_sizes': [len(parts)]},
                                  (boundaries[n], boundaries[n + 1])))
    for index, (group, output_time) in enumerate(output_groups):
        sources = [anchors[i] for i in group['anchor_ids']]
        parts = group['slices']; ids = []; texts = []
        for p in parts:
            origin = translations[p['translation_id']]
            a, b = p['start'], p['end']; text = origin.text[a:b]
            identity = stable_id('translation-slice', origin.id, str(a), str(b))
            if identity not in {t.id for t in result.translations}:
                result.translations.append(Translation(identity, text, subtitle_metadata={
                    'capsule_derived': True, 'origin_translation_id': origin.id,
                    'char_start': a, 'char_end': b}))
            ids.append(identity); texts.append(text)
            used.setdefault(origin.id, []).append((a, b))
        rows.append(Mapping(stable_id('confirmed-pair', draft['fingerprint'], str(index)), ids,
            list(dict.fromkeys(l for a in sources for l in a.lyric_ids)),
            list(dict.fromkeys(v for a in sources for v in a.visual_ids)),
            output_time[0] if output_time else min((a.start for a in sources), default=None),
            output_time[1] if output_time else max((a.end for a in sources), default=None),
            0.5, ['用户确认已有译文配对草案；未生成译文；原始画面证据未改'],
            human_edited=False, method='user_pairing_draft',
            text_override='\n'.join(''.join(translations[p['translation_id']].text[p['start']:p['end']]
                                           for p in piece) for piece in output_pieces(group))))
        if output_time:
            rows[-1].reasons.append('按现有译文字数估计并由用户确认的输出分界；非 OCR 实测边界，原锚点未改')
    # Unused text fragments remain visible as untimed rows, not silently swallowed.
    for identity, original in translations.items():
        if original.subtitle_metadata.get('capsule_derived'): continue
        text = original.text; cursor = 0
        if not used.get(identity) and text:
            rows.append(Mapping(stable_id('unpaired-translation', identity), [identity],
                                reasons=['未配对译文，原样保留待打轴']))
            continue
        for a, b in sorted(used.get(identity, [])) + [(len(text), len(text))]:
            if cursor < a:
                piece_id = stable_id('translation-slice', identity, str(cursor), str(a))
                if piece_id not in {t.id for t in result.translations}:
                    result.translations.append(Translation(piece_id, text[cursor:a], subtitle_metadata={
                        'capsule_derived': True, 'origin_translation_id': identity, 'char_start': cursor, 'char_end': a}))
                rows.append(Mapping(stable_id('unpaired-slice', piece_id), [piece_id],
                                    reasons=['未配对译文切片，原样保留待打轴']))
            cursor = max(cursor, b)
    # Re-pairing may detach only a subset of a former group. Preserve every
    # affected visual anchor that no longer carries a translation.
    detached_visuals = {v for row in affected for v in row.visual_ids}
    occupied_visuals = {v for row in rows for v in row.visual_ids}
    for anchor in anchors.values():
        if detached_visuals.intersection(anchor.visual_ids) and not occupied_visuals.intersection(anchor.visual_ids):
            rows.append(Mapping(stable_id('visual-unpaired', anchor.id), [],
                                list(anchor.lyric_ids), list(anchor.visual_ids),
                                anchor.start, anchor.end, method='visual-unpaired',
                                reasons=['解绑后保留原始画面时间，待重新配对']))
            occupied_visuals.update(anchor.visual_ids)
    rows.sort(key=lambda row: (row.start is None, row.start if row.start is not None else float('inf')))
    result.mappings = rows
    from .roi_attribution import inherit
    inherit(result, [row for row in rows if row.method in ('user_pairing_draft', 'visual-unpaired')])
    result.validate_timeline()
    assert [asdict(v) for v in result.visuals] == [asdict(v) for v in project.visuals]
    assert [asdict(l) for l in result.lyrics] == [asdict(l) for l in project.lyrics]
    assert all(next(t.text for t in result.translations if t.id == old.id) == old.text for old in project.translations)
    return result

