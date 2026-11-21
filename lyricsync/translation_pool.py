"""Import translated words into the pairing pool without adopting subtitle timings."""
from copy import deepcopy
from .capsule_pairing import KEY, state
from .model import Translation
from .subtitles import import_translation
from .translation_pairing_draft import legacy_origin_spans


def import_translation_pool(path):
    return [Translation(row.id, row.text) for row in import_translation(path) if row.text.strip()]


def update_translation_pool(project, incoming, *, replace=False, reserved=()):
    """Return a new project. Keep all original IDs needed by rows, groups and saved edits."""
    translations = {row.id:row for row in project.translations}
    incoming_ids = {row.id for row in incoming}
    if not incoming or len(incoming_ids)!=len(incoming):
        raise ValueError('文件没有可导入的译文，或包含重复的译文编号。')
    for row in incoming:
        if row.id in translations and translations[row.id].text!=row.text:
            raise ValueError('译文编号与现有文字冲突，已保留当前译文池。')
    if incoming_ids <= translations.keys():
        return project
    draft = deepcopy(project)
    if replace:
        keep = set(reserved) | incoming_ids
        for row in project.mappings:
            keep.update(row.translation_ids)
            keep.update(part['id'] for part in row.text_segments)
        for row in project.translations:
            if row.subtitle_metadata.get('capsule_derived'):
                keep.add(row.id)
                keep.update(identity for identity,_,_ in legacy_origin_spans(row.id,translations))
        st = state(project)
        used = {identity for group in st['groups'] for identity in group['piece_ids']}
        for group in st['groups']:
            keep.update(group.get('applied_ids',[]))
            keep.update(group.get('before',{}).get('translation_ids',[]))
        for piece in st['pieces']:
            if piece['id'] in used or keep.intersection(piece['origins']):
                keep.update(piece['origins'])
        draft.translations = [r for r in draft.translations if r.id in keep]
        if KEY in draft.settings:
            st['pieces'] = [p for p in st['pieces'] if set(p['origins']) <= keep]
            draft.settings[KEY] = st
    draft.translations.extend(Translation(r.id,r.text) for r in incoming if r.id not in translations)
    draft.alignment_graph.sync_unbound(draft.lyrics,draft.translations,draft.visuals)
    return draft


def pairing_label(project, candidate=None):
    original = any(t.text.strip() for t in project.lyrics) or any(v.text.strip() for v in project.visuals)
    if candidate and not original:
        original = any(v.get('text','').strip() for v in candidate.get('evidence',{}).get('visuals',[]))
    if not original:
        original = any(r.text_role!='translation' and (r.text_override or '').strip() for r in project.mappings)
    translated = any(t.text.strip() for t in project.translations) or any(
        r.text_role=='translation' and (r.text_override or '').strip() for r in project.mappings)
    return '与原文配对' if translated and not original else '与翻译配对'
