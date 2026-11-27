"""File-drop planning for any frontend. Never guess which text is a translation.

Call plan_files with native paths, present ambiguities, then resolve_plan. Neither
operation mutates a project. The UI must open a project or start a new task using
the resolved paths; an edited task must not be silently replaced by a file drop.
"""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import uuid

VIDEO={'.mp4','.mkv','.mov','.avi','.webm','.m4v','.wmv','.ts'}
TEXT={'.ass','.ssa','.srt','.lrc','.txt'}


def plan_files(paths,role=None):
    if role not in (None,'video','ktv','translation','project'):raise ValueError('未知的导入文件类型')
    files=[];seen=set()
    for raw in paths:
        path=Path(raw).expanduser().resolve()
        if not path.is_file():raise ValueError('文件不存在或无法读取：'+path.name)
        if str(path).casefold() in seen:continue
        seen.add(str(path).casefold())
        if path.name.lower().endswith('.lyricsync.json'):roles=['project']
        elif path.suffix.lower() in VIDEO:roles=['video']
        elif path.suffix.lower() in TEXT:roles=['ktv','translation']
        else:raise ValueError('不支持的文件格式：'+path.name)
        if role:
            if role not in roles:raise ValueError('该文件格式无法作为此类素材导入：'+path.name)
            roles=[role]
        files.append({'path':str(path),'roles':roles})
    if not files:raise ValueError('未检测到要导入的文件')
    if any(f['roles']==['project'] for f in files) and len(files)!=1:
        raise ValueError('工程文件请单独拖入打开，勿与其他素材同时拖入')
    assigned={};ambiguous=[]
    for f in files:
        if len(f['roles'])==1:
            key=f['roles'][0]
            if key in assigned:raise ValueError('同一类型只能导入一个文件：'+key)
            assigned[key]=f['path']
        else:ambiguous.append(f)
    return {'assignments':assigned,'ambiguous':ambiguous}


def resolve_plan(plan,choices):
    """choices maps each ambiguous absolute path to ktv or translation."""
    expected={f['path'] for f in plan['ambiguous']}
    if set(choices)!=expected:raise ValueError('请分别指定每个文本文件是演唱原文还是中文译文')
    result=dict(plan['assignments'])
    for f in plan['ambiguous']:
        role=choices[f['path']]
        if role not in f['roles'] or role in result:raise ValueError('文件类型指定重复，原文和译文请各选一个文件')
        result[role]=f['path']
    for role,path in result.items():
        # Revalidate before committing; a dropped file can disappear while choosing.
        plan_files([path],role=role)
    return result


def apply_imported_appearance(project, ids=None):
    """Atomically initialize appearance on newly imported, provenance-linked rows."""
    candidate = deepcopy(project)
    applied = _apply_imported_appearance(candidate, ids)
    candidate.validate_timeline()
    project.settings = candidate.settings
    project.warnings = candidate.warnings
    by_id = {row.id: row for row in candidate.mappings}
    for row in project.mappings:
        other = by_id[row.id]
        for key in ('speaker_name', 'speaker_id', 'speaker_style', 'style_id', 'style_overrides'):
            setattr(row, key, deepcopy(getattr(other, key)))
    return applied


def _apply_imported_appearance(project, ids=None):
    """Copy immutable ASS evidence to explicitly newly seeded timeline rows.

    Call inside the existing import transaction after links are initialized.
    Never infer identity from text/index, and never overwrite edited appearance.
    Actor/style conflicts on a many-to-one mapping remain visible warnings.
    """
    from .styles import validate_profiles, validate_overrides
    from .speakers import default_style, style_for
    translations = {row.id: row for row in project.translations}
    lyrics = {row.id: row for row in project.lyrics}
    wanted = set(ids) if ids is not None else {row.id for row in project.mappings}
    applied = []
    overflow_rows=[]
    profiles=validate_profiles(project.settings.get('subtitle_styles',{}))
    imported_styles={}
    for row in project.mappings:
        if row.id not in wanted or row.speaker_name or row.speaker_id or row.style_id or row.speaker_style or row.style_overrides:
            continue
        sources = [translations[key] for key in row.translation_ids if key in translations]
        if not sources: sources = [lyrics[key] for key in row.lyric_ids if key in lyrics]
        evidence = [source.subtitle_metadata for source in sources if source.subtitle_metadata]
        if not evidence: continue
        if any(value != evidence[0] for value in evidence[1:]):
            project.warnings.append('导入行合并后包含不同的说话人或样式，请手动选择：'+row.id)
            continue
        metadata = evidence[0]
        actor = metadata.get('actor', '')
        style_name = metadata.get('style_name', '')
        if style_name:
            style = metadata.get('style') or default_style(project)
            # Reuse an exact named definition; a same-name/different-style import
            # receives a new ID so existing references are never overwritten.
            profile = validate_profiles({'incoming': {'name': style_name, 'style': style}})['incoming']
            signature=json.dumps(profile,sort_keys=True,ensure_ascii=False)
            source_id=metadata.get('style_source_id')
            key=(source_id,signature)
            if key in imported_styles: identity=imported_styles[key]
            else:
                identity=source_id or 'style-'+hashlib.sha256(signature.encode('utf-8')).hexdigest()[:32]
                if not source_id: identity=next((key for key,value in profiles.items() if value==profile),identity)
                if identity in profiles and profiles[identity] != profile: identity='style-'+uuid.uuid4().hex
                imported_styles[key]=identity
            if identity not in profiles and len(profiles)>=256:
                # dev8 exported one SpeakerCueN definition per row. Do not
                # reject a whole valid import or merge independent names just
                # because their five supported appearance attributes coincide.
                row.speaker_style=deepcopy(profile['style'])
                overflow_rows.append(row.id)
            else:
                profiles[identity] = profile
                row.style_id = identity
        if actor:
            if not isinstance(actor,str) or not actor.strip() or len(actor)>80 or any(ord(c)<32 for c in actor):
                raise ValueError('ASS 说话人名称无效。')
            row.speaker_name = actor
        row.style_overrides = validate_overrides(metadata.get('overrides', {}))
        if row.style_id or row.style_overrides: row.speaker_style = style_for(project, row, profiles=profiles)
        project.warnings.extend(metadata.get('warnings', []))
        applied.append(row.id)
    if profiles:project.settings['subtitle_styles']=validate_profiles(profiles)
    if overflow_rows:
        project.warnings.append(f'本次导入有 {len(overflow_rows)} 句涉及超出工程 256 套命名样式容量的定义，'
            '已逐句保留为个人样式，不随命名样式联动；原样式名、说话人及导入记录仍保留，不同命名样式未被合并。')
    project.warnings = list(dict.fromkeys(project.warnings))
    return applied
