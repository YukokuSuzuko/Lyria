"""Read-only OCR evidence views and visual-first downstream correspondence.

No OCR provider, tracker, preprocessing or frame refinement is changed here.
"""
from dataclasses import dataclass, asdict
from copy import deepcopy
import hashlib
import json
import re
from .model import Mapping, stable_id, valid_time
from .sequence_alignment import eligible


@dataclass(frozen=True)
class SourceAnchor:
    id: str
    text: str
    start: float
    end: float
    visual_ids: tuple
    lyric_ids: tuple = ()
    warnings: tuple = ()
    interjection_evidence: bool = False


def reference_lyrics(project):
    # stable_id uses namespace + 20 lowercase hex digits. Generated role remains
    # generated after the associated Visual is deleted/replaced; do not infer
    # authorship from today's Visual table. Real imports use the lyric namespace.
    # This is a detached view, not deletion of historical project evidence.
    return [l for l in project.lyrics if not re.fullmatch(r'ocr-source-[0-9a-f]{20}', l.id)]


def source_anchors(project):
    from .matching import associate_visuals
    refs = reference_lyrics(project)
    links, _ = associate_visuals(refs, project.visuals)
    by_visual = {}
    for lyric in refs:
        for visual, score, reasons in links.get(lyric.id, []):
            by_visual.setdefault(visual.id, []).append(lyric.id)
    from .ocr_anchor_evidence import clipped_noise, observed_groups, single_crop, has_cheer_evidence
    roi = single_crop(project.settings)
    ordered = sorted(project.visuals, key=lambda v: (v.start if v.start is not None else float('inf'),
                     v.box[1] if len(v.box) >= 2 else 0,
                     v.evidence.get('multiline_line_index', v.evidence.get('line_index', 0))))
    usable = [v for v in ordered if eligible(v) and not clipped_noise(v, roi)]
    result = []
    for text, group in observed_groups(usable, roi, project.settings.get('sample_fps', 3)):
        ids = tuple(v.id for v in group)
        lyric_ids = tuple(dict.fromkeys(i for v in group for i in by_visual.get(v.id, [])))
        warnings = tuple(dict.fromkeys(note for v in group for note in v.warnings))
        result.append(SourceAnchor(stable_id('source-anchor', *ids), text, group[0].start,
                                   group[-1].end, ids, lyric_ids, warnings,
                                   len(group)==1 and has_cheer_evidence(group[0])))
    return result


def evidence_snapshot(project):
    """Five detached views, retaining even ineligible raw visual records."""
    return {'visuals': deepcopy([asdict(v) for v in project.visuals]),
            'reference_lyrics': deepcopy([asdict(l) for l in reference_lyrics(project)]),
            'source_anchors': [asdict(a) for a in source_anchors(project)],
            'translation_units': deepcopy([asdict(t) for t in project.translations]),
            'main_timeline': deepcopy([asdict(m) for m in project.mappings])}


def evidence_fingerprint(project):
    data = project.to_dict()
    # Includes current edits: a draft must fail closed if the document changed.
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def match_visual_project(project):
    """Propose from physical observations, not a fabricated Lyric table."""
    from .matching import _ordered_groups
    original_order = {m.id:i for i,m in enumerate(project.mappings)}
    protected = [m for m in project.mappings if project.alignment_graph.manual_locked(m.id)
                 or m.locked or m.confirmed or m.human_edited or m.text_segments]
    protected_v = {i for m in protected for i in m.visual_ids}
    protected_t = {i for m in protected for i in m.translation_ids}
    anchors = [a for a in source_anchors(project) if not protected_v.intersection(a.visual_ids)]
    translations = [t for t in project.translations if t.id not in protected_t]
    # A one-frame reading alone cannot time a translation. A matching reference
    # or the surrounding component evidence can independently confirm it.
    raw = {v.id: v for v in project.visuals}
    references = {r.id: r.text for r in reference_lyrics(project)}
    from .sequence_alignment import similarity
    from .model import normalized
    reference_position = {identity:i for i, identity in enumerate(references)}
    matches = [tuple(reference_position[i] for i in a.lyric_ids
                     if i in references and similarity(a.text,references[i]) >= .72) for a in anchors]

    def corroborated(n, anchor):
        if matches[n] or len(anchor.visual_ids) != 1:
            return True
        visual = raw[anchor.visual_ids[0]]
        if visual.evidence.get('frame_count') == 1:
            return any(similarity(anchor.text, t.reference or t.text) >= .8 for t in translations)
        # A weak, short reading between two consecutive, independently matched
        # original lines has no separate source line. Preserve it for review.
        if references and len(normalized(anchor.text)) <= 4 and visual.confidence < .75:
            before = matches[n-1] if n else (-1,)
            after = matches[n+1] if n+1 < len(anchors) else (len(references),)
            if before and after and max(before)+1 == min(after):
                return False
        return True
    assignments = _ordered_groups([a for n,a in enumerate(anchors) if corroborated(n,a)], translations,
                                  project.settings.get('order_confirmed', False))
    result = list(protected)
    used = set()
    for sources, targets, confidence, reasons in assignments:
        if len(sources)>1 and len(targets)==1 and confidence < .93:
            # Structural many-to-one alignment does not establish a translation for
            # a standalone trailing exclamation. Exact bilingual anchors stay intact.
            last = sources[-1]
            target_text = normalized(targets[0].reference or targets[0].text)
            if (normalized(last.text) not in target_text
                    and len(normalized(last.text)) <= 6 and last.text.rstrip().endswith(('!', '！', '～', '〜'))
                    and any(raw[v].evidence.get('frame_count',0) >= 3 for v in last.visual_ids)
                    and last.end-last.start <= 1.2):
                sources = sources[:-1]
        vids = list(dict.fromkeys(v for a in sources for v in a.visual_ids))
        lids = list(dict.fromkeys(l for a in sources for l in a.lyric_ids))
        tids = [t.id for t in targets]
        used.update(vids)
        result.append(Mapping(stable_id('visual-pair', *tids), tids, lids, vids,
                              min((a.start for a in sources), default=None),
                              max((a.end for a in sources), default=None),
                              confidence, list(reasons), method='ocr_correspondence'))
    for anchor in anchors:
        if not used.intersection(anchor.visual_ids):
            row = Mapping(stable_id('visual-unpaired', *anchor.visual_ids), [],
                                  list(anchor.lyric_ids), list(anchor.visual_ids),
                                  anchor.start, anchor.end, 0.,
                                  ['画面时间证据已保留；未对应译文，请在配对页处理'],
                                  method='ocr_unpaired', text_override='', text_role='translation')
            index = next((i for i, m in enumerate(result) if m.start is not None and m.start > anchor.start), len(result))
            result.insert(index, row)
    # Existing row order is user-owned, including unedited neighbors around a
    # manual insert. Restore relative positions of surviving identities without
    # globally sorting by time (which would undo deliberate timeline ordering).
    positions = [i for i,row in enumerate(result) if row.id in original_order]
    surviving = sorted((result[i] for i in positions),key=lambda row:original_order[row.id])
    for index,row in zip(positions,surviving):result[index]=row
    project.mappings = result
    project.settings['input_mode'] = 'translated'
    project.validate_timeline()
    return result

