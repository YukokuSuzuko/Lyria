"""Headless QC orchestration: local pixels, honest PTS evidence, text-only review.

The desktop must confirm each ROI before a text-mode run. A coarse OCR proposal
is never frame evidence: every candidate boundary is independently examined on
adjacent decoded frames. This module does not upload media or persist secrets.
"""
from __future__ import annotations

from bisect import bisect_left
from contextlib import contextmanager
from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timezone
import uuid
from pathlib import Path
import math
import os
import shutil
import subprocess
import tempfile
import time

from .storage import Cache, protect_output, file_hash
from .vision import role_ocr_settings, Video, scan, make_ocr, propose_regions, ocr_similarity, resources, box_overlap


API_DISCLOSURE = '仅发送识别文本与时间轴信息，不上传视频或截图；AI 结论仅供参考。'
LOCAL_NOTICE = '当前仅执行本地画面检查；如需核对双语语义，可在设置中启用文本 API。'


def _cancelled(cancel):
    if cancel():
        raise InterruptedError('质检已取消。')


def _file(path, label):
    if not path or not Path(path).is_file():
        raise ValueError(f'{label}不存在或无法读取')
    return Path(path).resolve()


def _language(value,label):
    if not isinstance(value,str) or not value.strip() or len(value)>80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-' for c in value):
        raise ValueError(label+' OCR 语言设置无效')
    return value.strip()


def fingerprint_inputs(inputs,cancel=lambda:False):
    """Content versions, not timestamps/filenames, bind follow-up evidence."""
    versions={};digests={}
    for role,value in inputs.items():
        if not value:continue
        _cancelled(cancel)
        path=_file(value,role)
        key=str(path)
        if key not in digests:digests[key]=file_hash(path,cancel)
        versions[role]={'path':key,'sha256':digests[key],'bytes':path.stat().st_size}
    return versions


def verify_input_versions(report,cancel=lambda:False):
    expected=report.get('input_versions')
    inputs={k:v for k,v in report.get('inputs',{}).items() if v}
    if not isinstance(expected,dict) or set(expected)!=set(inputs):
        raise ValueError('原质检报告缺少完整的素材校验信息，请重新发起完整质检')
    actual=fingerprint_inputs(inputs,cancel)
    for role,value in actual.items():
        if not isinstance(expected[role],dict) or value['sha256']!=expected[role].get('sha256'):
            raise ValueError(role+' 文件内容已变化，无法在原报告上局部重查，请重新运行完整质检')
    return actual


def validate_region(value, width, height, *, label='字幕', required=True):
    """Pixel-coordinate ROI; candidates must have an explicit confirmation."""
    if value is None:
        if required:
            raise ValueError(f'请先框选并确认{label}字幕区域')
        return {'box': [0, 0, width, height], 'origin': 'full_frame', 'confirmed': False}
    if not isinstance(value, dict) or set(value) - {'box', 'origin', 'confirmed'}:
        raise ValueError(f'{label}区域格式无效')
    box = value.get('box')
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or not all(type(n) is int for n in box)
            or not 0 <= box[0] < box[2] <= width
            or not 0 <= box[1] < box[3] <= height):
        raise ValueError(f'请在视频画面范围内框选有效的{label}区域')
    if value.get('origin', 'user') not in ('user', 'candidate'):
        raise ValueError('选区数据无效，请重新框选')
    if value.get('confirmed') is not True:
        raise ValueError(f'请先确认{label}区域')
    return {'box': list(box), 'origin': value.get('origin', 'user'), 'confirmed': True}


def validate_regions(chinese, source, width, height, *, mode):
    if mode not in ('local', 'text'):
        raise ValueError('质检模式设置无效')
    zh = validate_region(chinese, width, height, label='中文', required=mode == 'text')
    src = validate_region(source, width, height, label='原文', required=True) if source is not None or mode == 'text' else None
    if src is not None:
        a, b = zh['box'], src['box']
        if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
            raise ValueError('中文字幕区与原文区不能重叠，请分别框选')
    return zh, src


@dataclass(frozen=True)
class AuditClock:
    """Every decoded video PTS, plus the actual last-frame duration if available.

    ``end`` is None when the decoder does not expose that last duration. Neither
    average FPS nor a container duration estimate may complete this evidence.
    """
    stamps: tuple[float, ...]
    raw_pts: tuple[int, ...]
    time_bases: tuple[str, ...]
    end: float | None
    origin: float = 0.0

    def __post_init__(self):
        if (not self.stamps or len(self.stamps) != len(self.raw_pts)
                or len(self.stamps) != len(self.time_bases)
                or any(not math.isfinite(t) for t in self.stamps)
                or any(b <= a for a, b in zip(self.stamps, self.stamps[1:]))
                or (self.end is not None and (not math.isfinite(self.end) or self.end <= self.stamps[-1]))):
            raise ValueError('视频帧时间轴无效')

    @classmethod
    def build(cls, video, cancel=lambda: False):
        import av
        stamps, pts, bases, endpoint = [], [], [], None
        _cancelled(cancel)
        with av.open(video.path) as container:
            stream = container.streams.video[0]
            stream.thread_type = 'AUTO'
            for frame in container.decode(stream):
                _cancelled(cancel)
                if frame.pts is None or frame.time_base is None:
                    raise ValueError('视频帧缺少时间戳信息，无法执行逐帧质检，建议先转码为标准 MP4')
                stamp = float(frame.pts * frame.time_base) - video.origin
                stamps.append(stamp)
                pts.append(frame.pts)
                bases.append(str(frame.time_base))
                duration = getattr(frame, 'duration', None)
                endpoint = stamp + float(duration * frame.time_base) if duration and duration > 0 else None
        _cancelled(cancel)
        return cls(tuple(stamps), tuple(pts), tuple(bases), endpoint, video.origin)

    def frame(self, stamp):
        i = bisect_left(self.stamps, stamp - 1e-7)
        if i >= len(self.stamps) or abs(self.stamps[i] - stamp) > 1e-6:
            raise ValueError('采样时间与视频帧时间不匹配')
        return i + 1

    def activation(self, stamp):
        """First decoded frame on which a half-open subtitle boundary acts."""
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not math.isfinite(stamp):
            raise ValueError('参考时间数值无效')
        return bisect_left(self.stamps, stamp - 1e-7) + 1

    def evidence(self, frame):
        if type(frame) is not int or not 1 <= frame <= len(self.stamps):
            raise ValueError('所选帧号超出视频总帧数范围')
        return {'frame': frame, 'seconds': self.stamps[frame-1],
                'pts': self.raw_pts[frame-1], 'time_base': self.time_bases[frame-1]}


def timing_delta(cue, reference, clock=None):
    """One *actual frame* tolerance. Never changes either interval."""
    first, last = cue.get('first_frame'), cue.get('last_frame')
    rf, rl = reference.get('first_frame'), reference.get('last_frame')
    if clock is not None and (rf is None or rl is None):
        a, b = reference.get('start'), reference.get('end')
        if a is not None and b is not None and a < b:
            rf, rl = clock.activation(a), clock.activation(b) - 1
    numeric = all(type(n) is int and n >= 1 for n in (first, last, rf, rl))
    result = {'computed_by': 'local', 'tolerance_frames': 1, 'status': 'needs_review'}
    for side in ('start', 'end'):
        a, b = cue.get(side), reference.get(side)
        result[side+'_seconds'] = a-b if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
    if numeric and first <= last and rf <= rl:
        result.update(start_frames=first-rf, end_frames=last-rl,
                      within_tolerance=abs(first-rf) <= 1 and abs(last-rl) <= 1)
        strong = (cue.get('boundary_status') == 'adjacent_decoded_frames'
                  and cue.get('confidence', 0) >= .8
                  and reference.get('boundary_status', 'reference_only') == 'adjacent_decoded_frames'
                  and reference.get('confidence', 0) >= .8)
        if strong:
            result['status'] = 'within_tolerance' if result['within_tolerance'] else 'timing_suspect'
        else:
            result['reason'] = '至少一侧仅为参考时间或识别置信度偏低，时间差仅供参考'
    else:
        result['reason'] = '缺少逐帧进出点信息，无法按帧级精度比对'
    return result


def neighbor_relation(previous, current):
    first, last = current.get('first_frame'), previous.get('last_frame')
    if any(type(n) is not int for n in (first,last,previous.get('first_frame'),current.get('last_frame'))):
        return {'status': 'needs_review', 'computed_by': 'local', 'gap_frames': None, 'overlap_frames': None}
    overlap = max(0, min(previous['last_frame'], current['last_frame']) - max(previous['first_frame'], current['first_frame']) + 1)
    return {'status': 'measured' if previous.get('boundary_status') == current.get('boundary_status') == 'adjacent_decoded_frames' else 'needs_review',
            'computed_by': 'local', 'previous_id': previous['id'],
            'gap_frames': max(0, first-last-1), 'overlap_frames': overlap,
            'gap_seconds': current['start']-previous['end'] if previous.get('end') is not None else None}


def _recognized(provider, frame, roi):
    x1, y1, x2, y2 = roi
    detections = provider.recognize(frame[y1:y2, x1:x2])
    result = []
    for d in detections:
        confidence = float(d.confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('OCR 返回的置信度数值异常')
        result.append({'text': d.text, 'confidence': confidence,
                       'box': [d.box[0]+x1, d.box[1]+y1, d.box[2]+x1, d.box[3]+y1]})
    return result


def refine_cues(video, visuals, clock, region, settings, *, cancel=lambda: False,
                progress=lambda p, m: None, provider=None, track='chinese',metrics=None):
    """OCR each decoded frame in candidate-boundary neighborhoods.

    Empty OCR is *absence evidence*, not proof: all uncertainty stays attached.
    No interpolation, whole-track one-frame shift, or average-FPS conversion.
    """
    # Explicit per-track language wins; do not let a coarse OCR hypothesis
    # select the recognition language or bias its own boundary recheck.
    provider = provider or make_ocr(settings)
    sample_fps = float(settings.get('sample_fps', 3))
    if not math.isfinite(sample_fps) or not .5 <= sample_fps <= 15:
        raise ValueError('OCR 扫描帧率设置无效（需在 0.5～15 之间）')
    radius = max(2 / sample_fps, .2)
    from .qc_boundaries import BoundaryObservations
    stats=metrics if metrics is not None else {}
    reader=BoundaryObservations(video,clock,visuals,radius,lambda frame:_recognized(provider,frame,region['box']),cancel,progress,track,stats)
    neighborhood=reader.neighborhood
    try:
        cues = []
        for i, visual in enumerate(visuals):
            _cancelled(cancel)
            notes = list(visual.warnings)
            boundary, raw_rows = {}, {}
            chosen_first, chosen_last = None, None
            for side, center in (('start', visual.start), ('end', visual.end)):
                rows = neighborhood(center)
                for row in rows:
                    raw_rows[row['frame']] = row
                def matches(row):
                    return [(d, ocr_similarity(d['text'], visual.text)) for d in row['ocr']
                            if ocr_similarity(d['text'], visual.text) >= .7 and box_overlap(d['box'],visual.box)>=.3]
                visible = [j for j, row in enumerate(rows) if matches(row)]
                if not visible:
                    boundary[side] = {'status': 'unresolved', 'observations': rows}
                    notes.append(side+' 进出点精查未检出清晰文字，暂保留粗扫时间')
                    continue
                anchor = min(visible, key=lambda j: abs(rows[j]['seconds']-center))
                direction = -1 if side == 'start' else 1
                edge = anchor
                while 0 <= edge+direction < len(rows) and matches(rows[edge+direction]):
                    edge += direction
                visible_row = rows[edge]
                neighbor = edge + direction
                adjacent = rows[neighbor] if 0 <= neighbor < len(rows) else None
                adjacent_ok = adjacent is not None and abs(adjacent['frame']-visible_row['frame']) == 1
                clip_last=side=='end' and visible_row['frame']==len(clock.stamps)
                limit_ok = side == 'start' and visible_row['frame'] == 1 or clip_last and clock.end is not None
                confidence = max(d['confidence'] for d, score in matches(visible_row))
                status = 'adjacent_decoded_frames' if (adjacent_ok or limit_ok) and confidence >= .8 else 'needs_review'
                if adjacent and any(d['confidence'] < .8 for d in adjacent['ocr']):
                    status = 'needs_review'
                if not adjacent_ok and not limit_ok and not clip_last:
                    notes.append(side+' 字幕超出局部扫描范围，进出点待核对')
                if confidence < .8:
                    notes.append(side+' 进出点附近识别置信度偏低（可能有渐变或遮挡）')
                boundary[side] = {'status': status, 'visible': visible_row,
                                  'adjacent_nonmatching': adjacent,
                                  'clip_edge': bool(limit_ok or clip_last),'clip_endpoint_known':not clip_last or clock.end is not None, 'confidence': confidence,
                                  'rule': 'local OCR match >=0.7; actual adjacent decoded frame; absence is not proof'}
                if side == 'start':
                    chosen_first = visible_row['frame'] if adjacent_ok or limit_ok else None
                else:
                    chosen_last = visible_row['frame'] if adjacent_ok or limit_ok or clip_last else None
            precise = chosen_first is not None and chosen_last is not None and chosen_first <= chosen_last
            start = clock.stamps[chosen_first-1] if chosen_first is not None else visual.start
            end = (clock.stamps[chosen_last] if chosen_last < len(clock.stamps) else clock.end) if chosen_last is not None else visual.end
            if end is None:
                notes.append('视频末帧缺少时长信息，无法确定精确结束时间')
            if not precise:
                notes.append('进出帧尚未完全闭合，请结合质检截图核对')
            status = 'adjacent_decoded_frames' if precise and all(boundary[s]['status'] == 'adjacent_decoded_frames' for s in ('start', 'end')) else 'needs_review'
            raw_conf = [d['confidence'] for row in raw_rows.values() for d in row['ocr'] if ocr_similarity(d['text'], visual.text) >= .7]
            confidence = min([visual.confidence] + raw_conf) if raw_conf else visual.confidence
            representative=clock.evidence(chosen_first) if chosen_first is not None else min(raw_rows.values(),key=lambda row:abs(row['seconds']-visual.start)) if raw_rows else None
            cue = {'id': track+'-'+visual.id, 'track': track, 'text': visual.text,
                   'start': start, 'end': end, 'first_frame': chosen_first, 'last_frame': chosen_last,
                   'confidence': confidence, 'raw_confidences': raw_conf,
                   'ocr_text_variants':sorted({d['text'] for row in raw_rows.values() for d in row['ocr'] if ocr_similarity(d['text'],visual.text)>=.7 and box_overlap(d['box'],visual.box)>=.3}),
                   'evidence': '本地选区 OCR 与进出点逐帧识别',
                   'evidence_kind': 'local_ocr', 'boundary_status': status,
                   'content_status':'ocr_completeness_unverified',
                   'boundary_evidence': boundary, 'raw_observations': sorted(raw_rows.values(), key=lambda r:r['frame']),
                   'coarse_estimate': {'start': visual.start, 'end': visual.end, 'sample_fps': sample_fps},
                   'warnings': list(dict.fromkeys(notes)), 'review_status': 'needs_review',
                   'representative_frame':representative['frame'] if representative else None,
                   'representative_seconds':representative['seconds'] if representative else None,
                   'jump': {'frame': chosen_first if chosen_first is not None else representative['frame'] if representative else None,
                            'seconds': start if chosen_first is not None else representative['seconds'] if representative else None,
                            'kind':'boundary' if chosen_first is not None else 'sample_only','track': track}}
            cues.append(cue)
            reader.last_progress=max(reader.last_progress,(i+1)/max(1,len(visuals)))
            progress(reader.last_progress, f'{track}：逐帧核对进出点 {i + 1}/{len(visuals)}')
        cues.sort(key=lambda c: c['start'])
        for a, b in zip(cues, cues[1:]):
            b['previous_relation'] = neighbor_relation(a, b)
        return cues
    finally:reader.close()


def ffmpeg_executable():
    packaged = resources() / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    candidate = packaged if packaged.is_file() else shutil.which('ffmpeg')
    if not candidate:
        raise RuntimeError('未找到内置 FFmpeg（含 libass），无法渲染外挂字幕，请使用完整安装包')
    return str(candidate)


def _run_render(command, folder, target, cancel, timeout, max_bytes):
    """Bounded subprocess, no unbounded stderr pipe, always reaped on cancel."""
    started = time.monotonic()
    with (Path(folder)/'render.log').open('w+b') as log:
        process = subprocess.Popen(command, cwd=folder, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while process.poll() is None:
                _cancelled(cancel)
                if time.monotonic()-started > timeout:
                    raise TimeoutError('外挂字幕临时压制超时，请检查视频长度或字幕特效复杂度')
                if log.tell()>32*1024**2:raise ValueError('外挂字幕渲染报错过多，请检查 ASS 字幕文件是否有误')
                if target.exists() and target.stat().st_size > max_bytes:
                    raise ValueError('外挂字幕临时压制文件过大，超出缓存空间上限')
                time.sleep(.05)
            _cancelled(cancel)
            if process.returncode or not target.is_file() or not target.stat().st_size:
                raise RuntimeError('外挂字幕渲染失败，请检查 ASS 字幕格式或字体设置')
            if target.stat().st_size > max_bytes:
                raise ValueError('外挂字幕临时压制文件过大，超出缓存空间上限')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


@contextmanager
def rendered_input(video_path, ass_path=None, *, input_kind='hardsubbed',
                   cancel=lambda: False, temp_parent=None, timeout=1800, max_bytes=8*1024**3):
    video = _file(video_path, '视频')
    if input_kind not in ('hardsubbed', 'raw'):
        raise ValueError('请选择“已压制硬字幕 · 最终成片”或“裸视频 + 外挂字幕 · 临时预检”')
    if input_kind == 'hardsubbed':
        _cancelled(cancel)
        yield video, {'kind': 'finished_video', 'temporary': False, 'rendered': False,
                      'label': '硬字幕成片本地质检'}
        return
    ass = _file(ass_path, '外挂 ASS 字幕')
    if ass.stat().st_size>16*1024**2:raise ValueError('外挂字幕文件超过 16 MB 大小限制')
    if ass.suffix.lower() not in ('.ass', '.ssa'):
        raise ValueError('外挂字幕质检仅支持 .ass 或 .ssa 格式')
    if not math.isfinite(timeout) or not 0 < timeout <= 7200 or type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('外挂字幕渲染参数设置无效')
    _cancelled(cancel)
    with tempfile.TemporaryDirectory(prefix='lyria-qc-', dir=temp_parent) as folder:
        folder = Path(folder)
        shutil.copyfile(ass, folder/'overlay.ass')
        target = folder/'precheck.mkv'
        protect_output(target, [video, ass])
        command = [ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-protocol_whitelist', 'file,pipe', '-copyts', '-i', str(video), '-map', '0:v:0', '-map', '0:a?',
                   '-vf', 'subtitles=overlay.ass', '-c:v', 'ffv1', '-c:a', 'copy',
                   '-fps_mode', 'passthrough', '-n', str(target)]
        _run_render(command, folder, target, cancel, timeout, max_bytes)
        yield target, {'kind': 'temporary_render_precheck', 'temporary': True, 'rendered': True,
                       'label': '外挂字幕临时压制预检',
                       'cleanup': 'automatic_after_job', 'timing_basis': 'actual_decoded_temporary_render',
                       'limits': {'seconds': timeout, 'bytes': max_bytes}}


def propose_audit_regions(video_path, *, settings=None, cancel=lambda: False):
    """Unconfirmed suggestions only; UI/user must assign and confirm both roles."""
    video = _file(video_path, '视频')
    provider = make_ocr(settings or {})
    return [{**candidate, 'origin': 'candidate', 'confirmed': False}
            for candidate in propose_regions(video, provider, cancel)]


def run_audit(video_path, translation_path, output_dir, *, mode='local', chinese_roi=None,
              source_roi=None, ass_path=None, input_kind='hardsubbed', settings=None,
              project_path=None, ktv_path=None, cancel=lambda: False, progress=lambda p,m: None,
              semantic_runner=None, frame_range=None, expected_ids=None, cache_dir=None):
    """Blocking worker entry point; GUI supplies cancellation/progress callbacks.

    semantic_runner(report, output_dir, cancel=..., progress=...) is called only
    in explicitly selected text mode, after all local evidence has been saved.
    It may modify report but must perform only text semantic inspection.
    """
    if (settings or {}).get('audit_strategy') == 'sentence':
        from .audit_sentences import run_sentence_audit
        return run_sentence_audit(video_path, translation_path, output_dir, mode=mode,
            chinese_roi=chinese_roi, source_roi=source_roi, ass_path=ass_path, input_kind=input_kind,
            settings=settings, project_path=project_path, ktv_path=ktv_path, cancel=cancel,
            progress=progress, semantic_runner=semantic_runner, frame_range=frame_range,
            expected_ids=expected_ids, cache_dir=cache_dir)
    from .audit import local_audit, save_report
    # A supplied Chinese ASS is itself a valid expected-text reference; do not
    # force users to manufacture a redundant TXT file for a raw-video precheck.
    translation_path=translation_path or ass_path
    video_path, translation_path = _file(video_path, '视频'), _file(translation_path, '译文文件')
    for value, label in ((ass_path, '字幕'), (project_path, '工程'), (ktv_path, '原文')):
        if value:
            _file(value, label)
    if mode == 'text' and semantic_runner is None:
        raise ValueError('AI 语义服务尚未配置，请先填写 API 密钥，或改用纯本地质检')
    options = dict(settings or {})
    options['chinese_language']=_language(options.get('chinese_language','chi_sim'),'中文')
    options['source_language']=_language(options.get('source_language','jpn+eng'),'原文')
    for role in ('chinese', 'source'):
        options[role + '_engine'] = role_ocr_settings(options, role, options[role + '_language'])['engine']
    output = Path(output_dir)
    inputs = [video_path, translation_path, ass_path, project_path, ktv_path]
    for name in ('audit.cache.sqlite', 'audit.cache.sqlite-wal', 'audit.cache.sqlite-shm', 'report.json', 'report.html'):
        protect_output(output/name, inputs)
    versions=fingerprint_inputs({'video':video_path,'translation':translation_path,'ass':ass_path,'project':project_path,'ktv':ktv_path},cancel)
    original = Video(video_path)
    zh, source = validate_regions(chinese_roi, source_roi, original.info['width'], original.info['height'], mode=mode)
    _cancelled(cancel)
    output.mkdir(parents=True, exist_ok=True)
    progress(0, '正在读取视频帧 · '+(API_DISCLOSURE if mode == 'text' else LOCAL_NOTICE))
    with rendered_input(video_path, ass_path, input_kind=input_kind, cancel=cancel,
                        timeout=options.get('render_timeout', 1800), max_bytes=options.get('render_max_bytes', 8*1024**3)) as (scan_path, stage):
        video = Video(scan_path)
        clock = AuditClock.build(video, cancel)
        if stage['rendered']:
            source_clock=AuditClock.build(original,cancel)
            if len(source_clock.stamps)!=len(clock.stamps):
                raise ValueError('外挂字幕临时压制后总帧数与原视频不一致，已停止质检，请检查视频编码')
            deltas=[actual-source for actual,source in zip(clock.stamps,source_clock.stamps)]
            stage['source_timeline_comparison']={'source_frames':len(source_clock.stamps),
                'rendered_frames':len(clock.stamps),'max_abs_pts_delta_seconds':max(map(abs,deltas)),
                'exact_pts_preserved':all(abs(delta)<1e-7 for delta in deltas),
                'note':'使用临时文件实际 PTS；容器 time base 可能量化，不能当作最终成片或原文件的逐秒相同副本'}
        scope_detail={'kind':'whole_video_candidates','complete_video_verified':False}
        if frame_range is not None:
            if (not isinstance(frame_range,(list,tuple)) or len(frame_range)!=2
                    or any(type(n) is not int for n in frame_range)
                    or not 1<=frame_range[0]<=frame_range[1]<=len(clock.stamps)):
                raise ValueError('局部检查的起止帧范围无效')
            first,last=frame_range
            left=clock.stamps[first-1]
            right=clock.stamps[last] if last<len(clock.stamps) else video.info['duration']
            if right<=left:raise ValueError('所选局部检查帧范围过短，请扩大起止帧范围')
            options['scan_range']=[max(0,left),right]
            scope_detail={'kind':'local_rescan','first_frame':first,'last_frame':last,
                'start':clock.evidence(first),'last':clock.evidence(last),
                'discovery_seconds':[max(0,left),right],
                'adjacent_evidence_may_extend_range':True,'result_status':'candidates_not_adopted',
                'complete_video_verified':False,
                'processing_scope':'仅扫描所选帧范围（外挂字幕预检仍需生成临时视频）'}
        cache_path=(Path(cache_dir)/'audit-local.cache.sqlite') if cache_dir is not None else output/'audit.cache.sqlite'
        for suffix in ('','-wal','-shm'):protect_output(str(cache_path)+suffix,inputs)
        # Legacy coarse results bind to PTS rather than exact crop bytes. Keep
        # those per-report; only independently verifiable pixel OCR is shared.
        with_cache = Cache(output/'audit.cache.sqlite')
        ocr_cache=Cache(cache_path) if cache_dir is not None else with_cache
        tracks = {};runtime_metrics={};providers={}
        try:
            roles = [('chinese', zh, options['chinese_language'])]
            if source is not None:
                roles.append(('source', source, options.get('source_language', 'jpn+eng')))
            for number, (role, region, language) in enumerate(roles):
                _cancelled(cancel)
                scan_options = {**role_ocr_settings(options, role, language), 'roi': region['box']}
                from .qc_ocr_cache import CachedOCR
                tick=time.perf_counter()
                # Sequential roles own their models; no shared mutable engine
                # crosses worker threads or user-selected language boundaries.
                providers[role]=make_ocr(scan_options)
                provider=CachedOCR(providers[role],ocr_cache,file_hash(scan_path,cancel),region['box'],scan_options,cancel)
                model_seconds=time.perf_counter()-tick
                # Coarse discovery and exact boundary verification share only
                # identical-input OCR; their independent evidence rules remain.
                visuals, scan_info = scan(scan_path, scan_options, with_cache, (),
                                  lambda p,m:progress((number+p*.6)/len(roles)*.9, m), cancel,provider=provider)
                boundary_metrics={}
                tracks[role] = refine_cues(video, visuals, clock, region, scan_options,
                    cancel=cancel, track=role,provider=provider,metrics=boundary_metrics,
                    progress=lambda p,m:progress((number+.6+p*.4)/len(roles)*.9, m))
                runtime_metrics[role]={'model_and_identity_seconds':model_seconds,'scan':scan_info.get('ocr_metrics',{}),'boundary':boundary_metrics,'ocr_reuse':dict(provider.metrics)}
        finally:
            if ocr_cache is not with_cache:ocr_cache.close()
            with_cache.close()
        report = local_audit(video_path, translation_path, output, ass_path=ass_path,
            project_path=project_path, ktv_path=ktv_path, settings=options, cancel=cancel,
            _observed=tracks['chinese'], _source_cues=tracks.get('source', []),
            _clock=clock, _video_info=video.info, _stage=stage,_expected_ids=expected_ids)
        report['local_performance']=runtime_metrics
        report['input_versions']=fingerprint_inputs(report['inputs'],cancel)
        if any(report['input_versions'][role]['sha256']!=version['sha256'] for role,version in versions.items()):
            raise ValueError('质检期间素材文件发生了变化，请重新运行质检')
        report['ocr_settings']={key:options.get(key) for key in ('engine','chinese_engine','source_engine','chinese_language','source_language','sample_fps','visibility','ocr_device','ocr_threads','ocr_model','ocr_preprocess') if options.get(key) is not None}
        report['scope_detail']=scope_detail
        if frame_range is not None:
            for cue in report['cues']+report.get('source_cues',[]):
                cue['scope_relation']={'requested_first':frame_range[0],'requested_last':frame_range[1],
                    'crosses_start':cue.get('first_frame') is not None and cue['first_frame']<frame_range[0],
                    'crosses_end':cue.get('last_frame') is not None and cue['last_frame']>frame_range[1],
                    'boundary_uncertain':cue.get('boundary_status')!='adjacent_decoded_frames'}
        if frame_range is not None:
            report['warnings'].append('本次仅重查所选帧范围，重查结果不会自动覆盖原报告')
        if stage.get('source_timeline_comparison',{}).get('exact_pts_preserved') is False:
            report['warnings'].append('临时预检视频存在轻微时间戳舍入误差，帧序号不受影响，最终请以成片为准')
        if mode=='text' and not tracks.get('source'):
            report['warnings'].append('原文选区未识别出文字，无法完成双语对比，请检查选区位置、识别语言或画面遮挡')
        report['warnings'].append('OCR 识别可能存在漏字或误识，可疑处请结合质检截图复核')
        report.update(mode='burned_video' if input_kind=='hardsubbed' else 'external_ass', qc_mode=mode, regions={'chinese': zh, 'source': source},
                      disclosure=API_DISCLOSURE, timing_policy={'tolerance_frames': 1,
                      'timestamp_source': 'actual decoded PTS/time base', 'global_shift_applied': False})
        # Keep local evidence frames even though the temporary rendered movie is
        # removed. Each file is private output; no frame bytes enter API payloads.
        import cv2
        evidence_dir = output/'frames'
        evidence_dir.mkdir(exist_ok=True)
        saved_frames={}
        evidence_bytes=0
        evidence_limit=options.get('evidence_max_bytes',512*1024**2)
        if type(evidence_limit) is not int or evidence_limit<=0:raise ValueError('质检截图缓存上限设置无效')
        for cue in report['cues'] + report.get('source_cues', [])+report.get('unobserved_cues',[]):
            _cancelled(cancel)
            needed={}
            if cue.get('first_frame') is None and cue.get('representative_frame') is not None:
                needed['sample_only']=cue['representative_frame']
            needed.update(cue.get('reference_sample_frames',{}))
            for side, detail in cue.get('boundary_evidence',{}).items():
                for kind in ('visible','adjacent_nonmatching'):
                    item=detail.get(kind)
                    if item:
                        label=side+'_'+kind
                        if kind=='visible' and item['frame']!=cue.get('first_frame' if side=='start' else 'last_frame'):
                            label=side+'_sample_only'
                        needed[label]=item['frame']
            if cue.get('first_frame') is not None:needed.setdefault('start_visible',cue['first_frame'])
            if not needed and cue.get('representative_frame') is not None:
                needed['sample_only']=cue['representative_frame']
            cue['evidence_frames']=[]
            for label,number in needed.items():
                _cancelled(cancel)
                if number not in saved_frames:
                    target=protect_output(evidence_dir/f'frame-{number:09d}.png',inputs)
                    stamp=clock.stamps[number-1]
                    actual,frame=video.at(stamp,cancel=cancel,last_pts=clock.stamps[-1])
                    if frame is None or abs(actual-stamp)>1e-6:
                        cue['warnings'].append('未能定位到对应视频帧，未保存该帧截图')
                        continue
                    ok,buffer=cv2.imencode('.png',frame)
                    if not ok:raise RuntimeError('保存质检截图失败')
                    if evidence_bytes+len(buffer)>evidence_limit:
                        cue['warnings'].append('质检截图已达空间上限，后续帧仅保留文字识别记录')
                        continue
                    fd,name=tempfile.mkstemp(prefix='.qc-frame-',suffix='.tmp',dir=evidence_dir)
                    try:
                        with os.fdopen(fd,'wb') as stream:stream.write(buffer.tobytes())
                        protect_output(target,inputs)
                        os.replace(name,target)
                    finally:Path(name).unlink(missing_ok=True)
                    evidence_bytes+=len(buffer)
                    saved_frames[number]={'path':str(target),**clock.evidence(number),'stage':stage['kind']}
                cue['evidence_frames'].append({'label':label,**saved_frames[number]})
            first=next((f for f in cue['evidence_frames'] if f['label']=='start_visible'),None) or next(iter(cue['evidence_frames']),None)
            if first and evidence_bytes+Path(first['path']).stat().st_size<=evidence_limit:
                evidence_bytes+=Path(first['path']).stat().st_size
                # Keep the legacy per-cue thumbnail name for existing report/UI
                # viewers; no symlink or hardlink is followed during replacement.
                target=protect_output(evidence_dir/(cue['id']+'.png'),inputs)
                fd,name=tempfile.mkstemp(prefix='.qc-cue-',suffix='.tmp',dir=evidence_dir)
                try:
                    with os.fdopen(fd,'wb') as stream:stream.write(Path(first['path']).read_bytes())
                    protect_output(target,inputs);os.replace(name,target)
                finally:Path(name).unlink(missing_ok=True)
                cue.update(screenshot=str(target),screenshot_pts=first['seconds'],
                           screenshot_frame=first['frame'],screenshot_stage=stage['kind'],
                           screenshot_kind='boundary' if first['label']=='start_visible' else 'sample_only')
        report['coverage']['saved_evidence_frames']=len(saved_frames)
        report['coverage']['saved_evidence_bytes']=evidence_bytes
        report['stage']['temporary_file_retained'] = False
        save_report(report, output)
    # No temporary render survives API work, failures, or cancellation.
    if mode == 'text':
        _cancelled(cancel)
        semantic_runner(report, output, cancel=cancel, progress=progress)
    else:
        report['api']['text'].update(status='disabled', reason='未执行 API 语义检查')
        report['warnings'].append(LOCAL_NOTICE)
    _review_coverage(report)
    save_report(report, output)
    progress(1, '质检完成，报告已保存，请结合质检截图复核可疑项')
    return report


def _all_report_cues(report):
    if report.get('strategy') == 'sentence': return report.get('cues',[])+report.get('unmatched_cues',[])
    return report.get('cues',[])+report.get('source_cues',[])+report.get('unobserved_cues',[])


def _review_coverage(report):
    cues=_all_report_cues(report)
    states=[c.get('review_status','needs_review') for c in cues]
    coverage=report.setdefault('coverage',{})
    coverage.update(manual_reviewed=states.count('reviewed'),manual_skipped=states.count('skipped'),
                    manual_pending=states.count('needs_review'),complete_video_verified=False)
    coverage['review_meaning']='人工复核仅记录查看状态，不会修改原字幕或时间轴'


def record_audit_review(report,output_dir,cue_ids,status,note=''):
    """Persist an explicit human disposition, never alter OCR/text/timeline.

    reviewed means the user inspected this item, NOT correctness/pass. Skipped
    items remain visible and unverified. No provider can call this implicitly.
    """
    from .audit import save_report
    if status not in ('needs_review','reviewed','skipped'):
        raise ValueError('人工复核状态无效')
    ids=list(cue_ids) if isinstance(cue_ids,(list,tuple,set)) else []
    known={c['id'] for c in _all_report_cues(report)}
    if not ids or not all(isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or not set(ids)<=known:
        raise ValueError('所选复核条目不存在或重复')
    if not isinstance(note,str) or len(note)>4000:raise ValueError('复核备注不能超过 4000 字')
    updated=deepcopy(report)
    event={'id':'review-'+uuid.uuid4().hex,'cue_ids':ids,'status':status,'note':note,
           'actor':'explicit_user_action','created_at':datetime.now(timezone.utc).isoformat(),
           'counts_as_pass':False}
    updated.setdefault('review_history',[]).append(event)
    for cue in _all_report_cues(updated):
        if cue['id'] in ids:
            cue.update(review_status=status,review_note=note,review_event=event['id'],counts_as_pass=False)
    for issue in updated.get('issues',[]):
        related=[c for c in _all_report_cues(updated) if c['id'] in issue.get('cue_ids',[])]
        if related:
            states={c.get('review_status','needs_review') for c in related}
            issue['review_status']=next(iter(states)) if len(states)==1 else 'needs_review'
    _review_coverage(updated)
    save_report(updated,output_dir)
    report.clear();report.update(updated)
    return report


def rescan_audit(parent_report,parent_output_dir,*,first_frame,last_frame,cue_ids=(),
                 chinese_roi=None,source_roi=None,source_language=None,chinese_language=None,settings=None,
                 cancel=lambda:False,progress=lambda p,m:None,cache_dir=None):
    """Append a scoped local recheck; never replace the parent's observations.

    No API call is made. A new child report carries new candidates and evidence;
    parent ``rescans`` records success/failure/cancellation and exact requested
    global frame range. Old OCR text, frames, semantic findings and reviews stay.
    """
    from .audit import save_report
    _cancelled(cancel)
    if any(type(n) is not int for n in (first_frame,last_frame)) or not 1<=first_frame<=last_frame:
        raise ValueError('局部重查的起止帧范围无效')
    verify_input_versions(parent_report,cancel)
    inputs=parent_report.get('inputs',{})
    video_path=_file(inputs.get('video'),'原质检视频')
    video=Video(video_path);clock=AuditClock.build(video,cancel)
    if any(type(n) is not int for n in (first_frame,last_frame)) or not 1<=first_frame<=last_frame<=len(clock.stamps):
        raise ValueError('局部重查的起止帧超出视频总帧数范围')
    ids=list(cue_ids) if isinstance(cue_ids,(tuple,list,set)) else None
    known={c['id']:c for c in _all_report_cues(parent_report)}
    if ids is None or not all(isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or not set(ids)<=set(known):
        raise ValueError('所选重查字幕条目无效')
    selected=[known[i] for i in ids]
    left=clock.stamps[first_frame-1];right=clock.stamps[last_frame-1]
    context=[c for c in parent_report.get('cues',[]) if c.get('start') is not None and c['start']<=right and (c.get('end') or c['start'])>=left]
    selected.extend(c for c in context if c['id'] not in {item['id'] for item in selected})
    expected=set(i for c in selected for i in c.get('expected_ids',[]))
    expected.update(c['translation_id'] for c in selected if c.get('translation_id'))
    options={**parent_report.get('ocr_settings',{}),**dict(settings or {})}
    if parent_report.get('baseline_policy',{}).get('use_current_project'):
        options['audit_use_current_project']=True
    if source_language is not None:options['source_language']=_language(source_language,'原文')
    if chinese_language is not None:options['chinese_language']=_language(chinese_language,'中文')
    regions=parent_report.get('regions',{})
    zh=chinese_roi if chinese_roi is not None else regions.get('chinese')
    source=source_roi if source_roi is not None else regions.get('source')
    # Reconfirm changed rectangles, not auto-accept a candidate. Existing full
    # frame local mode may remain None, while explicit regions retain validation.
    if zh and zh.get('origin')=='full_frame':zh=None
    validate_regions(zh,source,video.info['width'],video.info['height'],mode='local')
    directory=Path(parent_output_dir)
    for name in ('report.json','report.html'):protect_output(directory/name,inputs.values())
    identity='rescan-'+uuid.uuid4().hex
    child_dir=directory/identity
    entry={'id':identity,'status':'running','selected_cue_ids':ids,
           'context_cue_ids':[c['id'] for c in context],
           'range':{'first_frame':first_frame,'last_frame':last_frame,
                    'start':clock.evidence(first_frame),'last':clock.evidence(last_frame)},
           'regions':{'chinese':zh,'source':source},'source_language':options.get('source_language','jpn+eng'),
           'chinese_language':options.get('chinese_language','chi_sim'),'input_versions':deepcopy(parent_report['input_versions']),
           'report_path':str(child_dir/'report.json'),'created_at':datetime.now(timezone.utc).isoformat(),
           'result_status':'candidates_not_adopted','counts_as_pass':False}
    updated=deepcopy(parent_report)
    updated.setdefault('rescans',[]).append(entry)
    _review_coverage(updated);save_report(updated,directory)
    parent_report.clear();parent_report.update(updated)
    entry=parent_report['rescans'][-1]
    kind='raw' if parent_report.get('stage',{}).get('kind') in ('temporary_render_precheck','ass_parse_only') else 'hardsubbed'
    try:
        child=run_audit(video_path,inputs.get('translation'),child_dir,mode='local',
            chinese_roi=zh,source_roi=source,ass_path=inputs.get('ass'),input_kind=kind,
            settings=options,project_path=inputs.get('project'),ktv_path=inputs.get('ktv'),
            cancel=cancel,progress=progress,semantic_runner=None,
            frame_range=[first_frame,last_frame],expected_ids=sorted(expected),cache_dir=cache_dir)
        child['parent_audit']={'report_path':str(directory/'report.json'),'rescan_id':identity,
                               'selected_cue_ids':ids,'parent_observations_unchanged':True}
        child['scope_detail']['expected_ids']=sorted(expected)
        child['scope_detail']['result_status']='candidates_not_adopted'
        _review_coverage(child);save_report(child,child_dir)
        entry.update(status='needs_review',stage=deepcopy(child.get('stage',{})),
                     scope=deepcopy(child.get('scope_detail',{})),coverage=deepcopy(child.get('coverage',{})))
        if parent_report.get('strategy')=='sentence':
            candidates={c['id']:c for c in child.get('cues',[])}
            for original in parent_report.get('cues',[]):
                if original['id'] in ids and original['id'] in candidates:
                    original['latest_recheck']={'rescan_id':identity,'report_path':str(child_dir/'report.json'),
                        'observation':deepcopy(candidates[original['id']]),'counts_as_pass':False,
                        'meaning':'已更新局部重查结果（不影响原字幕文件）'}
        return {'parent_report':parent_report,'report':child,'rescan':entry}
    except InterruptedError:
        entry.update(status='cancelled',reason='已取消，原报告内容不受影响')
        raise
    except Exception as exc:
        entry.update(status='failed',reason=type(exc).__name__+'，局部重查未完成')
        raise
    finally:
        entry['finished_at']=datetime.now(timezone.utc).isoformat()
        _review_coverage(parent_report);save_report(parent_report,directory)
