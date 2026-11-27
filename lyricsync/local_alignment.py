"""Given-lyrics local forced alignment -> read-only vocal candidate only.

No ASR, downloads or project writes occur here. Separation is optional and explicitly selected. All mutations
remain in the existing explicit candidate preview/apply/undo workflow.
"""
from __future__ import annotations
from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave

from .candidate_tracks import capture_source, candidate_from_project
from .editing import mapping_text
from .local_models import CATALOG, QWEN, UVR, NEXTFIRE, ALIGNMENT_MODELS, separation_spec, ModelManager, check_cancel
from .local_alignment_runtime import run_worker, _stop, validate_runtime_path
from .model import Project

LANGUAGES = ("Japanese", "Chinese", "English", "Cantonese", "French", "German",
             "Italian", "Korean", "Portuguese", "Russian", "Spanish")
MAX_CHARACTERS = 24000

def _normal(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if unicodedata.category(c)[0] in "LN")

def _number(value):
    return type(value) in (float, int) and math.isfinite(value)


def discover_media_duration(path):
    """Read the selected media's header, never silently choose its first 300 s."""
    import av
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError('所选素材没有音轨。')
        stream = container.streams.audio[0]
        if container.duration is not None:
            duration = float(container.duration / av.time_base)
        elif stream.duration is not None and stream.time_base is not None:
            duration = float(stream.duration * stream.time_base)
            if stream.start_time is not None:
                duration += max(0., float(stream.start_time * stream.time_base))
        else:
            raise ValueError('无法读取整首时长，请检查素材或明确填写处理范围。')
    if not _number(duration) or duration <= 0:
        raise ValueError('素材时长无效。')
    return duration

def request_rows(document, sentence_ids=None, text_source="main", *, main_source_confirmed=False):
    project = document.project
    if text_source == "reference":
        rows = [{"id": "reference-" + row.id, "text": row.text, "lyric_ids": [row.id], "reading":getattr(row,"reading",""), "reading_segments":deepcopy(getattr(row,"reading_segments",[]))} for row in project.lyrics if row.text.strip()]
        return _validate_source_rows(rows)
    known = {row.id for row in project.mappings}
    ids = list(sentence_ids or [row.id for row in project.mappings])
    if not ids or len(ids) != len(set(ids)) or any(identity not in known for identity in ids):
        raise ValueError('请选择有效的演唱原文句子。')
    if text_source not in ("main", "lyrics"):
        raise ValueError('请选择使用主时间轴歌词还是关联的演唱原文。')
    texts = {row.id: row.text for row in project.translations}
    lyrics = {row.id: row.text for row in project.lyrics}
    requested = set(ids)
    result = []
    for row in project.mappings:
        if row.id not in requested:
            continue
        if text_source == "main" and row.text_role=="translation" and not main_source_confirmed:
            continue
        if text_source == "main":
            text = mapping_text(project, row, texts)
        else:
            if not row.lyric_ids or any(i not in lyrics for i in row.lyric_ids):
                raise ValueError('所选句子未关联演唱原文，请先关联原文后再自动打轴。')
            text = " ".join(lyrics[i] for i in row.lyric_ids)
        entry={"id":row.id,"text":text}
        if text_source=="lyrics":
            by_id={item.id:item for item in project.lyrics}
            linked=[by_id[i] for i in row.lyric_ids]
            if all(getattr(item,"reading","") for item in linked):entry["reading"]=" ".join(item.reading for item in linked)
            segments=[deepcopy(segment) for item in linked for segment in getattr(item,"reading_segments",[])]
            if segments:entry["reading_segments"]=segments
        result.append(entry)
    if not any(_normal(row["text"]) for row in result):
        raise ValueError('所选范围内没有可用的演唱原文。若无现成歌词，请先用语音转写生成初稿并核对确认。')
    if sum(len(row["text"]) for row in result) > MAX_CHARACTERS or len(result) > 1000:
        raise ValueError('单次打轴的歌词过多，请分批选择（每次不超过 1000 句或 24000 字）。')
    return result

def _validate_source_rows(rows):
    if not isinstance(rows,list) or not rows or len(rows)>1000:
        raise ValueError("请选择有效的演唱原文，最多 1000 句。")
    if any(not isinstance(row,dict) or not isinstance(row.get("id"),str) or not row["id"] or not isinstance(row.get("text"),str) for row in rows):
        raise ValueError("歌词行数据无效。")
    if len({row["id"] for row in rows})!=len(rows) or sum(len(row["text"]) for row in rows)>MAX_CHARACTERS:
        raise ValueError("歌词标识重复或超过 24000 字。")
    for row in rows:
        ids = row.get('lyric_ids', [])
        if not isinstance(ids, list) or len(ids) > 1000 or any(not isinstance(i, str) or not i or len(i)>256 for i in ids):
            raise ValueError('歌词来源实例关联无效。')
    if not any(_normal(row["text"]) for row in rows):raise ValueError("没有可用于打轴的原词。")
    return deepcopy(rows)


def validate_options(document, options):
    value = dict(options)
    value.setdefault("separation_model_id", UVR.id)
    separation_spec(value["separation_model_id"])
    value.setdefault("model_id", QWEN.id)
    value.setdefault("cache_root", str(ModelManager().root))
    value.setdefault("device", "cuda:0")
    value.setdefault("language", "Japanese")
    value.setdefault("offset_seconds", 0.0)
    if value.get("separation_device") not in (None, "cuda:0", "directml", "cpu"):
        raise ValueError("不支持的人声分离计算设备。")
    value.setdefault("text_source", "reference")
    if value["text_source"]=="main" and value.get("main_source_confirmed") is not True:
        raise ValueError("请明确确认当前主轴文本就是实际演唱原词；译文不会自动作为打轴输入。")
    value.setdefault("min_timestamp_score", .01)
    value["rows"] = _validate_source_rows(value.get("source_rows")) if value["text_source"]=="external" else request_rows(document, value.get("sentence_ids"), value["text_source"],main_source_confirmed=value.get("main_source_confirmed",False))
    readings=value.get("reading_rows")
    if readings is not None:
        readings=_validate_source_rows(readings)
        if [(r["id"],r["text"]) for r in readings]!=[(r["id"],r["text"]) for r in value["rows"]]:
            raise ValueError("原词已改变，请重新核对读音。")
        for row,reading in zip(value["rows"],readings):
            if isinstance(reading.get("reading"),str):row["reading"]=reading["reading"]
            for key in ("alignment_units","reading_tool","reading_confirmed_for","reading_origin","generated_reading","reading_issues","reading_conversion"):
                if key in reading:row[key]=deepcopy(reading[key])
    value.setdefault("alignment_strategy","anchor_then_estimated_cursor")
    if value["alignment_strategy"] not in ("anchored","anchor_then_estimated_cursor"):raise ValueError("未知的对齐调度策略。")
    value.setdefault("use_separation", False)
    if value["use_separation"] in ("true", "false"):
        value["use_separation"] = value["use_separation"] == "true"
    if type(value["use_separation"]) is not bool:raise ValueError("请选择是否分离人声。")
    if value["model_id"] not in ALIGNMENT_MODELS:
        raise ValueError('暂不支持所选的对齐模型。')
    if value["device"] not in ("cuda:0", "cpu") or value["language"] not in LANGUAGES:
        raise ValueError('请选择支持的演唱语言和计算设备。')
    if value["model_id"]==NEXTFIRE.id and value["language"]!="Japanese":
        raise ValueError("NextFire 当前仅支持日语演唱原词。")
    if value["model_id"] == NEXTFIRE.id:
        from .japanese_readings import invalid_reading_rows
        issues=invalid_reading_rows(readings if readings is not None else value['rows'])
        if issues:
            raise ValueError('以下读音需核对（已扫描全曲）：\n'+'\n'.join(f"第 {item['index']} 句：{item['message']}" for item in issues))
    if value.get('duration_seconds') is None:
        if not Path(value.get('audio_path', '')).is_file():
            raise ValueError("找不到音频或人声文件；请选择项目音频或已有人声分轨。")
        try:
            value['duration_seconds'] = discover_media_duration(value['audio_path']) - value['offset_seconds']
        except (OSError, ValueError) as exc:
            raise ValueError('无法确定整首音频范围：' + str(exc)) from exc
    for key in ("offset_seconds", "duration_seconds", "min_timestamp_score"):
        if not _number(value[key]):
            raise ValueError('片段时间或置信度阈值填写有误。')
    if value["offset_seconds"] < 0 or value["duration_seconds"] <= 0:
        raise ValueError('开始时间不能小于零，处理时长必须大于零。')
    if not 0 <= value["min_timestamp_score"] <= 1:
        raise ValueError('置信度阈值需在 0～1 之间。')
    audio = Path(value.get("audio_path", "")).expanduser()
    if not audio.is_file():
        raise ValueError("找不到音频或人声文件；请选择项目音频或已有人声分轨。")
    value["audio_path"] = str(audio.resolve())
    value["runtime_python"] = str(validate_runtime_path(value.get("runtime_python", "")))
    return value

def _extract_audio(path, destination, offset, duration, cancel, *, ffmpeg=None, sample_rate=16000, channels=1, gain=1.):
    if ffmpeg is None:
        from .vision import resources
        ffmpeg = resources() / "ffmpeg.exe"
    if not Path(ffmpeg).is_file():
        raise RuntimeError('未找到内置 FFmpeg 组件，请使用完整安装包。')
    # Keep stream lead relative to the media origin before selecting the window.
    command = [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-copyts", "-start_at_zero", "-i", str(path), "-map", "0:a:0", "-vn",
               "-af", f"aresample={sample_rate}:first_pts=0,atrim=start={offset}:duration={duration},asetpts=PTS-STARTPTS,volume={gain}",
               "-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(destination)]
    with tempfile.TemporaryFile() as error:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=error,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        started = time.monotonic()
        try:
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() - started > max(180, duration * 2):
                    raise TimeoutError('音频解码超时，请检查媒体文件是否损坏。')
                time.sleep(.05)
            if process.returncode:
                error.seek(0)
                detail = error.read(3000).decode("utf-8", errors="replace")
                raise RuntimeError("音频无法解码或没有音轨：" + detail)
        finally:
            _stop(process)
    check_cancel(cancel)
    with wave.open(str(destination), "rb") as stream:
        actual = stream.getnframes() / stream.getframerate()
    if not _number(actual) or not 0 < actual <= duration + .1:
        raise ValueError('所选音频为空，或解码后的时长超出了请求范围。')
    return actual

def qwen_acoustic_rows(rows):
    """Temporary model input only; never mutate stored source or infer conversion success."""
    from .japanese_readings import pure_symbol_row, temporary_reading_input
    return [dict(row, text=temporary_reading_input(row['text'])[0])
            for row in rows if not pure_symbol_row(row)]


def candidate_from_alignment(snapshot, rows, response, *, offset_seconds=0.0,
                             min_timestamp_score=.01, audio_evidence=None):
    """Strict ordered coverage, never text search or positional ID reassignment.

    Unit tests inject explicit worker results here; production only calls this
    with the separate real model process. Missing words invalidate association
    rather than shift a repeated chorus onto another stable sentence ID.
    """
    if response.get("model_id") != QWEN.id or response.get("revision") != QWEN.revision:
        raise ValueError('对齐模型版本校验不匹配，请重新校验本地模型。')
    duration = response.get("duration")
    words = response.get("words")
    if not _number(duration) or duration <= 0 or not isinstance(words, list) or len(words) > 50000:
        raise ValueError('对齐模型返回的数据异常，请重试。')
    project = Project.from_dict(deepcopy(snapshot["project"]))
    row_ids = [row["id"] for row in rows]
    if len(row_ids) != len(set(row_ids)) or any(i not in snapshot["base_rows"] for i in row_ids):
        raise ValueError('对齐返回结果与请求的歌词行不匹配，请重试。')
    from .japanese_readings import pure_symbol_row
    symbolic_ids = {row['id'] for row in rows if pure_symbol_row(row)}
    acoustic = qwen_acoustic_rows(rows)
    supplied = {row["id"]: row["text"] for row in acoustic}
    supplied.update({identity: '' for identity in symbolic_ids})
    project.mappings = [row for row in project.mappings if row.id in supplied]
    expected = "".join(_normal(row["text"]) for row in acoustic)
    valid_words = all(isinstance(w, dict) and isinstance(w.get("text"), str) for w in words)
    observed = "".join(_normal(w["text"]) for w in words) if valid_words else ""
    coverage_ok = bool(expected) and expected == observed
    spans, cursor = [], 0
    previous_end = 0.0
    for word in words if valid_words else []:
        length = len(_normal(word["text"]))
        start, end = word.get("start_time"), word.get("end_time")
        reason = ""
        if not length or not _number(start) or not _number(end) or not 0 <= start < end <= duration + .001:
            reason = '部分字词未检出有效时间，请手动补轴'
        elif start + .001 < previous_end:
            reason = '模型逐词时间出现重叠或倒退，请手动调整'
        if _number(end):
            previous_end = max(previous_end, end)
        if word.get("repaired"):
            reason = reason or '部分字词含时间插值估算，请试听核对'
        score = word.get("score")
        if score is not None and (not _number(score) or not 0 <= score <= 1):
            reason = reason or '模型时间评分异常，请手动核对'
        elif score is not None and score < min_timestamp_score:
            reason = reason or '对时置信度过低，已留空待手动打轴'
        if word.get('boundary_pending'):
            reason=reason or '片段边界句未完整确认，保留未定位；已尝试带余量重试'
        spans.append((cursor, cursor + length, start, end, score, reason))
        cursor += length
    cursor = 0
    evidence = "local-model:" + QWEN.repo + "@" + QWEN.revision
    source_note = ""
    if audio_evidence:
        source_note = "audio-sha256:" + audio_evidence["sha256"] + "；源文件：" + audio_evidence["path"]
    for row in project.mappings:
        if row.id in symbolic_ids:
            row.start = row.end = None
            row.confidence = 0.0
            row.method = 'nonvocal_symbols'
            row.reasons = [evidence, '纯符号原文已保留，不送入声学对时。']
            continue
        length = len(_normal(supplied[row.id]))
        line_start, line_end = cursor, cursor + length
        cursor = line_end
        overlapping = [span for span in spans if span[0] < line_end and span[1] > line_start]
        reason = ""
        if not coverage_ok:
            reason = '模型输出词序与原词不一致，为避免串句未自动填轴，请手动打轴'
        elif not length or not overlapping:
            reason = '本句无有效可打轴字符（如纯符号或空行）'
        elif any(span[0] < line_start or span[1] > line_end for span in overlapping):
            reason = '模型切词跨越了分行边界，请手动拆分时间'
        else:
            reason = next((span[5] for span in overlapping if span[5]), "")
        scores = [span[4] for span in overlapping if span[4] is not None and _number(span[4]) and 0 <= span[4] <= 1]
        row.start = row.end = None
        row.confidence = min(scores) if scores else 0.0
        row.reasons = [evidence, '原词自动打轴结果，请结合试听核对',
                       '参考分仅供辅助排查（0 表示未知）',
                       f'模型精度约 80 毫秒 · 片段偏移 {offset_seconds:g} 秒']
        if source_note:
            row.reasons.append(source_note[:3900])
        if reason:
            row.reasons.append(reason)
        else:
            row.start = float(overlapping[0][2]) + offset_seconds
            row.end = float(overlapping[-1][3]) + offset_seconds
            row.reasons.append('当前为打轴预览结果，确认应用后才会写入主时间轴')
        # Keep exact original display text and IDs even if alignment used source lyrics.
        if any(w.get('estimated_window') for w in words if isinstance(w,dict) and w.get('source_row_id')==row.id):
            row.reasons.append('预计句数宽窗补齐候选，合法时间不代表对齐正确，请试听确认。')
        row.method = "local_vocal_qwen"
    return candidate_from_project(snapshot, project)

def candidate_from_nextfire(snapshot, rows, response, *, offset_seconds=0.0, min_timestamp_score=.01, audio_evidence=None):
    from .nextfire_candidate import candidate_from_nextfire as convert
    return convert(snapshot,rows,response,offset_seconds=offset_seconds,audio_evidence=audio_evidence)


def source_row_bindings(project, rows):
    """Bind only exact unique occurrence partitions, including many source lines per cue."""
    from collections import Counter
    lyrics={x.id:x.text for x in project.lyrics}
    valid={}
    if len({row['id'] for row in rows})!=len(rows):return {}
    for row in rows:
        ids=row.get('lyric_ids',[])
        if (ids and len(ids)==len(set(ids)) and all(i in lyrics for i in ids)
                and row['text']==' '.join(lyrics[i] for i in ids)):
            valid[row['id']]=tuple(ids)
    uses=Counter(i for ids in valid.values() for i in ids)
    target_uses=Counter(i for cue in project.mappings for i in cue.lyric_ids)
    result={}
    for cue in project.mappings:
        ids=list(cue.lyric_ids)
        if not ids or len(ids)!=len(set(ids)) or any(uses[i]!=1 or target_uses[i]!=1 for i in ids):continue
        pieces=[(row['id'],valid[row['id']]) for row in rows if row['id'] in valid and set(valid[row['id']])<=set(ids)]
        if [i for _,part in pieces for i in part]!=ids:continue
        result.update({identity:cue.id for identity,_ in pieces})
    return result


def rebind_source_candidate(snapshot, record, source_rows):
    """Aggregate proven source groups without changing original/translation text."""
    from .candidate_tracks import candidate_project
    from .model import stable_id
    original=Project.from_dict(deepcopy(snapshot['project']))
    bindings=source_row_bindings(original,source_rows)
    sources={x['id']:x for x in source_rows}
    generated=candidate_project(record)
    groups={};unbound=[];positions={}
    for position,row in enumerate(generated.project_timeline(detached=True)):
        positions[id(row)]=position
        target=bindings.get(row.id);source=sources.get(row.id,{})
        if target and (row.text_override!=source.get('text') or row.lyric_ids!=source.get('lyric_ids')):target=None
        if target:groups.setdefault(target,[]).append(row)
        else:
            matches=[edge for edge in original.alignment_graph.unbound_edges
                     if row.lyric_ids and edge['lyric_ids']==row.lyric_ids and edge['translation_ids']]
            if len(matches)==1:row.translation_ids=list(matches[0]['translation_ids'])
            if row.id in snapshot['base_rows']:row.id=stable_id('alignment-unbound',snapshot['document_id'],row.id,row.text_override)
            unbound.append(row)
    rebound=[]
    for target in original.project_timeline(detached=True):
        group=groups.get(target.id)
        if not group:continue
        expected=[r['id'] for r in source_rows if bindings.get(r['id'])==target.id]
        if [r.id for r in group]!=expected:
            unbound.extend(group);continue
        complete=all(_number(r.start) and _number(r.end) and r.start<r.end for r in group)
        ordered=complete and all(x.end<=y.start+.002 for x,y in zip(group,group[1:]))
        target.start=group[0].start if ordered else None
        target.end=group[-1].end if ordered else None
        target.confidence=min(r.confidence for r in group)
        target.reasons=list(dict.fromkeys(reason for r in group for reason in r.reasons))
        if not ordered:target.reasons.append('多原词关联存在缺失或时间次序冲突，未聚合时间')
        target.method=group[0].method
        if hasattr(target,'alignment_tokens'):target.alignment_tokens=[deepcopy(t) for r in group for t in r.alignment_tokens]
        positions[id(target)]=min(positions[id(row)] for row in group)
        rebound.append(target)
    original.replace_timeline(sorted(rebound+unbound,key=lambda row:positions[id(row)]))
    return candidate_from_project(snapshot,original)


def alignment_result_summary(record):
    """Timing coverage only; never present existence of timestamps as accuracy."""
    rows = record['rows']
    missing = [i + 1 for i, row in enumerate(rows)
               if not (_number(row.get('start')) and _number(row.get('end'))
                       and 0 <= row['start'] < row['end'])]
    ranges = []
    for number in missing:
        if ranges and ranges[-1][1] + 1 == number:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    labels = [str(start) if start == end else f'{start}–{end}' for start, end in ranges]
    total, valid = len(rows), len(rows) - len(missing)
    status = 'empty' if not valid else 'partial' if missing else 'complete'
    message = (f'无有效打轴结果：{total} 句均未得到可用时间。' if not valid else
               f'部分完成：{valid}/{total} 句有可用时间，{len(missing)} 句未打轴。' if missing else
               f'已生成 {valid} 句时间候选，仍需试听核对。')
    if missing:
        message += '未打轴句号：' + '、'.join(labels[:20]) + (' 等' if len(labels) > 20 else '') + '。原因保留在候选详情中。'
    return dict(total=total, valid=valid, missing=len(missing), status=status,
                missing_ranges=labels, message=message)



def alignment_stage_diagnostics(response, record):
    """Bounded counts only: never copy source text, raw model text or paths to logs."""
    words = response.get('words', response.get('rows', []))
    if not isinstance(words, list): words = []
    duration = response.get('duration')
    raw_valid = sum(1 for word in words if isinstance(word, dict)
                    and _number(word.get('start_time')) and _number(word.get('end_time'))
                    and 0 <= word['start_time'] < word['end_time']
                    and (not _number(duration) or word['end_time'] <= duration + .001))
    chunking = response.get('chunking', {})
    batches = chunking.get('batches', []) if isinstance(chunking, dict) else []
    summary = alignment_result_summary(record)
    rows = record['rows']
    bound = sum(row.get('sentence_id') is not None for row in rows)
    return dict(raw_words=len(words), raw_timed_words=raw_valid,
                batches=(len(batches) if batches else chunking.get('chunks', 1)) if isinstance(chunking, dict) else 1,
                candidate_total=summary['total'], candidate_valid=summary['valid'],
                candidate_missing=summary['missing'], bound_rows=bound,
                unbound_rows=len(rows)-bound, status=summary['status'])


def audio_activity(path):
    """Whole PCM signal counters; quiet regions are measured, never removed."""
    import numpy as np
    peak=0.; square=0.; samples=0; quiet=0; blocks=0
    with wave.open(str(path), 'rb') as stream:
        rate=stream.getframerate(); channels=stream.getnchannels()
        if stream.getsampwidth()!=2: raise ValueError('诊断音频需要 PCM16。')
        while data:=stream.readframes(max(1,round(rate*.02))):
            values=np.frombuffer(data,dtype='<i2').astype(np.float64)/32768.
            if not len(values):continue
            power=float((values*values).sum()); square+=power;samples+=len(values)
            peak=max(peak,float(np.abs(values).max()));blocks+=1
            quiet+=power/len(values)<1e-6
    return dict(sample_rate=rate,channels=channels,samples=samples//channels,
                rms=math.sqrt(square/max(1,samples)),peak=peak,
                quiet_frame_fraction=quiet/max(1,blocks),digital_silence=peak<=1/32768.)


class MediaBoundaryError(ValueError):
    """Actionable mismatch without permission to move or crop audio."""
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def media_boundary_policy(document, frame_index, audio_evidence, actual, offset, *, stream_info=None):
    """Map only verified same-media, same-origin codec/frame tail rounding.

    The complete decoded audio (including silence) remains inference input.
    Header/frame-derived tolerance is capped; unrelated tracks never inherit it.
    """
    if frame_index is None:
        return dict(strategy='audio_timeline',audio_end=offset+actual,video_end=None,tolerance=0.)
    video_end=float(frame_index.duration);audio_end=offset+actual;delta=audio_end-video_end
    result=dict(strategy='within_video',audio_end=audio_end,video_end=video_end,
                offset=offset,delta_seconds=delta,tolerance=0.)
    if delta<=1e-6:return result
    primary=document.project.inputs.get('video','')
    same=bool(primary and str(Path(primary).resolve()).casefold()==str(audio_evidence['path']).casefold())
    expected=document.project.video.get('content_hash')
    if expected and expected!=audio_evidence['sha256']:same=False
    result['same_source']=same
    if same:
        try:
            if stream_info is None:
                import av
                with av.open(primary) as container:
                    a=container.streams.audio[0];v=container.streams.video[0]
                    def start(st):return float(st.start_time*st.time_base) if st.start_time is not None else 0.
                    stream_info=dict(audio_start=start(a),video_start=start(v),
                        audio_frame_seconds=float(a.codec_context.frame_size or 0)/float(a.codec_context.sample_rate or 1),
                        video_frame_seconds=1/float(v.average_rate) if v.average_rate else 0.,
                        audio_end=start(a)+float(a.duration*a.time_base) if a.duration is not None else None)
            differences=[b-a for a,b in zip(frame_index.stamps[-101:-1],frame_index.stamps[-100:]) if b>a]
            import statistics
            video_tick=statistics.median(differences) if differences else stream_info['video_frame_seconds']
            audio_tick=stream_info['audio_frame_seconds']
            budget=min(.1,max(0.,video_tick)+max(0.,audio_tick)+2/16000.)
            result.update(tolerance=budget,video_frame_seconds=video_tick,audio_frame_seconds=audio_tick,sample_budget_seconds=2/16000.)
            origin_ok=abs(stream_info['audio_start']-stream_info['video_start'])<=2/16000.
            header_end=stream_info.get('audio_end')
            length_ok=_number(header_end) and abs(audio_end-header_end)<=max(.002,2/16000.)
            if origin_ok and length_ok and delta<=budget:
                result.update(strategy='same_source_codec_tail',tolerance=budget)
                return result
        except (OSError,ValueError,IndexError,KeyError,AttributeError):pass
    result['choices']=['select_matching_media','keep_settings_cancel']
    raise MediaBoundaryError(f'媒体范围不匹配：音频结束 {audio_end:.6f} 秒，视频结束 {video_end:.6f} 秒，差值 {delta*1000:.3f} 毫秒，偏移 {offset:.6f} 秒。无法证明是同源同起点的小幅编码尾差；未裁剪音频或平移时间。请核对素材来源，选择匹配的媒体，或保留当前设置并取消。',result)


def constrain_candidate_to_media(record, policy):
    """Keep raw model endpoints in reasons; no stretching or invented last frame."""
    end=policy.get('video_end')
    if end is None:return record
    record=deepcopy(record)
    for row in record['rows']:
        start,stop=row.get('start'),row.get('end')
        if not (_number(start) and _number(stop)) or stop<=end:continue
        row['reasons'].append(f'原模型时间：{start:.9f}–{stop:.9f} 秒；视频尾：{end:.9f} 秒')
        if policy['strategy']=='same_source_codec_tail' and stop-end<=policy['tolerance'] and start<end:
            row['end']=end
            row['reasons'].append('同源编码小幅尾差：只将候选终点限制在真实视频尾；完整音轨已参与推理')
        else:
            row['start']=row['end']=None;row['status']='missing'
            row['reasons'].append('候选完全位于视频尾外或越界过大，保留未定位，不伪造一帧')
    return record


def write_alignment_manifest(cache_root, run_id, data):
    """Private counters only. Detailed lyrics/raw output require explicit export."""
    import uuid
    if str(uuid.UUID(run_id))!=run_id:raise ValueError('invalid run id')
    from .storage import atomic_json
    root=Path(cache_root)/'runs-v1'/run_id;root.mkdir(parents=True,exist_ok=True)
    atomic_json(root/'manifest.json',data)


def update_alignment_manifest(cache_root, record, *, application=None, navigation=None):
    ids={reason.removeprefix('diagnostic-run:') for row in record.get('rows',[]) for reason in row.get('reasons',[]) if reason.startswith('diagnostic-run:')}
    if len(ids)!=1:return False
    run_id=ids.pop()
    import uuid
    if str(uuid.UUID(run_id))!=run_id:return False
    path=Path(cache_root)/'runs-v1'/run_id/'manifest.json'
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return False
    for key,value in (('application',application),('navigation',navigation)):
        if value is not None:data[key]={k:v for k,v in value.items() if isinstance(v,(int,float,bool)) or v is None or k in ('status','reason')}
    write_alignment_manifest(cache_root,run_id,data);return True

def refine_alignment_with_cursor(request, response, runtime_python, *, cancel=lambda:False, progress=lambda p,m:None):
    """Default Qwen candidate completion: estimate K, never consume K blindly.

    Existing acoustically valid row instances have priority. Every proposed row
    is run through the same strict converter, then bounded by known neighbors.
    Only confirmed contiguous instances advance the cursor. No project is edited.
    """
    from .dev9_long_audio import silence_intervals,disjoint_speech_windows
    from .project_document import ProjectDocument
    from .model import Mapping
    from .local_alignment_runtime import PartialAlignmentError,task_timeout
    rows=request['rows'];duration=response['duration']
    def converted(raw,selected):
        temporary=ProjectDocument(Project(mappings=[Mapping(r['id'],[],text_override=r['text']) for r in selected]))
        return candidate_from_alignment(capture_source(temporary,'vocal'),selected,raw,min_timestamp_score=request.get('min_timestamp_score',.01))
    initial=converted(response,rows)
    resolved={i for i,row in enumerate(initial['rows']) if row['start'] is not None}
    if len(resolved)==len(rows):return response
    tokenization=response.get('tokenization',{})
    counts=tokenization.get('row_word_counts') if isinstance(tokenization,dict) else None
    if not isinstance(counts,list) or len(counts)!=len(rows) or any(type(c) is not int or c<0 for c in counts) or sum(counts)!=len(response.get('words',[])):
        # Cannot invent a segmentation of unidentifiable model tokens.
        return response
    by_row={};position=0
    for i,count in enumerate(counts):
        if i in resolved:by_row[i]=deepcopy(response['words'][position:position+count])
        position+=count
    cursor=0
    while cursor in resolved:cursor+=1
    try:
        quiet=silence_intervals(request['audio_wav'])
        windows=list(disjoint_speech_windows(request['audio_wav'],seconds=30.,quiet_regions=quiet))
    except (OSError,ValueError,wave.Error) as exc:
        partial=deepcopy(response)
        partial.setdefault('chunking',{})['refinement_error']=dict(stage='pcm_precheck',error_code=type(exc).__name__)
        raise PartialAlignmentError(partial,'failed') from exc
    reports=[];raw_batches=deepcopy(response.get('raw_batches',[]))
    if not raw_batches:raw_batches=[dict(start=0.,end=duration,request_rows=deepcopy(rows),response=deepcopy(response))]
    initial_resolved=set(resolved)
    pending_boundary=set()
    pending_details={}
    review_attempts=0
    last_review_range=None
    attempted_ranges=set()
    def active(a,b):return max(0.,b-a-sum(max(0.,min(b,y)-max(a,x)) for x,y in quiet))
    def assembled():
        result=deepcopy(response)
        if resolved!=initial_resolved:
            words=[];row_counts=[]
            for i,row in enumerate(rows):
                values=by_row.get(i,[dict(text=row['text'],start_time=None,end_time=None,repaired=False,source_row_id=row['id'],reason='estimated_cursor_unresolved')])
                words.extend(deepcopy(values));row_counts.append(len(values))
            result.update(words=words,tokenization=dict(strategy='anchor-then-estimated-cursor-v1',row_word_counts=row_counts))
        result['raw_batches']=deepcopy(raw_batches)
        result.setdefault('chunking',{})['cursor_refinement']=dict(strategy='remaining-active-duration-K-plus-3',reports=deepcopy(reports),cursor=cursor,initial_valid=len(initial_resolved),boundary_review_attempts=review_attempts,tail_cluster_rule=dict(min_words=2,tail_seconds=.5,max_span_seconds=.25),accepted_new=len(resolved-initial_resolved),pending_boundary_ids=[rows[i]['id'] for i in sorted(pending_boundary)],pending_details={rows[i]['id']:deepcopy(v) for i,v in pending_details.items()},silence_kind='PCM-energy-not-semantic-VAD',silence_intervals=quiet)
        return result
    try:
        with tempfile.TemporaryDirectory(prefix='lyria-cursor-candidates-') as folder:
            jobs=[dict(a=a,b=b,review=False) for a,b in windows]
            number=-1
            while jobs or (pending_boundary and review_attempts<2):
                number+=1
                if jobs:
                    job=jobs.pop(0)
                else:
                    details=list(pending_details.values())
                    job=dict(a=min(v['required_start'] for v in details),b=max(v['required_end'] for v in details),review=True)
                a,b=job['a'],job['b']
                review=job['review']
                if review:
                    if review_attempts>=2:continue
                    review_attempts+=1
                if cursor>=len(rows):break
                if cancel():raise PartialAlignmentError(assembled(),'cancelled')
                energy=active(a,b)
                if energy<.6 and not review and not pending_boundary:
                    reports.append(dict(start=a,end=b,cursor_before=cursor,cursor_after=cursor,status='quiet_no_consumption'));continue
                before=cursor
                k=min(len(rows)-cursor,max(1,math.ceil((len(rows)-cursor)*energy/max(.001,energy,active(a,duration)))))
                stop=max(cursor+k+3,max(pending_boundary,default=-1)+1)
                # Both lyric margins are actual model input. Resolved instances remain
                # context-only and can never overwrite their original timestamps.
                indices=list(range(max(0,cursor-3),min(len(rows),stop)))
                selected=[rows[i] for i in indices]
                context_ids=[rows[i]['id'] for i in indices if i in resolved]
                if review and last_review_range is not None:
                    # Grow from the actual previous review exactly once; required
                    # pending coverage is merged below without another +5 padding.
                    first=max(0.,last_review_range[0]-5.)
                    end=min(duration,last_review_range[1]+5.)
                else:
                    first=max(0.,a-(5. if review else 2.));end=min(duration,b+(5. if review else 2.))
                for i in pending_boundary:
                    detail=pending_details[i]
                    first=min(first,detail['required_start'])
                    end=max(end,detail['required_end'])
                # Audio must contain confirmed leading lyric context as well.
                for i in indices:
                    if i<cursor and i in by_row:
                        first=min(first,max(0.,by_row[i][0]['start_time']-2.))
                    if i in by_row:
                        end=max(end,min(duration,by_row[i][-1]['end_time']+2.))
                if review and (round(first*16000),round(end*16000)) in attempted_ranges:
                    reports.append(dict(start=first,end=end,base_start=a,base_end=b,
                        status='no_further_context',is_boundary_retry=True,
                        cursor_before=cursor,cursor_after=cursor,
                        pending_boundary_ids=[rows[i]['id'] for i in sorted(pending_boundary)]))
                    if not jobs:review_attempts=2
                    continue
                if end-first>85.:
                    reports.append(dict(start=first,end=end,base_start=a,base_end=b,
                        status='model_limit_incomplete',limit_seconds=85.,required_seconds=end-first,
                        cursor_before=cursor,cursor_after=cursor,context_only_row_ids=context_ids,
                        pending_boundary_ids=[rows[i]['id'] for i in sorted(pending_boundary)]))
                    continue
                if review:last_review_range=(first,end)
                attempted_ranges.add((round(first*16000),round(end*16000)))
                path=Path(folder)/'window.wav'
                with wave.open(request['audio_wav'],'rb') as source,wave.open(str(path),'wb') as target:
                    target.setparams((1,2,16000,0,'NONE','not compressed'));source.setpos(round(first*16000));target.writeframes(source.readframes(round(end*16000)-round(first*16000)))
                try:
                    raw=run_worker(dict(request,audio_wav=str(path),rows=selected),runtime_python,cancel=cancel,
                        timeout=task_timeout(end-first,'alignment'),progress=lambda p,m:progress(min(98,75+round((number+p/100)*22/max(1,len(windows)))),f'候选窗 {number+1}（含边界复核）：'+m))
                    raw_batches.append(dict(start=first,end=end,request_rows=deepcopy(selected),response=deepcopy(raw),strategy='estimated_cursor',is_boundary_retry=review))
                    if not _number(raw.get('duration')) or abs(raw['duration']-(end-first))>.01:raise ValueError('宽窗返回时长与实际音频不一致。')
                    candidate=converted(raw,selected)
                except (InterruptedError,PartialAlignmentError) as exc:
                    if isinstance(exc,PartialAlignmentError):
                        raw_batches.append(dict(start=first,end=end,request_rows=deepcopy(selected),
                            response=deepcopy(exc.result),strategy='estimated_cursor',
                            status='interrupted_unaccepted',interruption_reason=exc.reason,
                            is_boundary_retry=review))
                    raise PartialAlignmentError(assembled(),getattr(exc,'reason','cancelled')) from exc
                except (RuntimeError,ValueError,OSError) as exc:
                    reports.append(dict(start=first,end=end,cursor_before=before,cursor_after=cursor,status='failed',error_code=type(exc).__name__,row_ids=[r['id'] for r in selected]));continue
                tokenization=raw.get('tokenization',{})
                row_counts=tokenization.get('row_word_counts',[]) if isinstance(tokenization,dict) else []
                if not isinstance(row_counts,list) or len(row_counts)!=len(selected) or any(type(c) is not int or c<0 for c in row_counts) or sum(row_counts)!=len(raw.get('words',[])):
                    reports.append(dict(start=first,end=end,status='token_identity_mismatch',cursor_before=before,cursor_after=cursor));continue
                position=0;accepted=[];rejected=[]
                for i,row,count,item in zip(indices,selected,row_counts,candidate['rows']):
                    words=deepcopy(raw['words'][position:position+count]);position+=count
                    if i in resolved:continue
                    timed=[w for w in words if _number(w.get('start_time')) and _number(w.get('end_time'))]
                    # Suspicion only: never repair or accept guessed/compressed times.
                    compressed=(len(timed)>=2 and min(w['start_time'] for w in timed)>=end-first-.5
                        and max(w['end_time'] for w in timed)-min(w['start_time'] for w in timed)<=.25)
                    observed_start=min((w['start_time']+first for w in timed),default=first)
                    observed_end=max((w['end_time']+first for w in timed),default=end)
                    reasons=[]
                    if compressed:reasons.append('tail_word_cluster_compressed')
                    if item['start'] is not None:
                        if item['start']<.15:reasons.append('crop_start_edge')
                        if item['end']>end-first-.15:reasons.append('crop_end_edge')
                        if not review and b<duration and item['end']+first>=b-.15:
                            reasons.append('base_boundary_crossing_or_tail')
                    if reasons:
                        previous=pending_details.get(i,{})
                        pending_boundary.add(i)
                        pending_details[i]=dict(
                            required_start=min(previous.get('required_start',a),max(0.,min(a,observed_start)-2.)),
                            required_end=max(previous.get('required_end',b),min(duration,max(b,observed_end)+2.)),
                            observed_start=observed_start,observed_end=observed_end,reasons=reasons)
                        rejected.append(dict(index=i,reason='boundary_pending_next_overlap',diagnostics=reasons))
                        # A left-edge cue cannot recover by moving only forward.
                        # Queue one bounded local expansion before the next natural window.
                        if 'crop_start_edge' in reasons and not review and review_attempts<2 and not any(j['review'] for j in jobs):
                            jobs.insert(0,dict(a=first,b=end,review=True))
                        continue
                    if item['start'] is None:
                        rejected.append(dict(index=i,reason='invalid_output'));continue
                    start_time=item['start']+first;end_time=item['end']+first
                    prior=[j for j in by_row if j<i];following=[j for j in by_row if j>i]
                    if prior and start_time<by_row[max(prior)][-1]['end_time']-.002:
                        rejected.append(dict(index=i,reason='before_prior_anchor'));continue
                    if following and end_time>by_row[min(following)][0]['start_time']+.002:
                        rejected.append(dict(index=i,reason='after_next_anchor'));continue
                    for word in words:
                        for key in ('start_time','end_time','raw_start_time','raw_end_time'):
                            if _number(word.get(key)):word[key]+=first
                        word.update(source_row_id=row['id'],estimated_window=True)
                    by_row[i]=words;resolved.add(i);pending_boundary.discard(i);pending_details.pop(i,None);accepted.append(i)
                while cursor in resolved:cursor+=1
                reports.append(dict(start=first,end=end,base_start=a,base_end=b,is_boundary_retry=review,context_only_row_ids=context_ids,estimated_k=k,margin=3,leading_margin=3,row_ids=[r['id'] for r in selected],cursor_before=before,cursor_after=cursor,accepted_indices=accepted,pending_boundary_ids=[rows[i]['id'] for i in sorted(pending_boundary)],rejected=rejected))
    except (OSError,wave.Error) as exc:
        reports.append(dict(status='failed',stage='pcm_window_io_or_cleanup',error_code=type(exc).__name__,cursor_after=cursor))
        raise PartialAlignmentError(assembled(),'failed') from exc
    return assembled()


def alignment_reason_counts(record):
    """Manifest uses fixed categories, never source paths or lyric strings."""
    from collections import Counter
    counts=Counter()
    labels=(('未检出有效时间','missing_word_time'),('插值','interpolated_time'),
        ('置信度过低','low_confidence'),('词序与原词不一致','sequence_mismatch'),
        ('跨越了分行边界','cross_line_token'),('边界句未完整','boundary_pending'),
        ('无有效可打轴字符','empty_lyric'),('位于视频尾外','outside_video'),
        ('预计句数宽窗补齐','estimated_window'),('完整诊断导出失败','diagnostic_export_failed'))
    for row in record.get('rows',[]):
        for reason in row.get('reasons',[]):
            if not isinstance(reason,str) or reason.startswith(('audio-sha256:','local-model:','diagnostic-run:')):continue
            for text,code in labels:
                if text in reason:counts[code]+=1;break
    return dict(counts)


def export_alignment_diagnostics(path, *, request, response, candidate, manifest, protected_inputs=()):
    """Explicit user-selected full private export, never an automatic telemetry log."""
    from .storage import atomic_json,protect_output
    target=Path(path)
    if target.suffix.lower()!='.json':raise ValueError('完整诊断请保存为 .json 文件。')
    protect_output(target,protected_inputs)
    payload=dict(format='lyria-alignment-diagnostics-v1',contains_private_lyrics=True,
        request={k:deepcopy(v) for k,v in request.items() if k in ('model_id','language','rows','offset_seconds','duration_seconds','audio_path','use_separation','min_timestamp_score','alignment_strategy')},
        raw_response=deepcopy(response),candidate=deepcopy(candidate),manifest=deepcopy(manifest))
    atomic_json(target,payload,inputs=protected_inputs)
    return str(target)


def generate_vocal_candidate(document, frame_index=None, *, options, cancel=lambda: False,
                             progress=lambda p, m: None):
    """Call on a worker with a detached document; returns a standard candidate."""
    check_cancel(cancel)
    settings = validate_options(document, options)
    export_path=options.get('diagnostic_export_path')
    if export_path is not None:
        from .storage import protect_output,protected_project_inputs
        if not isinstance(export_path,str) or not export_path.strip():raise ValueError('请选择完整诊断保存路径。')
        if Path(export_path).suffix.lower()!='.json':raise ValueError('完整诊断请保存为 .json 文件。')
        protect_output(export_path,protected_project_inputs(document.project)+[settings['audio_path']])
    import uuid
    run_id=str(uuid.uuid4())
    manifest=dict(version=1,run_id=run_id,status='running',model_id=settings['model_id'],
        input=dict(rows=len(settings['rows']),unique_ids=len({r['id'] for r in settings['rows']}),offset=settings['offset_seconds'],requested_duration=settings['duration_seconds']),
        application=dict(status='not_applied'),navigation=dict(status='not_requested'),
        audio_preparation=dict(resampler='ffmpeg-aresample-no-async',format='pcm_s16le',sample_rate=16000,mono=True))
    def save_manifest():write_alignment_manifest(settings['cache_root'],run_id,manifest)
    auxiliary_paths={}
    def save_full_export(response,candidate):
        if not export_path:return
        from .storage import protected_project_inputs
        try:
            export_alignment_diagnostics(export_path,request=settings,response=response,candidate=candidate,manifest=manifest,
                protected_inputs=protected_project_inputs(document.project)+[settings['audio_path']]+list(auxiliary_paths.values()))
            manifest['full_export']=dict(status='saved');save_manifest()
        except (OSError,ValueError) as exc:
            manifest['full_export']=dict(status='failed',error_type=type(exc).__name__)
            try:save_manifest()
            except OSError:pass
            for row in candidate.get('rows',[]):row['reasons'].append('完整诊断导出失败，识别候选仍保留。')
            progress(99,'完整诊断导出失败，请检查保存目录和权限；任务结果仍保留。')
    save_manifest()
    try:
        progress(1, f'输入检查：{len(settings["rows"])} 句原词；' + ('整曲处理' if options.get('duration_seconds') is None else '自定义片段') + '。正在准备音频。')
        audio = Path(settings["audio_path"])
        auxiliary_paths = {"alignment_audio": str(audio)}
        snapshot = capture_source(document, "vocal", frame_index,
                                  auxiliary_paths=auxiliary_paths, cancel=cancel)
        manager = ModelManager(settings["cache_root"])
        manager.ensure_verified(settings["model_id"], cancel=cancel,
                       progress=lambda p, m: progress(2 + int(p * .08), m))
        audio_evidence=snapshot["provenance"]["auxiliary_files"]["alignment_audio"]
        stem = None
        if settings["use_separation"]:
            with tempfile.TemporaryDirectory(prefix='lyria-input-check-') as input_folder:
                input_wav=Path(input_folder)/'original.wav'
                _extract_audio(audio,input_wav,settings['offset_seconds'],settings['duration_seconds'],cancel)
                manifest['input_audio']=audio_activity(input_wav);save_manifest()
            from .local_separation import prepare_vocals
            sep_settings = dict(settings)
            if settings.get("separation_device"):
                sep_settings["device"] = settings["separation_device"]
            else:
                from . import split_profiles
                if getattr(sys, 'frozen', False) and settings.get("device") == "cpu" and split_profiles.ACTIVE == "directml":
                    sep_settings["device"] = "directml"
                elif getattr(sys, 'frozen', False) and settings.get("device") == "cuda:0" and split_profiles.ACTIVE == "amd":
                    sep_settings["device"] = "directml"
            stem=prepare_vocals(sep_settings,cancel=cancel,progress=lambda p,m:progress(10+round(p*.4),m))
            if stem["source_sha256"]!=audio_evidence["sha256"]:
                raise ValueError('人声分离期间原音频文件发生了变化，请重试。')
            auxiliary_paths.update(separated_vocals=stem["path"],separation_manifest=stem["manifest_path"])
            snapshot=capture_source(document,"vocal",frame_index,auxiliary_paths=auxiliary_paths,cancel=cancel)
            if snapshot["provenance"]["auxiliary_files"]["alignment_audio"]["sha256"]!=stem["source_sha256"]:
                raise ValueError('人声分离完成后原音频文件发生了变化，请重试。')
        progress(49 if stem else 10, '分离阶段：' + ('使用已分离人声。' if stem else '按所选模式直接使用原音频。'))
        with tempfile.TemporaryDirectory(prefix="lyria-audio-align-") as folder:
            wav=Path(folder)/"input.wav"
            actual=_extract_audio(stem["path"] if stem else audio,wav,0 if stem else settings["offset_seconds"],stem["duration_seconds"] if stem else settings["duration_seconds"],cancel,gain=stem["alignment_gain"] if stem else 1.)
            if stem and abs(actual-stem["duration_seconds"])>.001:
                raise ValueError('人声重采样后时长异常，请检查音频文件。')
            policy=media_boundary_policy(document,frame_index,audio_evidence,actual,settings['offset_seconds'])
            activity=audio_activity(wav)
            manifest.update(boundary=policy,separation=dict(enabled=stem is not None,source_hash=audio_evidence['sha256'],duration=actual))
            manifest['separated_audio' if stem else 'input_audio']=activity
            save_manifest()
            progress(50, f"媒体范围：音频尾 {policy['audio_end']:.6f} 秒；视频尾 {policy.get('video_end')}；策略 {policy['strategy']}。保留完整音频与静音。")
            if activity['digital_silence']:
                detached=deepcopy(document)
                from .model import Mapping
                detached.project.mappings=[Mapping(row['id'],[],text_override=row['text']) for row in settings['rows']]
                for row in detached.project.mappings:row.reasons=['整段音频为数字静音，无可验证演唱证据','diagnostic-run:'+run_id]
                result=candidate_from_project(snapshot,detached.project)
                manifest.update(status='no_signal',postprocess=dict(total=len(settings['rows']),valid=0,reason='digital_silence'));save_manifest()
                save_full_export(dict(status='not_run',reason='digital_silence'),result)
                progress(100,'音频为静音：未生成可信歌词时间，原时间轴未改动。');return result
            minutes, seconds = divmod(round(actual), 60)
            start_note = f'（从第 {settings["offset_seconds"]:g} 秒开始）' if settings["offset_seconds"] else ''
            progress(50, f'已读入 {minutes} 分 {seconds} 秒音频{start_note}，准备给歌词对时间…')
            request = {key: settings[key] for key in ("model_id", "cache_root", "device", "language", "rows", "min_timestamp_score")}
            if settings['model_id'] == QWEN.id:
                request['rows'] = qwen_acoustic_rows(settings['rows'])
            request["audio_wav"] = str(wav)
            request["tail_silence_guard"] = bool(settings.get("tail_silence_guard",False))
            from .local_alignment_runtime import task_timeout
            interrupted_reason=None
            from .local_alignment_runtime import PartialAlignmentError
            from .japanese_readings import pure_symbol_row
            symbols_only = all(pure_symbol_row(row) for row in settings['rows'])
            if (settings['model_id'] == NEXTFIRE.id and not symbols_only) or (settings['model_id'] == QWEN.id and request['rows']):
                progress(50, '正在启动本地对时引擎，首次加载模型可能需要一些时间…')
            try:
                response = (dict(model_id=NEXTFIRE.id, revision=NEXTFIRE.revision,
                                 duration=actual, rows=[dict(id=row['id'],text=row['text'],
                                     reading=row.get('reading',''),status='nonvocal_symbols',
                                     start_time=None,end_time=None,token_spans=[])
                                     for row in settings['rows']])
                            if settings['model_id'] == NEXTFIRE.id and symbols_only else
                            dict(model_id=QWEN.id, revision=QWEN.revision,
                                 duration=actual, words=[], status='nonvocal_symbols')
                            if settings['model_id'] == QWEN.id and not request['rows'] else
                            run_worker(request, settings["runtime_python"], cancel=cancel,
                                      timeout=task_timeout(actual, 'alignment'),
                                      progress=lambda p,m:progress(50+int(p*(.25 if settings['model_id']==QWEN.id and settings.get('alignment_strategy','anchor_then_estimated_cursor')=='anchor_then_estimated_cursor' else .48)),m)))
                if request['rows'] and settings['model_id']==QWEN.id and settings.get('alignment_strategy','anchor_then_estimated_cursor')=='anchor_then_estimated_cursor':
                    response=refine_alignment_with_cursor(request,response,settings['runtime_python'],cancel=cancel,progress=progress)
            except PartialAlignmentError as exc:
                response=exc.result;interrupted_reason=exc.reason
        if interrupted_reason is None:check_cancel(cancel)
        verification_cancel=(lambda:False) if interrupted_reason else cancel
        if not _number(response.get("duration")) or abs(response["duration"] - actual) > .01:
            raise ValueError('模型返回的音频时长与输入不一致，请重试。')
        # Verify original project sources again after inference; no stale candidate.
        from .candidate_tracks import verify_provenance
        if verify_provenance(document, frame_index, auxiliary_paths=auxiliary_paths, cancel=verification_cancel) != snapshot["provenance"]:
            raise ValueError('自动打轴期间素材文件发生了变化，请重新运行打轴。')
        converter = candidate_from_nextfire if settings["model_id"]==NEXTFIRE.id else candidate_from_alignment
        candidate_snapshot=snapshot
        if settings.get("text_source","main") in ("reference","external"):
            from .model import Mapping
            detached=deepcopy(document)
            known_lyrics={row.id for row in detached.project.lyrics}
            # An external file's original IDs are hints for exact source reuse, not
            # proof that its Lyric records exist in this project's evidence table.
            detached.project.mappings=[Mapping(row["id"],[],lyric_ids=[identity for identity in row.get("lyric_ids",[]) if identity in known_lyrics],text_override=row["text"],text_role="source_import") for row in settings["rows"]]
            candidate_snapshot=capture_source(detached,"vocal",frame_index,auxiliary_paths=auxiliary_paths,cancel=verification_cancel)
        result=converter(candidate_snapshot,settings["rows"],response,offset_seconds=settings["offset_seconds"],min_timestamp_score=settings["min_timestamp_score"],audio_evidence=audio_evidence)
        if candidate_snapshot is not snapshot:
            result = rebind_source_candidate(snapshot, result, settings['rows'])
        result=constrain_candidate_to_media(result,policy)
        for row in result['rows']:row['reasons'].append('diagnostic-run:'+run_id)
        if interrupted_reason:
            for row in result['rows']:row['reasons'].append('interrupted-run:'+interrupted_reason)
        counts = alignment_stage_diagnostics(response, result)
        model_raw_words=sum(len(batch.get('response',{}).get('words',[])) for batch in response.get('raw_batches',[]))
        if not response.get('raw_batches'):model_raw_words=counts['raw_words']
        reason_counts=alignment_reason_counts(result)
        manifest.update(status='interrupted' if interrupted_reason else counts['status'],postprocess=dict(counts,reason_counts=reason_counts),raw_output=dict(model_words=model_raw_words,merged_words=counts['raw_words']),
            segmentation={k:deepcopy(v) for k,v in response.get('chunking',{}).items() if k in ('strategy','qwen_batch_limit_seconds','coarse_asr','covered_until_seconds','cursor_refinement','refinement_error')},
            chunks=[{k:v for k,v in b.items() if k in ('start','end','row_ids','output_row_ids','context_only_row_ids','status','error_code','cursor_before','cursor_after','expected_rows_from_anchors','pending_row_ids','is_boundary_retry')} for b in response.get('chunking',{}).get('batches',[])])
        save_manifest()
        if interrupted_reason and not counts['candidate_valid']:
            if interrupted_reason=='failed':raise RuntimeError('对齐中断于失败阶段，已完成分段也未产生有效时间；未保留候选。')
            raise InterruptedError('任务已取消，已完成分段也未产生有效时间；未保留候选。')
        save_full_export(response,result)
        progress(99, f"模型与过滤检查：分段批次 {counts['batches']}，合并后词 / 行 {counts['raw_words']}，其中有效时间 {counts['raw_timed_words']}；候选 {counts['candidate_total']} 句，有效 {counts['candidate_valid']}、未定位 {counts['candidate_missing']}；可绑定既有主轴 {counts['bound_rows']}、独立候选 {counts['unbound_rows']}。以上不是准确率，尚未写入主轴。")
        progress(100, alignment_result_summary(result)['message'])
        return result
    except Exception as exc:
        manifest.update(status='cancelled' if isinstance(exc,InterruptedError) else 'failed',error=dict(type=type(exc).__name__))
        try:save_manifest()
        except OSError:pass
        save_full_export(dict(status='failed',error_type=type(exc).__name__,message=str(exc)),dict(rows=[]))
        raise
