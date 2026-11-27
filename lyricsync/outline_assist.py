"""Opt-in local outline boundary proposals. Never invent text or unidentified timing."""
from .vision import Video,edge_mask,patch,visibility_score
from .model import valid_time


def unreliable_ids(project):
    visuals={v.id:v for v in project.visuals}
    return [m.id for m in project.mappings if not m.locked and (
        not m.visual_ids or not valid_time(m.start,m.end) or
        any(v not in visuals or visuals[v].confidence<.75 or visuals[v].warnings for v in m.visual_ids))]


def outline_boundary(video,visual,threshold=.62,cancel=lambda:False):
    seed=float(visual.evidence.get('seed_time',(visual.start+visual.end)/2))
    seed_stamp,frame=video.at(seed)
    if frame is None:return {'reason':'没有可用的文字轮廓参考帧'}
    template=edge_mask(patch(frame,visual.box))
    if template.size==0 or (template>0).sum()<20:return {'reason':'选区内文字边缘不够清晰，无法追踪'}
    left=max(0,min(seed,visual.start)-2);right=min(video.info['duration'],max(seed,visual.end)+2)
    if right-left>40:return {'reason':'候选区间过长，请先缩小到单句歌词'}
    observations=[]
    for stamp,image in video.frames(left,right,cancel):
        observations.append((stamp,visibility_score(template,image,visual.box)))
    if not observations:return {'reason':'候选区间没有可用画面'}
    anchor=min(range(len(observations)),key=lambda i:abs(observations[i][0]-seed_stamp))
    if observations[anchor][1]<threshold:return {'reason':'文字轮廓无法稳定跟踪'}
    first=last=anchor
    while first>0 and observations[first-1][1]>=threshold:first-=1
    while last+1<len(observations) and observations[last+1][1]>=threshold:last+=1
    if (first==0 and left>0) or (last==len(observations)-1 and right<video.info['duration']):
        return {'reason':'文字持续超出搜索范围，请手动定位起止帧'}
    start=observations[first][0]
    end=observations[last+1][0] if last+1<len(observations) else video.info['duration']
    if not valid_time(start,end):return {'reason':'未能检出有效的连续显示时间'}
    return {'start':start,'end':end,'reason':'已按文字轮廓估算起止时间，渐变或切换处请结合画面核对。',
            'seed':seed_stamp,'threshold':threshold,'box':visual.box,'confidence':.5}


def propose(project,ids,cache,progress=lambda p,m:None,cancel=lambda:False):
    video=Video(project.inputs['video']);visuals={v.id:v for v in project.visuals}
    result=[]
    mappings=[m for m in project.mappings if m.id in ids and not m.locked]
    for number,m in enumerate(mappings):
        if cancel():raise InterruptedError('已取消轮廓辅助检测')
        candidates=[]
        for identity in m.visual_ids:
            if identity not in visuals:continue
            v=visuals[identity]
            key=cache.key('outline-v1',{'video':project.video.get('content_hash',project.inputs['video']),
                'visual':v.id,'box':v.box,'start':v.start,'end':v.end,'seed':v.evidence.get('seed_time'),'threshold':.62})
            value=cache.get(key)
            if value is None:value=outline_boundary(video,v,cancel=cancel);cache.put(key,value)
            candidates.append(value)
        success=candidates and all('start' in c for c in candidates)
        if success:
            ordered=sorted(candidates,key=lambda c:c['start']);frontier=ordered[0]['end']
            for row in ordered[1:]:
                if row['start']>frontier+1:success=False
                frontier=max(frontier,row['end'])
        row={'mapping_id':m.id,'reason':'缺少文字位置参考，请手动定位起止时间。'}
        if success:
            row.update(start=min(c['start'] for c in candidates),end=max(c['end'] for c in candidates),
                       confidence=.5,reason='已生成轮廓辅助时间，可在时间工具中选用并核对。')
        elif candidates:row['reason']='；'.join(dict.fromkeys(c['reason'] for c in candidates))
        result.append(row)
        progress((number+1)/max(1,len(mappings)),f'轮廓辅助检测 {number+1}/{len(mappings)}')
    return result
