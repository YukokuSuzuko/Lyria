"""Manual actors independent of named styles; legacy snapshots stay readable."""
from copy import deepcopy
import re

COLORS = {'white': '#FFFFFF', 'lilac': '#D6B5FF', 'cyan': '#9BE7FF', 'amber': '#FFD58A'}


def validate_style(style):
    if not isinstance(style, dict) or set(style) != {'font', 'size', 'color', 'bold', 'italic'}:
        raise ValueError('字幕样式数据无效。')
    font = style['font']
    if not isinstance(font, str) or not 1 <= len(font) <= 80 or any(ord(c)<32 or c in ',{}\\' for c in font):
        raise ValueError('字幕字体名称无效。')
    if type(style['size']) is not int or not 8 <= style['size'] <= 512:
        raise ValueError('字幕字号必须为 8–512。')
    if not isinstance(style['color'], str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', style['color']):
        raise ValueError('字幕颜色无效。')
    if type(style['bold']) is not bool or type(style['italic']) is not bool:
        raise ValueError('字幕字形选项无效。')
    return deepcopy(style)


def valid_name(name):
    if not isinstance(name, str) or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('说话人名称须为不超过 80 个可显示字符。')
    return name.strip()


def region_name(project, region):
    return region.get('speaker_name') or project.settings.get('speaker_profiles', {}).get(
        region.get('speaker_id'), {}).get('name', '')


def migrate_names(data, graph):
    settings = data.get('settings', {})
    if not isinstance(settings, dict): raise ValueError('工程设置无效。')
    profiles = settings.get('speaker_profiles', {})
    if not isinstance(profiles, dict):
        raise ValueError('旧工程说话人表无效。')
    def resolve(key):
        profile = profiles.get(key)
        if not isinstance(profile, dict) or not profile.get('name'):
            raise ValueError('旧工程引用了不存在的说话人。')
        return profile['name']
    for table in (data.get('mappings', []), graph.get('cue_specs', []) if isinstance(graph, dict) else []):
        for row in table:
            if isinstance(row, dict) and 'speaker_name' not in row:
                row['speaker_name'] = resolve(row['speaker_id']) if row.get('speaker_id') else ''
    for region in settings.get('ocr_regions', []):
        if isinstance(region, dict) and region.get('speaker_id') and 'speaker_name' not in region:
            region['speaker_name'] = resolve(region['speaker_id'])
    for entry in settings.get('ocr_region_attributes', {}).values():
        if isinstance(entry, dict) and 'speaker_id' in entry and 'speaker_name' not in entry:
            entry['speaker_name'] = resolve(entry['speaker_id']) if entry['speaker_id'] else ''


def speaker_names(project):
    names = [speaker_name(project, row) for row in project.mappings]
    names.extend(region_name(project, region) for region in project.settings.get('ocr_regions', []))
    return list(dict.fromkeys(n for n in names if n))


def validate_speakers(project):
    if not isinstance(project.settings,dict):raise ValueError('工程设置无效。')
    profiles = project.settings.get('speaker_profiles', {})
    if not isinstance(profiles, dict) or len(profiles)>256:
        raise ValueError('说话人预设列表无效。')
    for identity, profile in profiles.items():
        if not isinstance(identity, str) or not re.fullmatch(r'speaker-[0-9a-f]{32}', identity):
            raise ValueError('说话人编号无效。')
        if not isinstance(profile, dict) or set(profile) not in ({'name'}, {'name', 'style'}):
            raise ValueError('说话人预设无效。')
        name=profile['name']
        if not isinstance(name,str) or not name.strip() or len(name)>80 or any(ord(c)<32 for c in name):
            raise ValueError('说话人名称无效。')
        if 'style' in profile: validate_style(profile['style'])
    for region in project.settings.get('ocr_regions', []):
        if isinstance(region, dict): valid_name(region.get('speaker_name', ''))
    for row in project.mappings:
        valid_name(row.speaker_name)
        if not isinstance(row.speaker_id, str) or row.speaker_id and row.speaker_id not in profiles:
            raise ValueError('字幕关联的说话人不存在。')
        if row.speaker_style:
            validate_style(row.speaker_style)
        elif not isinstance(row.speaker_style,dict):
            raise ValueError('字幕样式数据无效。')


def default_style(project, color='white'):
    height=project.video.get('height',1080) or 1080
    return dict(font=project.settings.get('font','Microsoft YaHei'),
                size=int(project.settings.get('font_size') or max(20,round(height*.044))),
                color=COLORS[color],bold=False,italic=False)


def assign_speaker(project, history, ids, name, style=None, *, speaker_id='', replace_styles=False):
    """Assign manual identity only unless a legacy caller supplies style explicitly."""
    if not isinstance(ids,(list,tuple)) or not ids or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):
        raise ValueError('请明确选择要设置说话人的句子。')
    if type(replace_styles) is not bool:raise ValueError('样式替换选项无效。')
    if style is not None: style=validate_style(style)
    name = valid_name(name)
    if not name:
        raise ValueError('请填写说话人名称。')
    if speaker_id and speaker_id != name and project.settings.get('speaker_profiles', {}).get(speaker_id, {}).get('name') != name:
        raise ValueError('说话人名称与旧记录不符。')
    with history.transaction(project,'设置所选句的说话人') as draft:
        from .roi_attribution import protect
        protect(draft, ids, 'speaker_name', *(['style_id'] if style is not None else []))
        rows={row.id:row for row in draft.mappings}
        for cue in ids:
            if cue not in rows:raise ValueError('所选字幕已改变。')
            if cue in draft.settings.get('manual_locked_ids',[]):raise ValueError('所选字幕已锁定，请先解锁。')
            row=rows[cue];row.speaker_id='';row.speaker_name=name
            if style is not None and (replace_styles or not row.speaker_style and not row.style_id):
                row.speaker_style=deepcopy(style);row.style_id='';row.style_overrides={}
            row.human_edited=True
        validate_speakers(draft)
    return name


def clear_speaker(project, history, ids):
    if not ids:raise ValueError('请选择字幕。')
    with history.transaction(project,'移除所选句的说话人') as draft:
        from .roi_attribution import protect
        protect(draft, ids, 'speaker_name')
        rows={r.id:r for r in draft.mappings}
        for cue in ids:
            if cue not in rows or cue in draft.settings.get('manual_locked_ids',[]):raise ValueError('所选字幕无效或已锁定。')
            rows[cue].speaker_name='';rows[cue].speaker_id='';rows[cue].human_edited=True
            # A manual style snapshot is deliberately retained.


def style_for(project, mapping, profiles=None):
    """Resolve appearance; batch callers may pass a once-validated profile map."""
    if mapping.style_id:
        if profiles is None:
            from .styles import project_profiles
            profiles = project_profiles(project)
        if mapping.style_id not in profiles: raise ValueError('字幕引用的命名样式不存在。')
        style = deepcopy(profiles[mapping.style_id]['style'])
    else:
        style = deepcopy(mapping.speaker_style) if mapping.speaker_style else default_style(project)
    style.update(mapping.style_overrides)
    return validate_style(style)


def speaker_name(project, mapping):
    return mapping.speaker_name or project.settings.get('speaker_profiles',{}).get(mapping.speaker_id,{}).get('name','')


def has_speaker_styles(project):
    return any(m.speaker_name or m.speaker_id or m.speaker_style or m.style_id or m.style_overrides for m in project.mappings)
