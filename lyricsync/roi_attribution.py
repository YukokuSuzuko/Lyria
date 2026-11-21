"""Attribute OCR rows to user-assigned region roles; manual attributes take precedence."""
from .styles import project_profiles
from .speakers import region_name, speaker_name

KEY = 'ocr_region_attributes'
FIELDS = ('speaker_name', 'style_id')


def protect(project, ids, *fields):
    history = project.settings.get(KEY, {})
    for identity in ids:
        entry = history.get(identity, {})
        for field in fields:
            entry.pop(field, None)
            if field in ('speaker_name', 'speaker_id'):
                entry.pop('speaker_name', None)
                entry.pop('speaker_id', None)


def source_regions(row, visuals):
    found = {}
    for identity in row.visual_ids:
        v = visuals.get(identity)
        if v is None:
            return {}
        owner = v.evidence.get('roi_owner')
        configs = v.evidence.get('roi_configuration', [])
        origins = [owner] if owner else v.evidence.get('roi_ids', [])
        if not origins:
            return {}
        for tag in origins:
            region = next((r for r in configs if r.get('id') == tag), None)
            if not region:
                return {}
            found[tag] = region
    return found


def inherit(project, rows, *, newly_applied=False):
    if not any(v.evidence.get('roi_configuration') for v in project.visuals):
        return
    visuals = {v.id: v for v in project.visuals}
    styles = project_profiles(project)
    for row in rows:
        regions = source_regions(row, visuals)
        if not regions or row.locked or row.confirmed or (row.human_edited and not newly_applied):
            continue
        memo = project.settings.setdefault(KEY, {})
        if row.id in memo:
            continue
        record: dict[str, object] = {'regions': sorted(regions)}
        memo[row.id] = record
        for field in FIELDS:
            if (speaker_name(project, row) if field == 'speaker_name' else row.style_id or row.speaker_style or row.style_overrides):
                continue
            choices = {region_name(project, r) if field == 'speaker_name' else r.get(field, '') for r in regions.values()}
            choice = next(iter(choices)) if len(choices) == 1 else ''
            if field == 'style_id' and choice == 'default':
                choice = ''
            if field == 'style_id' and choice and choice not in styles:
                continue
            setattr(row, field, choice)
            record[field] = choice


def sync(project, regions, changes):
    current = {r['id']: r for r in regions}
    memo = project.settings.get(KEY, {})
    updated = 0
    for row in project.mappings:
        note = memo.get(row.id, {})
        origins = note.get('regions', [])
        if (row.locked or row.confirmed or row.id in project.settings.get('manual_locked_ids', [])
                or not changes.keys() & set(origins) or not all(r in current for r in origins)):
            continue
        changed = False
        for field in FIELDS:
            if not any(field in changes.get(r, ()) or field == 'speaker_name' and 'speaker_id' in changes.get(r, ()) for r in origins):
                continue
            if field not in note or getattr(row, field) != note[field]:
                continue
            choices = {region_name(project, current[r]) if field == 'speaker_name' else current[r].get(field, '') for r in origins}
            value = next(iter(choices)) if len(choices) == 1 else ''
            if field == 'style_id' and value == 'default':
                value = ''
            if value != note[field]:
                if field == 'speaker_name': row.speaker_id = ''
                setattr(row, field, value)
                note[field] = value
                changed = True
        updated += changed
    return updated
