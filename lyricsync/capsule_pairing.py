"""Non-OCR capsule pairing. Original evidence and every cue time stay intact."""
from copy import deepcopy
import uuid
from .model import Project, Translation
from .graphemes import validate_character_gap

KEY = 'translation_capsules_v022'

def _id(prefix):
    return prefix + '-' + uuid.uuid4().hex

def _validate_state(saved):
    try:
        if saved['version'] != 1 or not isinstance(saved['pieces'],list) or not isinstance(saved['groups'],list):
            raise ValueError
        ids=[];groups=[];bound=[];cues=[]
        for p in saved['pieces']:
            if not isinstance(p['id'],str) or not p['id'] or not isinstance(p['text'],str):raise ValueError
            if not isinstance(p['origins'],list) or not p['origins'] or any(not isinstance(x,str) for x in p['origins']):raise ValueError
            ids.append(p['id'])
        for g in saved['groups']:
            if not isinstance(g['id'],str) or not isinstance(g['cue_id'],str) or not isinstance(g['piece_ids'],list):raise ValueError
            if any(x not in ids for x in g['piece_ids']):raise ValueError
            groups.append(g['id']);cues.append(g['cue_id']);bound.extend(g['piece_ids'])
        if any(len(x)!=len(set(x)) for x in (ids,groups,cues,bound)):raise ValueError
    except (KeyError,TypeError,ValueError):
        raise ValueError('配对记录格式损坏，未修改原文或译文；请恢复此前工程副本。') from None


def state(project):
    saved = project.settings.get(KEY)
    if saved:
        _validate_state(saved)
        result = deepcopy(saved)
        known = {origin for piece in result['pieces'] for origin in piece['origins']}
        # Later imports join the pool; split originals never reappear as duplicates.
        for t in project.translations:
            if not t.subtitle_metadata.get('capsule_derived') and t.id not in known:
                result['pieces'].append({'id': t.id, 'text': t.text, 'origins': [t.id]})
        return result
    return {'version': 1, 'pieces': [
        {'id': t.id, 'text': t.text, 'origins': [t.id]}
        for t in project.translations
        if not t.subtitle_metadata.get('capsule_derived')], 'groups': []}

def sources(project):
    # Timed original rows are identities, never text-matched (repeated chorus safe).
    lyrics = {r.id: r for r in project.lyrics}
    saved = project.settings.get(KEY)
    before = {}
    if saved:
        _validate_state(saved)
        before = {group['cue_id']: group['before'] for group in saved['groups']
                  if isinstance(group.get('before'), dict)}
    out = []
    for row in project.mappings:
        if row.lyric_ids:
            if any(i not in lyrics for i in row.lyric_ids):
                continue
            original = '\n'.join(lyrics[i].text for i in row.lyric_ids)
        else:
            prior = before.get(row.id, {})
            role = prior.get('text_role', row.text_role)
            original = prior.get('text_override', row.text_override)
            if (role not in ('source_import', 'source_original', 'source_asr') or row.start is None
                    or row.end is None or not isinstance(original, str) or not original.strip()):
                continue
        out.append({'id': row.id, 'text': original, 'start': row.start, 'end': row.end})
    return out

def _check_row(project, identity):
    row = next((r for r in project.mappings if r.id == identity), None)
    if row is None:
        raise ValueError('原词时间段已变化，请重新选择。')
    if row.locked or row.confirmed or identity in project.settings.get('manual_locked_ids', []):
        raise ValueError('该句已锁定或确认，请先解锁，未改动配对。')
    return row

def _bound(st):
    return {p for g in st['groups'] for p in g['piece_ids']}

def operate(project, action, *, ids=(), target='', offset=None):
    """One explicit operation -> one validated draft, committed by caller history."""
    draft = Project.from_dict(deepcopy(project.to_dict()))
    before = [(r.id, r.start, r.end) for r in draft.mappings]
    original_lyrics = deepcopy(draft.to_dict()['lyrics'])
    original_translations = {t.id: t.text for t in draft.translations}
    st = state(draft)
    pieces = {p['id']: p for p in st['pieces']}
    selected = list(ids)
    if len(selected) != len(set(selected)):
        raise ValueError('选择包含重复项。')
    if action == 'source':
        allowed = {s['id'] for s in sources(draft)}
        used = {g['cue_id'] for g in st['groups']}
        if not selected or any(i not in allowed or i in used for i in selected):
            raise ValueError('请选择尚未放入配对区的原词。')
        for identity in selected:
            _check_row(draft, identity)
            st['groups'].append({'id': _id('pair'), 'cue_id': identity, 'piece_ids': []})
    elif action in ('split', 'merge'):
        if not selected or any(i not in pieces or i in _bound(st) for i in selected):
            raise ValueError('请先在右栏选择尚未配对的译文。')
        ordered = [p for p in st['pieces'] if p['id'] in selected]
        if action == 'split':
            if len(ordered) != 1:
                raise ValueError('剃刀一次切割一条译文。')
            item = ordered[0]
            validate_character_gap(item['text'], offset)
            if offset in (0, len(item['text'])):
                raise ValueError('请在文字内部的字间切割。')
            values = [dict(id=_id('piece'), text=x, origins=list(item['origins']))
                      for x in (item['text'][:offset], item['text'][offset:])]
        else:
            if len(ordered) < 2:
                raise ValueError('请按 Ctrl / Shift 选择至少两条译文。')
            positions = [st['pieces'].index(x) for x in ordered]
            if positions != list(range(min(positions), max(positions) + 1)):
                raise ValueError('请合并相邻译文，以保持原有文字顺序。')
            # Preserve exact split text; distinct original lines retain a newline.
            text = ordered[0]['text']
            origins = list(ordered[0]['origins'])
            for item in ordered[1:]:
                text += ('' if origins[-1:] == item['origins'][:1] else '\n') + item['text']
                origins.extend(x for x in item['origins'] if x not in origins)
            values = [dict(id=_id('piece'), text=text, origins=origins)]
        pos = st['pieces'].index(ordered[0])
        st['pieces'] = [p for p in st['pieces'] if p['id'] not in selected]
        st['pieces'][pos:pos] = values
    elif action == 'bind':
        group = next((g for g in st['groups'] if g['id'] == target), None)
        if not group or not selected or any(i not in pieces or i in _bound(st) for i in selected):
            raise ValueError('请把未配对译文拖到中间的原词胶囊。')
        row = _check_row(draft, group['cue_id'])
        if group.get('before') and (row.translation_ids != group.get('applied_ids') or row.text_override != group.get('applied_text')):
            raise ValueError('此句配对后已有其他文字修改，不能追加覆盖；请先核对。')
        if group.get('applied_fields') and any(getattr(row,key) != value for key,value in group['applied_fields'].items()):
            raise ValueError('此句配对后已有其他属性修改，不能追加覆盖；请先核对。')
        if row.text_segments:
            raise ValueError('该句有已分配的文字片段，请先完成片段整理。')
        if 'before' not in group:
            group['before'] = {'translation_ids': list(row.translation_ids),
                'text_override': row.text_override, 'text_role': row.text_role,
                'human_edited': row.human_edited, 'confirmed': row.confirmed,
                'reasons': list(row.reasons)}
        wanted = set(group['piece_ids']) | set(selected)
        group['piece_ids'] = [p['id'] for p in st['pieces'] if p['id'] in wanted]
        texts = []
        derived = []
        for identity in group['piece_ids']:
            piece = pieces[identity]
            texts.append(piece['text'])
            translation_id = 'capsule-' + identity
            if not any(t.id == translation_id for t in draft.translations):
                draft.translations.append(Translation(translation_id, piece['text'],
                    subtitle_metadata={'capsule_derived': True, 'origin_ids': list(piece['origins'])}))
            derived.append(translation_id)
        row.translation_ids = derived
        combined = ''
        previous = None
        for identity in group['piece_ids']:
            piece = pieces[identity]
            separator = '' if previous is None or previous['origins'][-1:] == piece['origins'][:1] else '\n'
            combined += separator + piece['text']
            previous = piece
        row.text_override = combined
        row.text_role = 'translation'
        row.human_edited = True
        row.confirmed = False
        row.reasons = list(dict.fromkeys(row.reasons + ['用户拖动配对已有译文；原词时间未改']))
        group['applied_text'] = row.text_override
        group['applied_ids'] = derived
        group['applied_fields'] = {key: deepcopy(getattr(row,key)) for key in group['before']}
    elif action in ('remove', 'detach_keep_text'):
        chosen = [g for g in st['groups'] if g['id'] in selected]
        if not chosen or len(chosen) != len(selected):
            raise ValueError('请选择配对区条目。')
        for group in chosen:
            # A removed original must not strand its translation in an invisible group.
            row = next((r for r in draft.mappings if r.id == group['cue_id']), None)
            if row is None:
                continue
            row = _check_row(draft, group['cue_id'])
            if action == 'detach_keep_text':
                # Explicit exit: preserve the current authoritative text and
                # its references, release only the workbench binding.
                continue
            if group.get('before'):
                if row.translation_ids != group['applied_ids'] or row.text_override != group['applied_text']:
                    raise ValueError('此句配对后已在其他位置编辑，请先核对，未覆盖新修改。')
                if group.get('applied_fields') and any(getattr(row,key) != value for key,value in group['applied_fields'].items()):
                    raise ValueError('此句配对后已有其他属性修改，未覆盖；请先核对。')
                for key, value in group['before'].items():
                    setattr(row, key, deepcopy(value))
        st['groups'] = [g for g in st['groups'] if g['id'] not in selected]
    else:
        raise ValueError('未知配对操作。')
    draft.settings[KEY] = st
    if 'pending_translation_ids' in draft.settings:
        completed = {origin for g in st['groups'] if g['piece_ids'] for identity in g['piece_ids']
                     for origin in next(p['origins'] for p in st['pieces'] if p['id'] == identity)}
        # A split original remains pending until every fragment is paired.
        incomplete = {origin for p in st['pieces'] if p['id'] not in _bound(st) for origin in p['origins']}
        completed -= incomplete
        draft.settings['pending_translation_ids'] = [t.id for t in draft.translations
            if not t.subtitle_metadata.get('capsule_derived') and t.id not in completed]
    assert before == [(r.id, r.start, r.end) for r in draft.mappings]
    assert original_lyrics == draft.to_dict()['lyrics']
    assert all(next(t.text for t in draft.translations if t.id == i) == text
               for i, text in original_translations.items())
    draft.validate_timeline()
    return draft
