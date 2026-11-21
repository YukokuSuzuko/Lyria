"""Named project styles, independent actors and explicit per-cue overrides."""
from copy import deepcopy
from pathlib import Path
import json
import uuid
from .speakers import validate_style, default_style
from .storage import atomic_json


def validate_profiles(profiles):
    if not isinstance(profiles, dict) or len(profiles) > 256:
        raise ValueError('样式列表无效或超过 256 项。')
    result = {}
    for identity, profile in profiles.items():
        if not isinstance(identity, str) or not identity or len(identity) > 80:
            raise ValueError('样式编号无效。')
        if not isinstance(profile, dict) or set(profile) != {'name', 'style'}:
            raise ValueError('样式数据无效。')
        name = profile['name']
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError('样式名称须为 1–80 个字符。')
        result[identity] = {'name': name.strip(), 'style': validate_style(profile['style'])}
    return result


def project_profiles(project):
    profiles = validate_profiles(project.settings.get('subtitle_styles', {}))
    if not profiles:
        profiles = {'default': {'name': '默认样式', 'style': default_style(project)}}
    return profiles


def current_style_id(project):
    profiles = project_profiles(project)
    identity = project.settings.get('current_subtitle_style', '')
    return identity if identity in profiles else next(iter(profiles))


def state(project):
    profiles = project_profiles(project)
    return {'items': [{'id': key, 'name': value['name']} for key, value in profiles.items()],
            'currentId': current_style_id(project)}


def validate_overrides(overrides):
    if not isinstance(overrides, dict) or not set(overrides) <= {'font', 'size', 'color', 'bold', 'italic'}:
        raise ValueError('个人样式覆盖无效。')
    base = dict(font='Arial', size=48, color='#FFFFFF', bold=False, italic=False)
    validate_style(dict(base, **overrides))
    return deepcopy(overrides)


def validate_project_styles(project):
    profiles = project_profiles(project)
    for row in project.mappings:
        if not isinstance(row.style_id, str) or row.style_id and row.style_id not in profiles:
            raise ValueError('字幕引用的命名样式不存在。')
        validate_overrides(row.style_overrides)


def style_name(project, row):
    return project_profiles(project).get(row.style_id, {}).get('name', '个人样式' if row.speaker_style else '默认样式')


def _save_profiles(draft, profiles, current_id):
    from .speakers import style_for
    previous_profiles=project_profiles(draft)
    # Deletion detaches references but retains the effective appearance.
    for row in draft.mappings:
        if row.style_id and row.style_id not in profiles:
            row.speaker_style = style_for(draft, row, profiles=previous_profiles)
            row.style_id = ''
            row.style_overrides = {}
    draft.settings['subtitle_styles'] = deepcopy(profiles)
    draft.settings['current_subtitle_style'] = current_id
    for row in draft.mappings:
        if row.style_id: row.speaker_style = style_for(draft, row, profiles=profiles)


def save_project_profiles(project, history, profiles, current_id, *, apply_ids=None, clear_overrides=True):
    profiles = validate_profiles(profiles)
    if not profiles: raise ValueError('工程至少保留一套样式。')
    if current_id not in profiles: current_id = next(iter(profiles))
    if apply_ids is not None: _validate_scope(project, apply_ids)
    with history.transaction(project, '修改字幕样式管理') as draft:
        _save_profiles(draft, profiles, current_id)
        if apply_ids is not None:
            _apply_style(draft, apply_ids, current_id, clear_overrides=clear_overrides)


def select_style(project, history, identity):
    profiles = project_profiles(project)
    if identity not in profiles: raise ValueError('所选样式不存在。')
    with history.transaction(project, '选择当前字幕样式') as draft:
        draft.settings['subtitle_styles'] = profiles
        draft.settings['current_subtitle_style'] = identity


def _validate_scope(project, ids):
    if not isinstance(ids, (list, tuple)) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('请先选择要应用样式的句子。')
    rows = {m.id: m for m in project.mappings}
    locked = project.settings.get('manual_locked_ids', [])
    if any(i not in rows or i in locked for i in ids):
        raise ValueError('所选句已改变或包含锁定句，请先解锁。')


def _apply_style(project, ids, identity, *, clear_overrides=True):
    from .roi_attribution import protect
    protect(project, ids, 'style_id')
    from .speakers import style_for
    if type(clear_overrides) is not bool: raise ValueError('个人覆盖处理选项无效。')
    profiles = project_profiles(project)
    project.settings['subtitle_styles'] = profiles
    for row in project.mappings:
        if row.id in ids:
            if not clear_overrides and not row.style_id and row.speaker_style:
                row.style_overrides = dict(row.speaker_style, **row.style_overrides)
            if clear_overrides: row.style_overrides = {}
            row.style_id = identity
            row.speaker_style = style_for(project, row, profiles=profiles)
            row.human_edited = True


def apply_style(project, history, ids, identity=None, *, clear_overrides=True):
    _validate_scope(project, ids)
    profiles = project_profiles(project)
    identity = identity or current_style_id(project)
    if identity not in profiles: raise ValueError('所选样式不存在。')
    with history.transaction(project, '应用字幕样式到所选句') as draft:
        _apply_style(draft, ids, identity, clear_overrides=clear_overrides)


def set_style_overrides(project, history, ids, overrides):
    _validate_scope(project, ids)
    overrides = validate_overrides(overrides)
    with history.transaction(project, '修改所选句的个人样式覆盖') as draft:
        from .roi_attribution import protect
        protect(draft, ids, 'style_id')
        for row in draft.mappings:
            if row.id in ids:
                row.style_overrides = deepcopy(overrides)
                row.human_edited = True


def load_library(path):
    path = Path(path)
    if not path.exists(): return {}
    if path.stat().st_size > 2 * 1024 * 1024: raise ValueError('样式文件超过 2 MB。')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict): raise ValueError('样式库格式无效。')
    if data.get('format') != 'lyria-styles-v1': raise ValueError('不支持的样式库格式。')
    return validate_profiles(data.get('styles'))


def save_library(path, profiles, inputs=()):
    atomic_json(path, {'format': 'lyria-styles-v1', 'styles': validate_profiles(profiles)}, inputs)


def ass_style(style):
    """Read the supported ASS style subset, never pretend to preserve effects."""
    raw_color = style.get('primarycolour', '&H00FFFFFF').strip().upper()
    color = raw_color.replace('&H', '').rstrip('&')
    try:
        n = int(color, 16) if raw_color.startswith('&H') else int(color)
        return validate_style(dict(font=style.get('fontname', 'Arial').strip(),
            size=round(float(style.get('fontsize', 48))),
            color=f'#{n & 255:02X}{(n >> 8) & 255:02X}{(n >> 16) & 255:02X}',
            bold=int(style.get('bold', 0)) != 0, italic=int(style.get('italic', 0)) != 0))
    except (TypeError, ValueError) as exc:
        raise ValueError('字幕文件中包含无法读取的样式。') from exc


def import_styles(path):
    path = Path(path)
    if path.suffix.lower() not in ('.ass', '.ssa'): return load_library(path)
    # Import the supported shared subset only; no subtitle text is touched.
    if path.stat().st_size > 16 * 1024 * 1024: raise ValueError('字幕样式导入文件超过 16 MB。')
    from .subtitles import read_text
    text = read_text(path)
    section = ''; columns = []; result = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('['): section = line.lower()
        elif section in ('[v4+ styles]', '[v4 styles]'):
            if line.lower().startswith('format:'):
                columns = [v.strip().lower() for v in line.split(':', 1)[1].split(',')]
            elif line.lower().startswith('style:') and columns:
                values = line.split(':', 1)[1].strip().split(',', len(columns)-1)
                d = dict(zip(columns, values))
                result['style-'+uuid.uuid4().hex] = {'name': d.get('name', '导入样式').strip(), 'style': ass_style(d)}
    if not result: raise ValueError('文件中没有可导入的字幕样式。')
    return validate_profiles(result)
