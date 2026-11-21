"""Validate NextFire occurrence identity before creating review-only candidates."""
from copy import deepcopy
import math
from .local_models import NEXTFIRE
from .model import Project
from .candidate_tracks import candidate_from_project


def candidate_from_nextfire(snapshot, rows, response, *, offset_seconds=0., audio_evidence=None):
    if response.get('model_id')!=NEXTFIRE.id or response.get('revision')!=NEXTFIRE.revision:
        raise ValueError('NextFire 模型版本与请求不一致。')
    def number(value):return type(value) in (int,float) and math.isfinite(value)
    duration=response.get('duration');aligned=response.get('rows')
    if not number(duration) or duration<=0 or not number(offset_seconds) or offset_seconds<0:
        raise ValueError('NextFire 音频时长无效。')
    if not isinstance(aligned,list) or len(aligned)!=len(rows):
        raise ValueError('NextFire 返回的原词行数不一致。')
    ids=[r['id'] for r in rows]
    if len(ids)!=len(set(ids)) or any(i not in snapshot['base_rows'] for i in ids):
        raise ValueError('NextFire 原词行标识无效。')
    last_end=0.
    for source,target in zip(rows,aligned):
        if any(target.get(key)!=source.get(key) for key in ('id','text')) or target.get('reading','')!=source.get('reading',''):
            raise ValueError('NextFire 返回的原词或读音与请求不一致。')
        if target.get('status')=='nonvocal_symbols':
            from .japanese_readings import pure_symbol_row
            if not pure_symbol_row(source) or target.get('start_time') is not None or target.get('end_time') is not None or target.get('token_spans'):
                raise ValueError('纯符号免对时结果不符合原文或包含伪造时间。')
            continue
        start,end=target.get('start_time'),target.get('end_time')
        if not number(start) or not number(end) or not 0<=start<end<=duration+.001 or start+.001<last_end:
            raise ValueError('NextFire 返回时间无效或倒退。')
        last_end=end
        score=target.get('path_score')
        if not number(score) or not 0<=score<=1:raise ValueError('NextFire 路径评分无效。')
    project=Project.from_dict(deepcopy(snapshot['project']))
    by_id={r['id']:r for r in aligned}
    project.mappings=[r for r in project.mappings if r.id in by_id]
    sources={r['id']:r for r in rows}
    for row in project.mappings:
        target=by_id[row.id]
        if target.get('status')=='nonvocal_symbols':
            row.start=row.end=None;row.alignment_tokens=[];row.method='nonvocal_symbols'
            row.reasons=['纯符号/颜文字免对时，原行保留且不制造时间。'];continue
        row.start=float(target['start_time'])+offset_seconds
        row.end=float(target['end_time'])+offset_seconds
        compact=target.get('token_spans',[])
        if compact:
            from .alignment_tokens import validate_alignment_tokens
            model_reading=target.get('model_reading','');step=target.get('frame_seconds')
            if not isinstance(compact,list) or len(compact)!=len(model_reading) or not number(step) or step<=0:
                raise ValueError('NextFire token evidence identity is invalid')
            owners=[]
            from .experimental_nextfire_alignment import normalize_reading
            if model_reading!=normalize_reading(sources[row.id]['reading']):
                raise ValueError('Model label text differs from requested reading')
            for unit in sources[row.id].get('alignment_units',[]):
                label=unit.get('reading','')
                if label:owners.extend([unit]*len(normalize_reading(label)))
            if owners and len(owners)!=len(compact):raise ValueError('Reading ownership no longer matches model labels')
            tokens=[];previous_frame=0
            for i,(label,span) in enumerate(zip(model_reading,compact)):
                if not isinstance(span,list) or len(span)!=4:raise ValueError('Invalid compact token evidence')
                token_id,a,b,score=span
                if any(type(v) is not int for v in (token_id,a,b)) or a<previous_frame or b<=a:
                    raise ValueError('Raw CTC token frame order is invalid')
                previous_frame=b
                owner=owners[i] if owners else {}
                tokens.append(dict(label=label,token_id=token_id,start=a*step+offset_seconds,end=b*step+offset_seconds,
                    start_frame=a,end_frame=b,score=score,source_row_id=row.id,reading_index=i,
                    source_start=owner.get('source_start'),source_end=owner.get('source_end'),ownership=owner.get('ownership','unknown')))
            validate_alignment_tokens(tokens)
            if any(t['start']<offset_seconds or t['end']>duration+offset_seconds+.001 for t in tokens):raise ValueError('Token evidence outside audio')
            row.alignment_tokens=tokens
        row.confidence=0. # Path probability is not calibrated correctness confidence.
        row.method='local_vocal_nextfire'
        row.reasons=['local-model:'+NEXTFIRE.repo+'@'+NEXTFIRE.revision,
            'NextFire 实验性强制对齐候选，须结合试听核对后应用',
            '有时间不代表该句确实唱出；路径分数不等于正确率',
            '对齐读音：'+target['reading'],
            f"未校准路径分数：{target['path_score']:.6g}"]
        if audio_evidence:row.reasons.append('audio-sha256:'+audio_evidence['sha256'])
    return candidate_from_project(snapshot,project)
