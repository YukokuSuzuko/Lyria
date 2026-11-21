"""Layered OCR/source/translation graph and atomic, time-preserving corrections.

Project's immutable entity tables retain observations and imported text. Mappings
are the compatibility/export projection; graph_snapshot describes independent
edges without flattening observations into translated text.
"""
from copy import deepcopy
from difflib import SequenceMatcher
from .model import Project, normalized, valid_time
from .editing import mapping_text

GRAPH_VERSION=1


def _text(rows,ids):
    lookup={r.id:r.text for r in rows}
    return '\n'.join(lookup[i] for i in ids if i in lookup)


def graph_snapshot(project, frame_index=None):
    anchors=set(project.settings.get('pairing_manual_anchors',[]))
    canonical=project.alignment_graph.to_dict()
    cues=[];vt=canonical['visual_source_edges'];lt=canonical['source_translation_edges']
    for row in project.mappings:
        edges=[project.alignment_graph.visual_source_edges[row.id],project.alignment_graph.source_translation_edges[row.id]]
        edge_states={edge['state'] for edge in edges}
        review_confidence=min([row.confidence]+[edge['confidence'] for edge in edges])
        state=('uncertain' if 'uncertain' in edge_states else 'manual_locked' if edge_states=={'manual_locked'}
               else 'auto_high' if review_confidence>=.85 else 'uncertain')
        cues.append(dict(id=row.id,start=row.start,end=row.end,text=mapping_text(project,row),
            visual_ids=list(row.visual_ids),lyric_ids=list(row.lyric_ids),translation_ids=list(row.translation_ids),state=state,review_confidence=review_confidence))
    bound_l={i for r in project.mappings for i in r.lyric_ids};bound_t={i for r in project.mappings for i in r.translation_ids}
    return dict(version=GRAPH_VERSION,
        observations=[dict(id=v.id,start=v.start,end=v.end,interval='half-open-seconds',box=list(v.box),
            line_slot=v.evidence.get('line_slot'),status=v.evidence.get('observation_status','normal'),
            eligible=v.evidence.get('eligible_for_alignment',True)) for v in project.visuals],
        lyric_instances=[dict(id=l.id,instance_index=i,paragraph=l.subtitle_metadata.get('paragraph_index'),is_interjection=l.is_interjection) for i,l in enumerate(project.lyrics)],
        translation_nodes=[dict(id=t.id,instance_index=i,paragraph=t.subtitle_metadata.get('paragraph_index')) for i,t in enumerate(project.translations)],
        visual_source_edges=vt,source_translation_edges=lt,cues=cues,
        unbound_lyrics=[l.id for l in project.lyrics if l.id not in bound_l],
        unbound_translations=[t.id for t in project.translations if t.id not in bound_t],
        diagnostics=diagnose(project,frame_index))


def diagnose(project,index=None):
    lyrics={l.id:l for l in project.lyrics};visuals={v.id:v for v in project.visuals}
    positions={l.id:i for i,l in enumerate(project.lyrics)};results=[];observed=[]
    # Report observations, not invented confidence or automatic timing edits.
    from .ocr_spatial import same_slot
    def boundary_signals(vs):
        signals=[]
        for v in vs:
            for edge in ('start','end'):
                bracket=getattr(v,edge+'_bracket',[])
                if len(bracket)>=2 and bracket[-1]-bracket[0]>.08:
                    signals.append(dict(code='boundary_uncertain',visual_id=v.id,edge=edge,
                                        bracket=list(bracket),width_seconds=bracket[-1]-bracket[0]))
            if index is None or len(v.box)!=4:continue
            for other in project.visuals:
                if other.id==v.id or len(other.box)!=4 or not same_slot(v.box,other.box):continue
                overlap=max(0,min(v.box[2],other.box[2])-max(v.box[0],other.box[0]))
                if overlap/max(1,min(v.box[2]-v.box[0],other.box[2]-other.box[0]))<.6:continue
                before,after=(v,other) if v.start<=other.start else (other,v)
                if after.start<before.end-1e-6:continue  # genuine overlap is not a gap
                # Only actual decoded PTS can establish a 1–3-frame interruption.
                if before.end<index.stamps[0] or after.start>index.stamps[-1]:continue
                gap=index.first(after.start)-index.last(before.end)-1
                if not 0<=gap<=3:continue
                x,y=normalized(v.text),normalized(other.text)
                if min(len(x),len(y))<5:continue
                matcher=SequenceMatcher(None,x,y,autojunk=False);sim=matcher.ratio()
                if sim<.85:continue
                base=dict(visual_id=v.id,neighbor_visual_id=other.id,gap_frames=gap,
                          gap_seconds=after.start-before.end,text_similarity=round(sim,3),
                          boxes=[list(v.box),list(other.box)],basis='decoded_pts_and_observed_boxes')
                if 1<=gap<=3:signals.append(dict(code='boundary_short_gap',**base))
                edits=sum(max(i2-i1,j2-j1) for op,i1,i2,j1,j2 in matcher.get_opcodes() if op!='equal')
                if 1<=edits<=2:
                    signals.append(dict(code='boundary_text_flicker',observed_texts=[v.text,other.text],
                                        differing_characters=edits,**base))
        return signals
    for number,row in enumerate(project.mappings):
        codes=[]
        edge_values=[project.alignment_graph.visual_source_edges[row.id],project.alignment_graph.source_translation_edges[row.id]]
        effective_confidence=min([row.confidence]+[edge['confidence'] for edge in edge_values])
        if effective_confidence<.7:codes.append('low_confidence')
        if any(edge['state']=='uncertain' for edge in edge_values):codes.append('uncertain_graph_edge')
        if len(row.lyric_ids)!=1 or len(row.visual_ids)!=1 or len(row.translation_ids)>1:codes.append('split_merge_or_unbound')
        if not valid_time(row.start,row.end):codes.append('unlocated')
        elif index is not None and index.last(row.end)-index.first(row.start)+1<5:codes.append('short_under_5_frames')
        elif row.end-row.start<.1:codes.append('short_under_100ms')
        vs=[visuals[i] for i in row.visual_ids if i in visuals]
        if any(v.evidence.get('observation_status')=='transition_noise' for v in vs):codes.append('transition_noise')
        source=_text(project.lyrics,row.lyric_ids);ocr=''.join(v.text for v in vs)
        score=SequenceMatcher(None,normalized(ocr),normalized(source),autojunk=False).ratio() if ocr and source else 0.
        signals=boundary_signals(vs) if vs and source and score>=.85 else []
        codes.extend(dict.fromkeys(item['code'] for item in signals))
        observed.append((ocr,positions.get(row.lyric_ids[0]) if len(row.lyric_ids)==1 else None,score))
        if codes:
            result=dict(cue_id=row.id,row=number,codes=list(dict.fromkeys(codes)),text_score=round(score,3))
            if signals:result['boundary_evidence']=signals
            results.append(result)
    # Consecutive ±1/±2 similarity peaks diagnose a local slip, not an auto-fix.
    for start in range(max(0,len(observed)-2)):
        window=observed[start:start+3]
        if any(not text or i is None for text,i,_ in window):continue
        base=sum(s for _,_,s in window)/3
        for shift in (-2,-1,1,2):
            if any(not 0<=i+shift<len(project.lyrics) for _,i,_ in window):continue
            shifted=sum(SequenceMatcher(None,normalized(text),normalized(project.lyrics[i+shift].text),autojunk=False).ratio() for text,i,_ in window)/3
            if base<.65 and shifted>.82 and shifted-base>.25:
                results.append(dict(cue_id=project.mappings[start].id,row=start,codes=['suspected_local_slip'],shift=shift,span=3,
                    current_score=round(base,3),shifted_score=round(shifted,3)))
    return results


def _protected(project,row):
    return (project.alignment_graph.manual_locked(row.id) or bool(row.text_segments) or row.locked or row.confirmed or row.id in project.settings.get('manual_locked_ids',[]) or
            row.id in project.settings.get('pairing_manual_anchors',[]) or
            (row.human_edited and not row.method.startswith('candidate_')))


def _paragraph(project,row,column):
    table=project.lyrics if column=='source' else project.translations
    ids=row.lyric_ids if column=='source' else row.translation_ids
    lookup={x.id:x for x in table}
    values={lookup[i].subtitle_metadata.get('paragraph_index') for i in ids if i in lookup}
    values.discard(None)
    return next(iter(values)) if len(values)==1 else None


def _payload(project,row,column):
    if column=='source':return dict(ids=list(row.lyric_ids))
    return dict(ids=list(row.translation_ids),text=mapping_text(project,row),role=row.text_role)


def _put(row,column,value):
    if column=='source':row.lyric_ids=list(value['ids'])
    else:
        row.translation_ids=list(value['ids']);row.text_override=value.get('text','');row.text_role=value.get('role','translation')


def correction(project, *, operation, column='source', cue_ids=(), node_ids=()):
    """Return a validated draft; no evidence/time mutation or partial changes.

    Each affected cue becomes a manual mapping anchor. A slip stops BEFORE the
    next manual/locked/confirmed cue or explicit paragraph boundary. The item
    displaced from the end is retained in a recovery tray, never deleted.
    """
    if column not in ('source','translation'):raise ValueError('请选择原词列或译文列。')
    if not cue_ids:raise ValueError('请先选择时间段。')
    draft=Project.from_dict(deepcopy(project.to_dict()));rows=draft.mappings;by_id={r.id:r for r in rows}
    if any(i not in by_id for i in cue_ids) or len(cue_ids)!=len(set(cue_ids)):raise ValueError('所选时间段无效。')
    chosen=[by_id[i] for i in cue_ids]
    if any(r.text_segments or r.locked or r.confirmed or r.id in draft.settings.get('manual_locked_ids',[]) for r in chosen):raise ValueError('所选句已锁定或确认，请先解锁；未改动任何关联。')
    before=[(r.id,r.start,r.end,deepcopy(r.text_segments)) for r in rows]
    affected=[];tray=draft.settings.setdefault('pairing_unbound_tray',[])
    if operation in ('slip_down','slip_up'):
        start=rows.index(chosen[0]);segment=[rows[start]];para=_paragraph(draft,rows[start],column)
        for r in rows[start+1:]:
            p=_paragraph(draft,r,column)
            if _protected(draft,r) or (para is not None and p is not None and p!=para):break
            segment.append(r)
        if len(segment)<2:raise ValueError('下一句已是锁定锚点或段落边界，没有可滑移区间。')
        values=[_payload(draft,r,column) for r in segment]
        displaced=values[-1] if operation=='slip_down' else values[0]
        if displaced.get('ids') or displaced.get('text'):tray.append(dict(column=column,payload=displaced,from_cue=segment[-1 if operation=='slip_down' else 0].id))
        moved=([dict(ids=[],text='',role=values[0].get('role','translation'))]+values[:-1] if operation=='slip_down' else values[1:]+[dict(ids=[],text='',role=values[-1].get('role','translation'))])
        for r,value in zip(segment,moved):_put(r,column,value)
        affected=segment
    elif operation=='bind':
        allowed={x.id for x in (draft.lyrics if column=='source' else draft.translations)}
        if not node_ids or len(node_ids)!=len(set(node_ids)) or any(i not in allowed for i in node_ids):raise ValueError('请选择有效的原词或译文实例。')
        if len(chosen)>1 and sorted(rows.index(r) for r in chosen)!=list(range(min(rows.index(r) for r in chosen),max(rows.index(r) for r in chosen)+1)):raise ValueError('请选择连续时间段。')
        for r in chosen:
            old=_payload(draft,r,column)
            if old.get('ids') and old['ids']!=list(node_ids):tray.append(dict(column=column,payload=old,from_cue=r.id))
            _put(r,column,dict(ids=list(node_ids),text=_text(draft.translations,node_ids) if column=='translation' else '',role='translation'))
        affected=chosen
    elif operation=='merge_next':
        start=rows.index(chosen[0])
        if start+1>=len(rows) or _protected(draft,rows[start+1]):raise ValueError('下一句是锚点或不存在，未合并。')
        a,b=rows[start:start+2];pa,pb=_payload(draft,a,column),_payload(draft,b,column)
        para_a,para_b=_paragraph(draft,a,column),_paragraph(draft,b,column)
        if para_a is not None and para_b is not None and para_a!=para_b:raise ValueError('不能跨显式段落合并。')
        _put(a,column,dict(ids=list(dict.fromkeys(pa['ids']+pb['ids'])),text='\n'.join(x for x in (pa.get('text',''),pb.get('text','')) if x),role=pa.get('role','translation')))
        _put(b,column,dict(ids=[],text='',role=pb.get('role','translation')));affected=[a,b]
    elif operation=='restore_tray':
        if not tray:raise ValueError('没有被滑出的关联。')
        item=tray[-1]
        if item['column']!=column:raise ValueError('请先切换到暂存项所属列。')
        old=_payload(draft,chosen[0],column)
        if old.get('ids') or old.get('text'):raise ValueError('请选择该列为空的时间段，再恢复暂存项。')
        _put(chosen[0],column,item['payload']);tray.pop();affected=[chosen[0]]
    else:raise ValueError('未知配对操作。')
    anchors=draft.settings.setdefault('pairing_manual_anchors',[])
    for r in affected:
        if r.id not in anchors:anchors.append(r.id)
        r.human_edited=True;r.confirmed=False
        r.reasons=list(dict.fromkeys(r.reasons+['人工调整文本关联；原始时间证据未改动']))
    assert before==[(r.id,r.start,r.end,r.text_segments) for r in rows]
    assert project.to_dict()['visuals']==draft.to_dict()['visuals']
    draft.validate_timeline()
    return draft


def visual_groups(project):
    """Read-only connected groups, with explicit occurrence IDs and edge states."""
    rows=list(project.mappings);parent=list(range(len(rows)))
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    seen={}
    for i,row in enumerate(rows):
        for kind,ids in (('visual',row.visual_ids),('source',row.lyric_ids),('translation',row.translation_ids)):
            for identity in ids:
                key=(kind,identity)
                if key in seen:parent[root(i)]=root(seen[key])
                else:seen[key]=i
    groups={}
    for i,row in enumerate(rows):groups.setdefault(root(i),[]).append(row)
    result=[]
    for number,group in enumerate(groups.values(),1):
        ids=[r.id for r in group]
        states=sorted({table[i]['state'] for table in (project.alignment_graph.visual_source_edges,project.alignment_graph.source_translation_edges) for i in ids})
        result.append(dict(id='G'+str(number),cue_ids=ids,
            visual_ids=list(dict.fromkeys(v for r in group for v in r.visual_ids)),
            lyric_ids=list(dict.fromkeys(v for r in group for v in r.lyric_ids)),
            translation_ids=list(dict.fromkeys(v for r in group for v in r.translation_ids)),states=states))
    return result
