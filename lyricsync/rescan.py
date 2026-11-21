"""Local OCR proposals are separate from user edits and can be explicitly adopted."""
from copy import deepcopy
from dataclasses import asdict
from uuid import uuid4
from .vision import scan, Video, ocr_similarity
from .model import Visual, valid_time


def suggested_window(project, identity, duration):
    """Search hints only: never assign these reference times to a subtitle."""
    row=next(m for m in project.mappings if m.id==identity)
    if valid_time(row.start,row.end):
        return max(0,row.start-1),min(duration,row.end+1)
    refs=[l for l in project.lyrics if l.id in row.lyric_ids and valid_time(l.start,l.end)]
    if refs:
        return max(0,min(l.start for l in refs)-2),min(duration,max(l.end for l in refs)+2)
    return None


def diagnose_candidates(visuals, refs):
    if not visuals:
        return [{'stage':'text_recognition','code':'no_reliable_text',
                 'reason':'所选范围内未识别出清晰文字，请检查选区位置、识别语言或搜索时间范围。'}]
    issues=[]
    if refs and max(ocr_similarity(v.text,r) for v in visuals for r in refs)<.6:
        issues.append({'stage':'source_matching','code':'source_mismatch',
                       'reason':'识别到了文字，但与本句原文差异较大；可能是相邻歌词、背景文字或识别错误。'})
    if any(not valid_time(v.start,v.end) or v.warnings for v in visuals):
        issues.append({'stage':'boundary_verification','code':'review_boundaries',
                       'reason':'识别结果的起止帧不够稳定，采用后请逐帧核对进出点。'})
    return issues


def rescan_cue(project, identity, cache, window=None, progress=lambda p,m:None,
               cancel=lambda:False, scanner=scan):
    row=next(m for m in project.mappings if m.id==identity)
    video=project.inputs.get('video')
    if not video:raise ValueError('请先载入视频')
    duration=project.video.get('duration') or Video(video).info['duration']
    if window is None:
        window=suggested_window(project,identity,duration)
        if window is None:raise ValueError('本句没有搜索范围，请指定视频起止帧后重新识别')
    if len(window)!=2 or not 0<=window[0]<window[1]<=duration:raise ValueError('搜索范围超出了视频总时长')
    settings=deepcopy(project.settings)
    from .multi_roi import rescan_settings, scan_regions
    settings=rescan_settings(project,row,settings)
    if settings.get('ocr_multi_region') is True and scanner is scan:
        scanner=scan_regions
    settings.update(scan_range=list(window),rescan_token=uuid4().hex)
    refs=[l.text for l in project.lyrics if l.id in row.lyric_ids]
    # No project mutation until scanning has finished successfully.
    visuals,info=scanner(video,settings,cache,refs,progress=progress,cancel=cancel)
    if cancel():raise InterruptedError('已取消单句重识别')
    issues=diagnose_candidates(visuals,refs)
    result={'status':'candidates' if visuals else 'no_reliable_text','window':list(window),
            'visuals':[asdict(v) for v in visuals],'metrics':info.get('ocr_metrics',{}),
            'diagnostics':issues,'reason':'\n'.join(r['reason'] for r in issues)}
    project.settings.setdefault('cue_rescans',{})[identity]=result
    return result


def adopt_rescan(editor,identity,visual_ids):
    project=editor.project;row=editor.row(identity)
    result=project.settings.get('cue_rescans',{}).get(identity,{})
    candidates={v['id']:v for v in result.get('visuals',[])}
    if not visual_ids or not set(visual_ids)<=candidates.keys():raise ValueError('请先选择要采用的识别结果')
    selected=[Visual(**candidates[i]) for i in dict.fromkeys(visual_ids)]
    if not all(valid_time(v.start,v.end) for v in selected):raise ValueError('所选识别结果的时间无效')
    editor.history.push(project,'采用本句重新识别结果')
    # New evidence IDs avoid changing references held by any other mapping.
    ids=[]
    for v in selected:
        v.id='rescan-'+uuid4().hex;project.visuals.append(v);ids.append(v.id)
    row.start=min(v.start for v in selected);row.end=max(v.end for v in selected)
    row.visual_ids=ids;row.confidence=min(v.confidence for v in selected)
    row.reasons=list(dict.fromkeys(['已采用单句重识别时间，请核对进出点']+[r for v in selected for r in v.warnings]))
    editor.changed(row)
