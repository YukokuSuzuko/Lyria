"""Reversible subtitle edits; user translation records remain immutable."""
from copy import copy, deepcopy
from dataclasses import asdict
from contextlib import contextmanager
import uuid
import math
from .model import Mapping, Project, valid_time
from .timebase import validate_boundaries


def segments(project, mapping, lookup=None):
    if lookup is None:lookup={t.id:t.text for t in project.translations}
    return deepcopy(mapping.text_segments) if mapping.text_segments else [
        dict(id=i,start=0,end=len(lookup[i])) for i in mapping.translation_ids if i in lookup]


def mapping_text(project, mapping, lookup=None):
    if mapping.text_override is not None:return mapping.text_override
    if lookup is None:lookup={t.id:t.text for t in project.translations}
    return '\n'.join(lookup.get(s['id'],'')[s['start']:s['end']] for s in segments(project,mapping,lookup))


def pending_text_links(project):
    """Persistent directed edges between existing cues awaiting text allocation.

    A right ID of '' marks an orphaned empty cue after explicit deletion. It
    stays visible until the user fills it and explicitly marks it allocated.
    """
    return deepcopy(project.settings.get('pending_text_splits',[]))


def pending_text_ids(project):
    return {i for link in pending_text_links(project) for i in (link['left'],link['right']) if i}


def _store_pending(project,links):
    unique=[]
    paired={i for link in links if link['right'] for i in (link['left'],link['right'])}
    for link in links:
        if not link['right'] and link['left'] in paired:continue
        if link not in unique:unique.append(link)
    if unique:project.settings['pending_text_splits']=unique
    else:project.settings.pop('pending_text_splits',None)


def _replace_pending(project,ids,left,right):
    replace_pending_mappings(project,{identity:(left,right) for identity in ids})


def replace_pending_mappings(project,replacements):
    """Retarget all reviewed structural changes together, preserving other edges."""
    links=[]
    rows={m.id:m for m in project.mappings}
    for link in pending_text_links(project):
        a=replacements.get(link['left'],(link['left'],link['left']))[1]
        b=replacements.get(link['right'],(link['right'],link['right']))[0]
        if a==b:continue
        if a not in rows or (b and b not in rows):raise ValueError('待分配文字的关联记录已失效，请重试。')
        if not b and link['left'] in replacements and mapping_text(project,rows[a]).strip():continue
        links.append({'left':a,'right':b})
    pairs={(link['left'],link['right']) for link in links if link['right']}
    outgoing={};incoming={};neighbours={}
    for a,b in pairs:
        outgoing.setdefault(a,set()).add(b);incoming.setdefault(b,set()).add(a)
        neighbours.setdefault(a,set()).add(b);neighbours.setdefault(b,set()).add(a)
    branches={i for i,values in outgoing.items() if len(values)>1}|{i for i,values in incoming.items() if len(values)>1}
    # Explicitly merging rows can join formerly independent pending groups.
    # Only that now-connected component is put in current mapping order. This
    # changes workflow edges, never actual text, timing, IDs or provenance.
    components={};positions={m.id:n for n,m in enumerate(project.mappings)}
    for identity in branches:
        if identity in components:continue
        members=set();todo=[identity]
        while todo:
            current=todo.pop()
            if current in members:continue
            members.add(current);todo.extend(neighbours.get(current,()))
        ordered=tuple(sorted(members,key=positions.__getitem__))
        for member in members:components[member]=ordered
    normalized=[];written=set()
    for link in links:
        component=components.get(link['left'])
        if component:
            if component not in written:
                normalized.extend({'left':a,'right':b} for a,b in zip(component,component[1:]));written.add(component)
        else:normalized.append(link)
    _store_pending(project,normalized)


def _delete_pending(project,identity):
    links=pending_text_links(project)
    incoming=[e['left'] for e in links if e['right']==identity]
    outgoing=[e['right'] for e in links if e['left']==identity and e['right']]
    kept=[e for e in links if identity not in (e['left'],e['right'])]
    if incoming and outgoing:kept.extend({'left':a,'right':b} for a in incoming for b in outgoing)
    # Explicit deletion is allowed to remove text, but never hide a surviving
    # blank cue's pending state. The deleted original remains undoable.
    referenced={i for e in kept for i in e.values()}
    for i in incoming+outgoing:
        row=next(m for m in project.mappings if m.id==i)
        if i not in referenced and not mapping_text(project,row).strip():kept.append({'left':i,'right':''})
    _store_pending(project,kept)


def pending_pair(project,identity):
    links=pending_text_links(project)
    # On an empty downstream cue, prefer the incoming allocation so the user
    # starts with the nearest predecessor that actually owns the text.
    row=next(m for m in project.mappings if m.id==identity)
    incoming=next((e for e in links if e['right']==identity),None)
    outgoing=next((e for e in links if e['left']==identity),None)
    return incoming if incoming and not mapping_text(project,row).strip() else outgoing or incoming


def validate_pending_pair(project,left,right,*,require_timing=True):
    if {'left':left,'right':right} not in pending_text_links(project):raise ValueError('这两段字幕已无需分配文字，请重新选择。')
    rows={m.id:m for m in project.mappings}
    a=rows.get(left);b=rows.get(right) if right else None
    if a is None or (right and b is None):raise ValueError('待分配字幕已改变。')
    if any(i in project.settings.get('manual_locked_ids',[]) for i in (left,right) if i):raise ValueError('请先解锁这两段字幕后再分配文字。')
    if b is not None and (project.mappings.index(b)!=project.mappings.index(a)+1
            or require_timing and (not valid_time(a.start,a.end) or not valid_time(b.start,b.end)
            or a.end>b.start+1e-6)):
        raise ValueError('两段已不相邻或时间重叠，请先调整时间或合并后重新切分。')
    return a,b


class History:
    def __init__(self,limit=60,context=lambda:None):
        self.undo_stack=[];self.redo_stack=[];self.limit=limit;self.group=None;self.group_recorded=False
        self.context=context;self.restored_context=None;self._transaction_active=False
    def _snapshot(self,project):
        state=deepcopy(project.to_dict())
        # UI selection belongs only to history, never to saved project settings.
        state['_history_context']=deepcopy(self.context())
        return state
    @contextmanager
    def transaction(self, project, label):
        """Edit a detached draft, then atomically commit one undoable command.

        Exceptions and no-ops leave the live project, undo and redo untouched.
        Do not notify the UI inside the transaction; notify once after commit.
        """
        if self.group is not None or self._transaction_active:
            raise RuntimeError('请先完成当前编辑操作后再执行此操作。')
        before = project.to_dict()
        draft = Project.from_dict(deepcopy(before))
        self._transaction_active=True
        try:
            yield draft
        finally:
            self._transaction_active=False
        after = draft.to_dict()
        if after == before:
            return
        # Roundtrip before push so an invalid draft cannot damage undo history.
        replacement = Project.from_dict(after)
        replacement.validate_timeline()
        self.push(project, label)
        project.__dict__.clear()
        project.__dict__.update(replacement.__dict__)

    def begin(self,label):self.group=label;self.group_recorded=False
    def end(self):self.group=None;self.group_recorded=False
    def push(self,project,label):
        if self.group:
            if self.group_recorded:return
            label=self.group;self.group_recorded=True
        self.undo_stack.append((label,self._snapshot(project)))
        self.undo_stack=self.undo_stack[-self.limit:];self.redo_stack=[]
    def restore(self,project,redo=False,*,validator=None):
        self.end()
        source,target=(self.redo_stack,self.undo_stack) if redo else (self.undo_stack,self.redo_stack)
        if not source:return project,None
        # Validate and capture both sides before touching either stack. A corrupt
        # entry or failed snapshot must leave the user's undo/redo path intact.
        label,state=source[-1]
        state=deepcopy(state);context=state.pop('_history_context',None)
        restored=Project.from_dict(state).validate_timeline()
        if validator is not None:restored=validator(restored)
        current=self._snapshot(project)
        source.pop();target.append((label,current))
        self.restored_context=context
        return restored,label


def split_mapping(project,mapping_id,character,cut=None):
    m=next(m for m in project.mappings if m.id==mapping_id)
    text=mapping_text(project,m)
    if cut is not None and (not isinstance(cut,(float,int)) or isinstance(cut,bool) or not math.isfinite(cut) or cut<0
            or (m.start is not None and cut<=m.start) or (m.end is not None and cut>=m.end)):
        raise ValueError('切分点必须在当前字幕的起止时间之内。')
    if valid_time(m.start,m.end) and cut is None:
        raise ValueError('切分已有时间的字幕时，请指定切分时间或帧号。')
    if not 0<character<len(text) or not text[:character].strip() or not text[character:].strip():
        raise ValueError('请在歌词中间选择拆分位置，前后都必须有正文。')
    left=[];right=[];offset=0
    for s in segments(project,m):
        length=s['end']-s['start'];local=max(0,min(length,character-offset))
        if local:left.append(dict(id=s['id'],start=s['start'],end=s['start']+local))
        if local<length:right.append(dict(id=s['id'],start=s['start']+local,end=s['end']))
        offset+=length+1
    if m.text_override is not None:
        left=segments(project,m);right=deepcopy(left)
    if m.text_override is None and (not left or not right):raise ValueError('请选择正文中的拆分位置。')
    a,b=deepcopy(m),deepcopy(m)
    for row,parts in ((a,left),(b,right)):
        row.id='split-'+uuid.uuid4().hex;row.text_segments=parts
        row.lineage_ids=list(dict.fromkeys(m.lineage_ids+[m.id]))
        row.translation_ids=list(dict.fromkeys(s['id'] for s in parts))
        row.locked=True;row.confirmed=False;row.human_edited=True;row.method='human_split'
    # Preserve literal text (including pre-existing line breaks) independently of provenance.
    a.text_override=text[:character];b.text_override=text[character:]
    a.end=b.start=cut
    from .alignment_token_editing import split_tokens
    a.alignment_tokens,b.alignment_tokens,ambiguous=split_tokens(m.alignment_tokens,cut)
    if ambiguous:
        a.reasons.append('跨拆分点逐字证据保留原始区间，字符归属待复核')
    index=project.mappings.index(m);project.mappings[index:index+1]=[a,b]
    _replace_pending(project,{m.id},a.id,b.id)
    return a,b


def merge_mappings(project,ids, *, order=None):
    rows=[m for m in project.mappings if m.id in ids]
    if len(rows)<2:raise ValueError('请按住 Ctrl 或 Shift 选中至少两条字幕后再合并。')
    if len({m.text_role for m in rows}) != 1:
        raise ValueError('演唱原文与译文行不能直接合并，请分别编辑。')
    from .speakers import speaker_name
    if len({(speaker_name(project,m), m.style_id, repr(sorted(m.style_overrides.items())),
             repr(sorted(m.speaker_style.items())) if not m.style_id else '') for m in rows}) != 1:
        raise ValueError('所选句的说话人或样式不同，请先明确统一后再合并。')
    # An explicit two-row text/time pairing need not be adjacent: untimed text
    # is displayed separately from timed clips. Keep the timed clip's identity
    # and position; no nonempty text or timing may be overwritten by this path.
    text_only=[m for m in rows if m.start is None and m.end is None and mapping_text(project,m).strip()]
    time_only=[m for m in rows if valid_time(m.start,m.end) and not mapping_text(project,m).strip()]
    if len(rows)==2 and len(text_only)==len(time_only)==1:
        source,target=text_only[0],time_only[0]
        combined=deepcopy(target)
        combined.lineage_ids=list(dict.fromkeys(i for i in target.lineage_ids+source.lineage_ids+[source.id] if i!=combined.id))
        combined.text_override=mapping_text(project,source)
        combined.text_segments=[]
        for part in segments(project,source)+segments(project,target):
            if part not in combined.text_segments:combined.text_segments.append(part)
        combined.translation_ids=list(dict.fromkeys(s['id'] for s in combined.text_segments))
        combined.lyric_ids=list(dict.fromkeys(target.lyric_ids+source.lyric_ids))
        combined.visual_ids=list(dict.fromkeys(target.visual_ids+source.visual_ids))
        combined.reasons=list(dict.fromkeys(target.reasons+source.reasons))
        from .alignment_token_editing import merged_tokens
        combined.alignment_tokens=merged_tokens([source,target])
        combined.confirmed=False;combined.locked=True;combined.human_edited=True;combined.method='human_pair'
        project.mappings=[combined if m.id==target.id else m for m in project.mappings if m.id!=source.id]
        _replace_pending(project,{source.id,target.id},target.id,target.id)
        return combined
    ordered=project.mappings
    if order is not None:
        lookup = {m.id:m for m in project.mappings}
        if len(order) != len(lookup) or set(order) != set(lookup):
            raise ValueError('字幕显示顺序已变化，请重试合并。')
        ordered = [lookup[i] for i in order]
    indexes=sorted(ordered.index(m) for m in rows)
    if indexes!=list(range(indexes[0],indexes[-1]+1)):raise ValueError('只能合并相邻的字幕，请勿跳过中间行。')
    rows=sorted(rows,key=ordered.index)
    combined=deepcopy(rows[0]);combined.id='merge-'+uuid.uuid4().hex
    combined.lineage_ids=list(dict.fromkeys(i for row in rows for i in row.lineage_ids+[row.id]))
    combined.text_segments=[s for m in rows for s in segments(project,m)]
    # Adjacent pieces of the same original line reconstruct that exact line.
    compact=[]
    for s in combined.text_segments:
        if compact and compact[-1]['id']==s['id'] and compact[-1]['end']==s['start']:
            compact[-1]['end']=s['end']
        else:compact.append(s)
    combined.text_segments=compact
    combined.text_override=''.join(mapping_text(project,m) for m in rows)
    combined.translation_ids=list(dict.fromkeys(s['id'] for s in compact))
    combined.lyric_ids=list(dict.fromkeys(i for m in rows for i in m.lyric_ids))
    combined.visual_ids=list(dict.fromkeys(i for m in rows for i in m.visual_ids))
    timed=[m for m in rows if valid_time(m.start,m.end)]
    known=lambda v:isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0
    combined.start=min((m.start for m in rows if known(m.start)),default=None)
    combined.end=max((m.end for m in rows if known(m.end)),default=None)
    if combined.start is not None and combined.end is not None and not valid_time(combined.start,combined.end):
        raise ValueError('所选字幕的起止时间冲突，请先调整时间边界后再合并。')
    combined.confidence=min(m.confidence for m in rows)
    combined.reasons=list(dict.fromkeys(r for m in rows for r in m.reasons))
    if len(timed)!=len(rows):combined.reasons.append('合并项中包含未打轴的句子，已沿用现有起止时间，请核对进出点')
    from .alignment_token_editing import merged_tokens
    combined.alignment_tokens=merged_tokens(rows)
    combined.confirmed=False;combined.locked=True;combined.human_edited=True;combined.method='human_merge'
    index=min(project.mappings.index(m) for m in rows)
    candidate=copy(project);candidate.settings=deepcopy(project.settings)
    candidate.mappings=[m for m in project.mappings if m.id not in ids]
    candidate.mappings.insert(index,combined)
    _replace_pending(candidate,set(ids),combined.id,combined.id)
    project.mappings=candidate.mappings;_store_pending(project,pending_text_links(candidate))
    return combined


class Editor:
    """UI-independent edits. All mutations validate before history/save notification."""
    def __init__(self,project,history,on_change=lambda:None):
        self.project,self.history,self.on_change=project,history,on_change

    def row(self,identity):
        return next(m for m in self.project.mappings if m.id==identity)

    def changed(self,row):
        row.human_edited=True;row.locked=True;row.confirmed=False
        self.on_change()

    def set_time(self,identity,**boundaries):
        row=self.row(identity)
        if not boundaries or set(boundaries)-{'start','end'}:raise ValueError('时间字段无效')
        start=boundaries.get('start',row.start);end=boundaries.get('end',row.end)
        validate_boundaries(start,end,duration=self.project.video.get('duration') or None)
        if (start,end)==(row.start,row.end):return False
        self.history.push(self.project,'修改字幕时间');row.start,row.end=start,end
        self.changed(row);return True

    def set_times(self, updates, label='批量修改时间'):
        """Apply ID-addressed timing edits as one atomic, reversible command."""
        if not isinstance(updates,dict):raise ValueError('时间修改参数格式无效。')
        changed=False
        with self.history.transaction(self.project,label) as draft:
            rows={m.id:m for m in draft.main_timeline}
            for identity,boundaries in updates.items():
                if identity not in rows:raise ValueError('该句字幕已不存在。')
                if identity in draft.settings.get('manual_locked_ids',[]):raise ValueError('此句已锁定，请先解锁再修改。')
                if not isinstance(boundaries,dict) or not boundaries or set(boundaries)-{'start','end'}:
                    raise ValueError('时间字段无效。')
                row=rows[identity]
                start,end=boundaries.get('start',row.start),boundaries.get('end',row.end)
                validate_boundaries(start,end,duration=draft.video.get('duration') or None)
                if (start,end)!=(row.start,row.end):
                    row.start,row.end=start,end
                    row.human_edited=True;row.locked=True;row.confirmed=False
                    changed=True
        if changed:self.on_change()
        return changed

    def move_frames(self,identity,delta,index):
        """Move both inclusive frame edges atomically, including on VFR media."""
        row=self.row(identity)
        if type(delta) is not int:raise ValueError('移动帧数必须为整数。')
        if identity in self.project.settings.get('manual_locked_ids',[]):raise ValueError('此句已锁定，请先解锁再修改。')
        if index is None:raise ValueError('请等待视频帧索引加载完成。')
        if not valid_time(row.start,row.end) or row.end>index.duration+1e-6:
            raise ValueError('当前句尚未设置有效的起止时间。')
        first,last=index.first(row.start),index.last(row.end)
        if first>last:raise ValueError('当前句没有有效帧区间。')
        shift=max(1-first,min(delta,len(index.stamps)-last))
        if not shift:return False
        start,end=index.interval(first+shift,last+shift)
        return self.set_time(identity,start=start,end=end)

    def set_text(self,identity,text):
        if not isinstance(text,str):raise ValueError('字幕文本格式无效')
        row=self.row(identity)
        if mapping_text(self.project,row)==text:return False
        self.history.push(self.project,'修改中文');row.text_override=text
        if row.text_role!='translation' and row.alignment_tokens:
            from .alignment_token_editing import unknown
            row.alignment_tokens=unknown(row.alignment_tokens)
            row.reasons.append('原词已编辑，保留逐字声学证据但需重新确认字符归属')
        self.changed(row);return True

    def confirm(self,identity,value=True,before_notify=None):
        row=self.row(identity)
        if value and (not valid_time(row.start,row.end) or not mapping_text(self.project,row).strip()):raise ValueError('请先补全字幕文字和起止时间')
        if row.confirmed==value and row.locked==value:return
        self.history.push(self.project,'确认字幕' if value else '取消确认')
        row.confirmed=value;row.locked=value
        if before_notify:before_notify()
        self.on_change()

    def delete_mapping(self,identity,before_notify=None):
        """Remove one cue from active timeline, retaining provenance and undo history."""
        row=self.row(identity)
        self.history.push(self.project,'删除当前字幕')
        self.project.settings.setdefault('deleted_mappings',[]).append(asdict(row))
        self.project.mappings.remove(row)
        _delete_pending(self.project,identity)
        if before_notify:before_notify()
        self.on_change()

    def insert_blank(self,after=None,fill_available_gap=True,*,before=None,before_notify=None):
        from .model import Translation
        if before is not None:
            index=next((i for i,m in enumerate(self.project.mappings) if m.id==before),None)
            if index is None:raise ValueError('当前字幕不存在。')
        else:
            index=next((i+1 for i,m in enumerate(self.project.mappings) if m.id==after),len(self.project.mappings))
        identity='manual-'+uuid.uuid4().hex
        anchor = next((m for m in self.project.mappings if m.id == (before or after)), None)
        role = anchor.text_role if anchor is not None else 'translation'
        # Explicitly selected current style affects only newly-created cues.
        # Resolve before changing history and link inside the same undo step.
        selected_style = {}
        selected_style_id = ''
        if self.project.settings.get('current_subtitle_style'):
            from .styles import project_profiles, current_style_id
            selected_style_id = current_style_id(self.project)
            selected_style = deepcopy(project_profiles(self.project)[current_style_id(self.project)]['style'])
        self.history.push(self.project,'新增空白字幕')
        if role == 'translation':self.project.translations.append(Translation(identity,''))
        row=Mapping(identity,[identity] if role == 'translation' else [],text_override='',text_role=role,
                    human_edited=True,locked=True,method='human_insert',speaker_style=selected_style,
                    style_id=selected_style_id)
        if fill_available_gap and 0 < index < len(self.project.mappings):
            prev,nxt=self.project.mappings[index-1:index+1]
            if (valid_time(prev.start,prev.end) and valid_time(nxt.start,nxt.end)
                    and prev.end < nxt.start
                    and not any(m is not prev and m is not nxt and valid_time(m.start,m.end)
                        and m.start < nxt.start and m.end > prev.end for m in self.project.mappings)):
                row.start,row.end=prev.end,nxt.start
                row.reasons=['已自动填入前后句空档时间，请填写字幕并核对进出点']
        self.project.mappings.insert(index,row)
        if before_notify:before_notify(row)
        self.on_change();return row

    def fill_gap(self,identity):
        row=self.row(identity);i=self.project.mappings.index(row)
        if i==0 or i==len(self.project.mappings)-1:raise ValueError('首尾字幕无法自动填补空档，需要前后都有相邻字幕')
        prev,nxt=self.project.mappings[i-1:i+2:2]
        if not valid_time(prev.start,prev.end) or not valid_time(nxt.start,nxt.end) or prev.end>=nxt.start:raise ValueError('前后相邻字幕之间没有空档，或相邻句缺少起止时间')
        if any(m is not row and m not in (prev,nxt) and valid_time(m.start,m.end) and m.start<nxt.start and m.end>prev.end for m in self.project.mappings):raise ValueError('空档已被其他字幕占用')
        self.set_time(identity,start=prev.end,end=nxt.start)
        row.reasons=list(dict.fromkeys(row.reasons+['已采用前后句空档时间，请结合试听或画面核对']))
        self.on_change()

    def add_marker(self,frame,index=None):
        if type(frame) is not int or frame<1:raise ValueError('标记点帧号必须从 1 开始')
        if index is not None:index.stamp(frame)
        markers=self.project.settings.get('frame_markers',[])
        if frame in markers:return
        self.history.push(self.project,'添加标记点')
        self.project.settings['frame_markers']=sorted(markers+[frame]);self.on_change()

    def delete_marker(self,frame):
        markers=self.project.settings.get('frame_markers',[])
        if frame not in markers:return
        self.history.push(self.project,'删除标记点')
        self.project.settings['frame_markers']=[f for f in markers if f!=frame];self.on_change()

    def delete_all_markers(self):
        if not self.project.settings.get('frame_markers',[]):return False
        self.history.push(self.project,'清空标记点')
        self.project.settings['frame_markers']=[];self.on_change();return True

    def split_at_marker(self,identity,character,frame,index,before_notify=None):
        if frame not in self.project.settings.get('frame_markers',[]):raise ValueError('该标记点不存在')
        candidate=Project.from_dict(self.project.to_dict())
        result=split_mapping(candidate,identity,character,index.stamp(frame))
        self.history.push(self.project,'按标记点切分字幕');self.project.mappings=candidate.mappings
        _store_pending(self.project,pending_text_links(candidate))
        if before_notify:before_notify(result)
        self.on_change();return result

    def razor_split(self,identity,frame,index,before_notify=None):
        row=self.row(identity)
        if identity in self.project.settings.get('manual_locked_ids',[]):raise ValueError('此句已锁定，请先解锁再修改。')
        if index is None:raise ValueError('请等待视频帧索引加载完成。')
        if type(frame) is not int:raise ValueError('切点必须是整数帧号。')
        if not valid_time(row.start,row.end) or row.end>index.duration+1e-6:raise ValueError('当前句尚未设置有效的起止时间。')
        first,last=index.first(row.start),index.last(row.end)
        if not first<frame<=last:raise ValueError('请在片段内部选择切点，前后都必须保留至少一帧。')
        cut=index.stamp(frame)
        if not row.start<cut<row.end:raise ValueError('切点不在字幕时间范围内。')
        candidate=Project.from_dict(self.project.to_dict());original=next(m for m in candidate.mappings if m.id==identity)
        a,b=deepcopy(original),deepcopy(original)
        for part in (a,b):
            part.id='split-'+uuid.uuid4().hex;part.locked=True;part.confirmed=False
            part.lineage_ids=list(dict.fromkeys(original.lineage_ids+[original.id]))
            part.human_edited=True;part.method='human_razor';part.text_segments=segments(candidate,original)
        a.text_override=mapping_text(candidate,original);b.text_override='';a.end=b.start=cut
        position=candidate.mappings.index(original);candidate.mappings[position:position+1]=[a,b]
        _replace_pending(candidate,{identity},a.id,b.id)
        _store_pending(candidate,pending_text_links(candidate)+[{'left':a.id,'right':b.id}])
        self.history.push(self.project,'剃刀切分时间');self.project.mappings=candidate.mappings
        _store_pending(self.project,pending_text_links(candidate))
        if before_notify:before_notify((a,b))
        self.on_change();return a,b

    def keep_pending_text(self,left,right):
        """Acknowledge exactly one split relationship, including intentional blank text.

        No cue fields (including confirmed/locked) change. Timing overlap does
        not prevent retaining existing text; the two IDs must still be adjacent.
        """
        validate_pending_pair(self.project,left,right,require_timing=False)
        self.history.push(self.project,'保留切分后文字')
        _store_pending(self.project,[e for e in pending_text_links(self.project) if e!={'left':left,'right':right}])
        self.on_change()

    def allocate_text(self,left,right,character=None,*,mark_done=False):
        a,b=validate_pending_pair(self.project,left,right)
        text=mapping_text(self.project,a)+(mapping_text(self.project,b) if b else '')
        if mark_done:
            if not mapping_text(self.project,a).strip() or (b and not mapping_text(self.project,b).strip()):
                raise ValueError('请先补全两段字幕的文字，再确认完成分配。')
        else:
            if b is None:raise ValueError('当前片段没有关联的待分字邻句，补全文字后可直接确认。')
            if type(character) is not int or not 0<character<len(text) or not text[:character].strip() or not text[character:].strip():
                raise ValueError('请在文字中间选择断点，前后两段都须包含正文。')
            from .graphemes import character_gaps
            if character not in character_gaps(text):raise ValueError('请在完整字符之间切分，不能拆开表情或组合字符。')
        self.history.push(self.project,'手动分配切分文字' if mark_done else '分配已切分字幕文字')
        if not mark_done:
            # Preserve the union of original translation provenance independently
            # of current edited text, exactly as the existing split/merge path.
            provenance=[]
            for segment in segments(self.project,a)+segments(self.project,b):
                if segment not in provenance:provenance.append(segment)
            for row,part in ((a,text[:character]),(b,text[character:])):
                row.text_override=part;row.text_segments=deepcopy(provenance)
                row.translation_ids=list(dict.fromkeys(s['id'] for s in provenance))
        if not mark_done:
            for row in (a,b):
                if row:row.confirmed=False;row.locked=True;row.human_edited=True
        _store_pending(self.project,[e for e in pending_text_links(self.project) if e!={'left':left,'right':right}])
        self.on_change()
