"""Review presentation and atomic selected-cue role/style operations."""
import hashlib
import colorsys
from lyricsync.speakers import assign_speaker, clear_speaker, speaker_name, speaker_names
from lyricsync.styles import project_profiles, current_style_id

def speaker_color(identity):
    if not identity:return ''
    # Identity-derived pastel, independent of assignment counts, renames or UI
    # order. Unlike an eight-color modulo palette this does not cycle every
    # few speakers. This is UI identification, never an export-style override.
    digest=hashlib.sha256(identity.encode()).digest()
    hue=int.from_bytes(digest[:4],'big')/2**32
    red,green,blue=colorsys.hls_to_rgb(hue,.62,.32)
    return '#%02X%02X%02X' % (round(red*255),round(green*255),round(blue*255))


def time_label(seconds):
    if seconds is None:return '—'
    return f'{int(seconds)//60}:{seconds%60:04.1f}'

def row_metadata(project,row,text):
    duration=max(0,row.end-row.start) if row.start is not None and row.end is not None else 0
    cps=sum(not c.isspace() for c in text)/duration if duration else 0
    default=current_style_id(project)
    name = speaker_name(project, row)
    return dict(speakerId=name,speakerColor=speaker_color(name),speakerDefault=not bool(name),
                specialStyle=bool(row.style_overrides or (row.style_id and row.style_id!=default) or (not row.style_id and row.speaker_style)),
                cps=round(cps,1),cpsWarning=bool(duration and cps>15),durationSeconds=duration,
                startSeconds=row.start if row.start is not None else -1,endSeconds=row.end if row.end is not None else -1,
                timeLabel=time_label(row.start)+'–'+time_label(row.end))

def choices(project):
    return [dict(id=name, name=name, color=speaker_color(name)) for name in speaker_names(project)]

def selection_state(bridge, items, position):
    rows = (items[bridge._row_positions[i]] for i in bridge._multi_selected) if bridge._multi_selected else (
        (items[position],) if position >= 0 else ())
    names = {row['speakerName'] for row in rows}
    name = next(iter(names)) if len(names) == 1 else ''
    return dict(name=name, color=speaker_color(name), mixed=len(names)>1,
                count=len(bridge._multi_selected) or int(position>=0))

def select_same(bridge, cue_id):
    if bridge._candidate_source != 'main' or bridge._alignment_preview.active:
        raise ValueError('请先回到主时间轴。')
    project = bridge.document.project
    name = speaker_name(project, bridge._row(cue_id))
    bridge._multi_selected = {row.id for row in project.mappings if speaker_name(project, row) == name}
    bridge._review_range_anchor = cue_id
    bridge._select_row(cue_id)

def selected_ids(bridge):
    return [r.id for r in bridge.document.project.mappings if r.id in bridge._multi_selected] or ([bridge.selected] if bridge.selected else [])

def guard(bridge,ids):
    if bridge._candidate_source!='main' or bridge._alignment_preview.active:raise ValueError('请先回到主时间轴。')
    if bridge._editor_active or bridge._editor_composing:raise ValueError('请先完成文字编辑。')
    if not ids:raise ValueError('请先选择句子。')
    p=bridge.document.project;rows={r.id:r for r in p.mappings}
    if any(i not in rows or i in p.settings.get('manual_locked_ids',[]) for i in ids):
        raise ValueError('所选句已改变或锁定，请先解锁。')

def assign(bridge,kind,payload):
    p=bridge.document.project;ids=([bridge.selected] if payload.get('currentOnly') else selected_ids(bridge));guard(bridge,ids)
    if kind=='speaker':
        identity=payload.get('id','');name=payload.get('name','')
        if identity and not name:
            if identity not in speaker_names(p): raise ValueError('所选说话人已不在本工程中。')
            name = identity
        if name:assign_speaker(p,bridge.history,ids,name)
        else:clear_speaker(p,bridge.history,ids)
    else:
        identity=payload.get('id');profiles=project_profiles(p)
        if identity not in profiles:raise ValueError('该样式已不存在。')
        # A quick assignment never silently clears personal overrides.
        _apply_style(p,bridge.history,ids,identity)
    bridge._changed()

def _apply_style(project,history,ids,identity):
    with history.transaction(project,'设置所选句的样式') as draft:
        from lyricsync.roi_attribution import protect
        protect(draft, ids, 'style_id')
        draft.settings.setdefault('subtitle_styles',project_profiles(draft))
        for row in draft.mappings:
            if row.id in ids:row.style_id=identity;row.human_edited=True

def select(bridge,payload):
    identity=payload.get('id');visible=[r['id'] for r in bridge.model.items]
    if identity not in visible:raise ValueError('所选句不在当前列表。')
    anchor=getattr(bridge,'_review_range_anchor',bridge.selected)
    if payload.get('shift') and anchor in visible:
        a,b=sorted((visible.index(anchor),visible.index(identity)))
        values=set(visible[a:b+1])
        bridge._multi_selected=(bridge._multi_selected|values) if payload.get('ctrl') else values
    elif payload.get('ctrl'):
        if not bridge._multi_selected and bridge.selected in visible:bridge._multi_selected.add(bridge.selected)
        if identity in bridge._multi_selected:bridge._multi_selected.remove(identity)
        else:bridge._multi_selected.add(identity)
        bridge._review_range_anchor=identity
    else:
        bridge._multi_selected.clear();bridge._review_range_anchor=identity
    if payload.get('ctrl') and identity not in bridge._multi_selected:
        identity = bridge.selected if bridge.selected in bridge._multi_selected else next((i for i in visible if i in bridge._multi_selected), '')
        if not identity:
            bridge.selected = ''; bridge._refresh(False); return
    if identity!=bridge.selected:bridge._select_row(identity)
    else:bridge._refresh(False)

def selection_scope(bridge,clear=False):
    if bridge._editor_active or bridge._editor_composing:return
    visible=[r['id'] for r in bridge.model.items]
    bridge._multi_selected=set() if clear else set(visible)
    bridge._refresh(False)


def shortcut(bridge,payload):
    if bridge._editor_active or bridge._editor_composing:return
    options=choices(bridge.document.project);number=payload.get('number',0)
    if type(number)is not int or not 0<=number<=9:return
    if number and number>len(options):return
    multiple = len(bridge._multi_selected) > 1
    assign(bridge, 'speaker', {'name': options[number-1]['name'] if number else '', 'currentOnly': not multiple})
    if multiple: return
    visible=[r['id'] for r in bridge.model.items]
    if bridge.selected in visible:
        n=visible.index(bridge.selected)
        bridge._multi_selected.clear()
        if n+1<len(visible):bridge._select_row(visible[n+1])
        else:bridge._refresh(False)
