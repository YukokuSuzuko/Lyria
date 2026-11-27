"""Preserve OCR line identity without manufacturing per-line time boundaries."""
from copy import deepcopy
from dataclasses import replace
from .model import stable_id


def split_visual_lines(visuals):
    """Expand actual newline text, never wrapping or inferred lyric boundaries.

    A block-level detector supplies no independent line boxes or boundaries.
    Keep its original box/time evidence on each child and explicitly record
    that limitation. Independent detector lines retain their own identities.
    The input observations are never mutated and repeated calls are idempotent.
    """
    result = []
    for visual in visuals:
        # Current scanner has already done pixel-based spatial line separation.
        # An unresolved block must not acquire fake line geometry downstream.
        if visual.evidence.get('observation_status') == 'unresolved_multiline_block':
            result.append(visual)
            continue
        lines = [(index, text) for index, text in enumerate(visual.text.splitlines())
                 if text.strip()]
        if len(lines) <= 1:
            result.append(visual)
            continue
        for index, text in lines:
            evidence = deepcopy(visual.evidence)
            evidence.update(multiline_parent_id=visual.id, multiline_line_index=index,
                            multiline_original_text=visual.text,
                            line_boundary_status='shared_block_interval_unverified',
                            line_box_status='shared_block_box')
            warnings = list(visual.warnings) + [
                'OCR 实际换行已分别建行；仅有整块画面证据，暂共享区间，独立出入点请核对']
            result.append(replace(visual, id=stable_id('visual-line', visual.id, index),
                                  text=text, box=list(visual.box),
                                  start_bracket=list(visual.start_bracket),
                                  end_bracket=list(visual.end_bracket),
                                  warnings=warnings, evidence=evidence))
    return result
