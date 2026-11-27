"""Generate editable empty translations from observed OCR, without a provider."""
from .model import Mapping, Translation, stable_id
from .ocr_anchor_evidence import clipped_noise, observed_groups, single_crop


def build_empty_translations(project):
    """Rebuild OCR-only proposals but never discard user-edited/confirmed rows."""
    retained = [m for m in project.mappings if project.alignment_graph.manual_locked(m.id) or m.locked or m.human_edited or m.confirmed or m.text_segments]
    protected_visuals = {i for m in retained for i in m.visual_ids}
    translations = {t.id: t for t in project.translations}
    lyrics = {l.id: l for l in project.lyrics}
    sources = {}
    if project.inputs.get('ktv') and project.lyrics:
        from .matching import associate_visuals
        associations, _ = associate_visuals(project.lyrics, project.visuals)
        for identity, observations in associations.items():
            for observation, _, reasons in observations:
                sources[observation.id] = ([identity], list(reasons))
    from .sequence_alignment import eligible
    proposed = []
    usable = []
    roi = single_crop(project.settings)
    for visual in project.visuals:
        if visual.id in protected_visuals or visual.evidence.get('multiline_parent_id') in protected_visuals:
            continue
        if not eligible(visual):
            project.warnings.append(f'OCR 片段 {visual.id} 缺少有效起止时间，已跳过')
            continue
        if not clipped_noise(visual, roi):
            usable.append(visual)
    usable.sort(key=lambda visual: visual.start)
    for _, observations in observed_groups(usable, roi, project.settings.get('sample_fps', 3)):
        # A single reading or the verified centre of a three-track observation
        # owns the existing row identity. Its flanks remain raw evidence.
        primary = observations[len(observations) // 2]
        identity = stable_id("ocr-empty", primary.id)
        translations.setdefault(identity, Translation(identity, ""))
        source = stable_id("ocr-source", primary.id)
        source_ids, source_warnings = sources.get(primary.id, ([source] if source in lyrics else [], []))
        ids = [primary.id] + [v.id for v in observations if v.id != primary.id]
        notes = list(dict.fromkeys(note for v in observations for note in v.warnings))
        proposed.append(Mapping(identity, [identity], source_ids, ids,
            observations[0].start, observations[-1].end, primary.confidence,
            reasons=['已按 OCR 识别时间建立空白字幕行，请填写译文并核对'] + notes + source_warnings,
            method="ocr_empty", text_override=""))
    project.translations = list(translations.values())
    # Keep existing manual relative order. New observed segments are inserted by
    # time, without changing any manual timing or filling genuine gaps.
    result = list(retained)
    for row in proposed:
        insert = next((i for i, m in enumerate(result) if m.start is not None and m.start > row.start), len(result))
        result.insert(insert, row)
    project.mappings = result
    project.settings["input_mode"] = "video_only"
    return proposed
