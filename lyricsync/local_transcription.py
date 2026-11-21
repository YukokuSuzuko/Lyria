"""Independent ASR draft -> explicit correction -> standard vocal candidate.

No project mutation or guessed timings. Recovered from the full prior source
excerpt; fresh tests are required after the source-recovery incident.
"""
from copy import deepcopy
import json
import math
import re
from pathlib import Path
import tempfile
import uuid
from .local_models import ASR, UVR, separation_spec, ModelManager, check_cancel
from .local_alignment_runtime import run_worker, validate_runtime_path, task_timeout
from .local_alignment import LANGUAGES, _extract_audio, generate_vocal_candidate, discover_media_duration
from .local_separation import prepare_vocals, file_digest


def split_singing_text(text, *, chunks=(), pauses=(), max_chars=32):
    """Draft-only suggestions, never accepted timestamps.

    Use validated timed words when available; otherwise map approximately over
    non-silent duration, snapping to CJK graphemes/punctuation or Latin spaces.
    """
    import regex
    if not isinstance(text,str):raise ValueError('听写文本无效。')
    if type(max_chars) is not int or not 4<=max_chars<=200:raise ValueError('断句长度无效。')
    boundaries={0,*[m.end() for m in regex.finditer(r'\X',text)]}
    breaks={m.end() for m in re.finditer(r'[。！？!?；;]+|\n+',text) if m.end() in boundaries}
    valid_pauses=[]
    for pause in pauses:
        if not isinstance(pause,(list,tuple)) or len(pause)!=2:continue
        p,q=pause
        if all(type(v) in (int,float) and math.isfinite(v) for v in (p,q)) and q>p:valid_pauses.append((p,q))
    cursor=0
    for chunk in chunks:
        if not isinstance(chunk,dict) or not isinstance(chunk.get('text'),str):continue
        content=chunk['text'].strip()
        at=text.find(content,cursor) if content else -1
        if at<0:continue
        end=at+len(content);cursor=end
        if end in boundaries:breaks.add(end)
        a,b=chunk.get('start'),chunk.get('end')
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (a,b)) or b<=a:continue
        silent=[]
        for p,q in sorted(valid_pauses):
            p,q=max(a,p),min(b,q)
            if q<=p:continue
            if silent and p<=silent[-1][1]:silent[-1]=(silent[-1][0],max(q,silent[-1][1]))
            else:silent.append((p,q))
        voiced=(b-a)-sum(q-p for p,q in silent)
        if voiced<=0:continue
        timed=[];word_cursor=0;previous=a;valid_words=True
        words=chunk.get('words',[])
        if not isinstance(words,list):words=[]
        for word in words:
            if not isinstance(word,dict):valid_words=False;break
            token=word.get('text',word.get('word',''))
            w0,w1=word.get('start',word.get('start_time')),word.get('end',word.get('end_time'))
            if not isinstance(token,str) or not token.strip() or not all(type(v) in (int,float) and math.isfinite(v) for v in (w0,w1)) or not a<=previous<=w0<w1<=b:
                valid_words=False;break
            pos=content.find(token.strip(),word_cursor)
            if pos<0 or content[word_cursor:pos].strip():valid_words=False;break
            word_cursor=pos+len(token.strip());previous=w1
            timed.append((at+word_cursor,w0,w1))
        if not valid_words or content[word_cursor:].strip():timed=[]
        soft=[m.end()+at for m in re.finditer(r'\s+|[，、,]',content) if m.end()+at in boundaries]
        cjk=bool(re.search(r'[\u3040-\u30ff\u3400-\u9fff]',content))
        clusters=[i for i in sorted(boundaries) if at<i<end]
        for p,q in silent:
            if q-p<.3 or not a<p<q<b:continue
            if timed:
                adjacent=[left[0] for left,right in zip(timed,timed[1:]) if left[2]<=p+.05 and right[1]>=q-.05 and left[0] in boundaries]
                if adjacent:breaks.add(adjacent[-1])
                continue
            elapsed=p-a-sum(max(0.,min(p,y)-x) for x,y in silent if x<p)
            estimate=at+len(content)*elapsed/voiced
            choices=soft or (clusters if cjk else [])
            if choices:
                nearest=min(choices,key=lambda i:abs(i-estimate))
                if abs(nearest-estimate)<=max_chars:breaks.add(nearest)
    output=[];start=0
    for end in sorted(breaks|{len(text)}):
        fragment=text[start:end].strip();start=end
        while fragment:
            clusters=list(regex.finditer(r'\X',fragment))
            if len(clusters)<=max_chars:output.append(fragment);break
            limit=clusters[max_chars-1].end();legal={m.end() for m in clusters}
            choices=[m.end() for m in re.finditer(r'\s+|[，、,]',fragment[:limit+1]) if m.start()>0 and m.end()<=limit and m.end() in legal]
            cut=choices[-1] if choices else limit
            part=fragment[:cut].strip()
            if part:output.append(part)
            fragment=fragment[cut:].strip()
    assert re.sub(r'\s','',text)==re.sub(r'\s','',''.join(output))
    return output


def validate_transcription_options(options):
    value=dict(options)
    value.setdefault("separation_model_id", UVR.id)
    separation_spec(value["separation_model_id"])
    value['runtime_python']=str(validate_runtime_path(value.get('runtime_python','')))
    path=Path(value.get('audio_path','')).expanduser()
    if not path.is_file():raise ValueError('请选择有效的音频或视频文件。')
    value['audio_path']=str(path.resolve())
    value.setdefault('offset_seconds',0.)
    if value.get('duration_seconds') is None:
        value['duration_seconds']=discover_media_duration(path)-value['offset_seconds']
    if value.get('device') not in ('cpu','cuda:0') or value.get('language') not in LANGUAGES:
        raise ValueError('请选择计算设备和演唱语言。')
    for key in ('offset_seconds','duration_seconds'):
        if type(value.get(key)) not in (int,float) or not math.isfinite(value[key]):raise ValueError('语音转写的片段时间填写有误。')
    if value['offset_seconds']<0 or not 0<value['duration_seconds']:
        raise ValueError('语音转写的起点需非负，时长需大于零。')
    return value


def generate_transcript_draft(document, *, options, cancel=lambda:False, progress=lambda p,m:None):
    settings=validate_transcription_options(options)
    ModelManager(settings['cache_root']).ensure_verified(ASR.id,cancel=cancel)
    use_separation=settings.get("use_separation",True)
    if type(use_separation) is not bool:raise ValueError("请选择是否分离人声。")
    stem=None
    source_hash=file_digest(settings['audio_path'],cancel)
    if use_separation:
        stem=prepare_vocals(settings,cancel=cancel,progress=lambda p,m:progress(round(p*.5),m))
    with tempfile.TemporaryDirectory(prefix='lyria-asr-') as folder:
        audio=Path(folder)/'vocals16k.wav'
        actual=_extract_audio(stem['path'] if stem else settings['audio_path'],audio,0 if stem else settings['offset_seconds'],stem['duration_seconds'] if stem else settings['duration_seconds'],cancel,gain=stem['alignment_gain'] if stem else 1.)
        response=run_worker(dict(operation='transcribe',audio_wav=str(audio),cache_root=settings['cache_root'],
                           device=settings['device'],language=settings['language']),settings['runtime_python'],
                           cancel=cancel,progress=lambda p,m:progress(50+round(p*.48),m),timeout=task_timeout(actual,'transcribe'))
    if (response.get('model_id')!=ASR.id or response.get('revision')!=ASR.revision or
        not isinstance(response.get('text'),str) or len(response['text'])>24000 or
        abs(response.get('duration',-1)-actual)>.001 or
        file_digest(settings['audio_path'],cancel)!=source_hash):
        raise ValueError('语音转写结果与当前音频不匹配，请重新生成。')
    draft=dict(kind='transcript_draft',version=1,id='draft-'+uuid.uuid4().hex,
        document_id=document.id,revision=document.revision,status='unreviewed',model_id=ASR.id,
        model_revision=ASR.revision,text=response['text'],complete=response.get('complete') is True,
        source_path=settings['audio_path'],source_sha256=source_hash,stem_path=stem['path'] if stem else None,stem_sha256=stem['sha256'] if stem else None,
        offset_seconds=settings['offset_seconds'],duration_seconds=actual,
        requested_duration_seconds=settings['duration_seconds'],language=settings['language'],
        warning=response['warning'],asr_elapsed_seconds=response['elapsed_seconds'],
        chunking=response.get('chunking',{}),chunks=response.get('chunks',[]),
        raw_response=deepcopy(response),pause_intervals=deepcopy(response.get('pause_intervals',[])),
        segmentation_diagnostics=dict(raw_nonempty_lines=sum(bool(x.strip()) for x in response['text'].splitlines()),
            raw_chunk_count=len(response.get('chunks',[])),adoption_rule='explicit-newlines-only; optional draft splitter',
            pause_mapping='validated timed words when present; otherwise non-silent-duration CJK/whitespace draft heuristic, not aligned timestamps'))
    draft['suggested_lines']=split_singing_text(draft['text'],chunks=draft['chunks'],pauses=draft['pause_intervals'])
    root=ModelManager(settings['cache_root']).root/'asr-drafts-v1';root.mkdir(parents=True,exist_ok=True)
    check_cancel(cancel)
    (root/(draft['id']+'.json')).write_text(json.dumps(draft,ensure_ascii=False,indent=2),encoding='utf-8')
    progress(100,'语音转写初稿已生成，核对歌词并确认后可导入主轴。')
    return draft


def confirm_transcript_draft(draft,text,document,*,confirmed=False):
    if not confirmed:raise ValueError('请先结合试听核对词句，并勾选确认后再继续打轴。')
    if (draft.get('kind')!='transcript_draft' or draft.get('model_id')!=ASR.id or
        draft.get('model_revision')!=ASR.revision or draft.get('document_id')!=document.id or
        draft.get('revision')!=document.revision):raise ValueError('工程内容已变动，当前语音转写初稿已失效，请重新生成。')
    if not draft.get('complete'):raise ValueError('部分内部短段重试后仍未完整输出，请检查听写诊断后重新运行。')
    if not isinstance(text,str) or not text.strip() or len(text)>24000:raise ValueError('请填写校正后的完整演唱原文，每行一句。')
    lines=[line for line in text.splitlines() if line.strip()]
    if len(lines)>1000:raise ValueError('歌词行数超过 1000 句上限，请分批处理。')
    result=deepcopy(draft)
    result.update(status='confirmed',corrected_text=text,rows=[{'id':f"{draft['id']}-line-{i}",'text':line} for i,line in enumerate(lines)])
    return result


def generate_confirmed_draft_candidate(document,frame_index=None,*,options,cancel=lambda:False,progress=lambda p,m:None):
    from .model import Mapping
    from .candidate_tracks import capture_source,candidate_from_project,candidate_project
    draft=options.get('transcript_draft',{})
    if draft.get('status')!='confirmed':raise ValueError('请先确认语音转写初稿后再开始自动打轴。')
    checked=confirm_transcript_draft(draft,draft.get('corrected_text'),document,confirmed=True)
    if file_digest(checked['source_path'],cancel)!=checked['source_sha256']:raise ValueError('原音频文件已变动，请重新生成语音转写初稿。')
    detached=deepcopy(document)
    detached.project.mappings=[Mapping(row['id'],[],text_override=row['text'],text_role='source_asr') for row in checked['rows']]
    settings=dict(options,audio_path=checked['source_path'],offset_seconds=checked['offset_seconds'],
                  duration_seconds=checked['duration_seconds'],language=checked['language'],text_source='main',main_source_confirmed=True,
                  sentence_ids=[row['id'] for row in checked['rows']])
    # Mandatory separation again: no approved existing-stem reuse exception.
    record=generate_vocal_candidate(detached,frame_index,options=settings,cancel=cancel,progress=progress)
    paths={role:item['path'] for role,item in record['provenance'].get('auxiliary_files',{}).items()}
    original=capture_source(document,'vocal',frame_index,auxiliary_paths=paths,cancel=cancel)
    if original['provenance']!=record['provenance']:raise ValueError('打轴期间工程或素材发生了变化，请重新运行打轴。')
    generated=candidate_project(record)
    for row in generated.mappings:row.method='asr_confirmed_source'
    result=candidate_from_project(original,generated)
    for row in result['rows']:row['reasons'].append('来自已确认的语音转写初稿，请结合试听核对时间轴')
    return result
