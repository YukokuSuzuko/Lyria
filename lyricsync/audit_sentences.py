"""Sentence-first, bounded local OCR. Sampling is never frame-boundary verification."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
import time

from .model import normalized, valid_time, stable_id
from .vision import Video, make_ocr, role_ocr_settings
from .storage import protect_output

NOTICE = '快速逐句抽检：每句抽取最多 3 帧画面核对文字，不逐帧检查进出点。'


def text_differences(expected, actual):
    """Preserve punctuation, spacing and line breaks in a concrete edit list."""
    if not actual:
        return ['画面未识别出清晰文字，暂无法比对']
    edits = []
    for op, a, b, c, d in SequenceMatcher(None, expected, actual, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        if op == 'delete': edits.append('疑似缺字：' + repr(expected[a:b]))
        elif op == 'insert': edits.append('疑似多字：' + repr(actual[c:d]))
        else: edits.append('疑似替换：' + repr(expected[a:b]) + ' → ' + repr(actual[c:d]))
    return edits or ['抽样画面文字一致']


def assess_samples(cue):
    samples = cue.get('samples', [])
    valid = [s for s in samples if s.get('text') and s.get('confidence', 0) >= .8 and s.get('path')]
    counts = Counter(s['text'] for s in valid)
    chosen = max(counts, key=lambda text: (counts[text], max(s['confidence'] for s in valid if s['text'] == text)), default='')
    representative = next((s for s in valid if s['text'] == chosen), next(iter(samples), {}))
    cue.update(text=chosen or representative.get('text', ''), confidence=representative.get('confidence', 0),
               ocr_text_variants=list(dict.fromkeys(s.get('text', '') for s in samples)),
               representative_frame=representative.get('frame'), representative_seconds=representative.get('seconds'),
               counts_as_pass=False)
    if representative.get('path'):
        cue.update(screenshot=representative['path'], screenshot_frame=representative['frame'],
                   screenshot_pts=representative['seconds'], screenshot_kind='sample_only')
    expected = cue.get('expected_text', '')
    strong = bool(expected.strip() and chosen and counts[chosen] >= 2)
    cue['content_status'] = ('sample_text_matches' if chosen == expected else 'text_difference') if strong else 'unable_to_judge'
    cue['differences'] = text_differences(expected, cue['text']) if strong else ['识别信息不足（不足两帧清晰一致的识别结果），请结合质检截图核对']
    if len(counts) > 1:
        cue['content_status'] = 'unable_to_judge'
        cue['differences'].append('不同抽样帧识别不一致，请查看各帧文字')
    cue['checked'] = bool(samples) and len(samples) >= len(cue.get('planned_frames',samples))
    if not cue['checked']: cue['overall_status']='unable_to_judge'
    return cue


def sentence_observation(observation, expected):
    """Associate whole detected lines; never manufacture/correct OCR characters.

    Simultaneously displayed subtitles can share an ROI. Keep raw detections,
    select a contiguous actual-line group, and expose the association rule.
    """
    from .model import similarity
    result=deepcopy(observation)
    detections=observation.get('detections', [])
    candidates=[]
    for start in range(len(detections)):
        for length in range(1,min(4,len(detections)-start)+1):
            group=detections[start:start+length]
            text='\n'.join(d['text'] for d in group)
            candidates.append((similarity(text, expected), text, min(d['confidence'] for d in group)))
    if candidates:
        score,text,confidence=max(candidates,key=lambda c:c[0])
        if score>=.65:
            result.update(text=text,confidence=confidence,association_similarity=score,
                selection_rule='从完整识别行匹配本句；原始区域文字保留，未修改字符')
    return result


def _targets(cue, clock):
    a, b = cue.get('reference_start'), cue.get('reference_end')
    if not valid_time(a, b): return []
    endpoint=clock.end if clock.end is not None else clock.stamps[-1]
    if a>=endpoint or b>endpoint+1e-6: return []
    return sorted(set(max(1, min(len(clock.stamps), clock.activation(a + (b-a)*fraction))) for fraction in (.2, .5, .8)))


def _coverage(report, started):
    from .audit_service import _review_coverage
    rows = report['cues']
    report['coverage'].update(expected_total=len(rows), sentences_checked=sum(c.get('checked', False) for c in rows),
        sentences_unchecked=sum(not c.get('checked', False) for c in rows),
        sentences_partial=sum(bool(c.get('samples')) and not c.get('checked') for c in rows),
        unable_to_judge=sum(c.get('overall_status',c.get('content_status')) in ('unable_to_judge', 'not_checked') for c in rows),
        sample_text_matches=sum(c.get('overall_status',c.get('content_status')) == 'sample_text_matches' for c in rows),
        text_differences=sum(c.get('overall_status',c.get('content_status')) == 'text_difference' for c in rows),
        unmatched_samples=len(report.get('unmatched_cues', [])), complete_video_verified=False,
        frame_boundaries_verified=False, discovery=NOTICE,
        chinese_candidates=sum(bool(c.get('text')) for c in rows), source_candidates=len(report.get('source_cues',[])),
        expected_matched=sum(bool(c.get('text')) for c in rows), expected_unobserved=sum(not c.get('text') for c in rows))
    report['scope_detail']['unchecked_sentence_ids'] = [c['id'] for c in rows if not c.get('checked')]
    report['scope_detail']['unchecked_sentence_numbers'] = [c['number'] for c in rows if not c.get('checked')]
    report['api']['text']['unchecked_ids'] = [c['id'] for c in rows if c['id'] not in report['api']['text'].get('checked_ids', [])]
    report.setdefault('performance', {})['total_seconds'] = time.perf_counter() - started
    _review_coverage(report)


def run_sentence_audit(video_path, translation_path, output_dir, *, mode='local', chinese_roi=None,
        source_roi=None, ass_path=None, input_kind='hardsubbed', settings=None, project_path=None,
        ktv_path=None, cancel=lambda:False, progress=lambda p,m:None, semantic_runner=None,
        frame_range=None, expected_ids=None, cache_dir=None):
    from .audit import local_audit, save_report, _audit_project
    from .audit_service import (AuditClock, rendered_input, validate_regions, fingerprint_inputs,
                               _recognized, _cancelled, _file, _language)
    import av
    import cv2
    started = time.perf_counter()
    options = dict(settings or {})
    options['audit_strategy'] = 'sentence'
    output = Path(output_dir)
    video_path = _file(video_path, '视频')
    translation_path = _file(translation_path or ass_path, '译文文件')
    if mode == 'text' and semantic_runner is None: raise ValueError('AI 语义核对服务尚未配置，请先检查 API 设置')
    inputs = {'video':str(video_path), 'translation':str(translation_path), 'project':str(project_path) if project_path else None,
              'ass':str(ass_path) if ass_path else None, 'ktv':str(ktv_path) if ktv_path else None}
    for name in ('report.json', 'report.html'): protect_output(output/name, inputs.values())
    original = Video(video_path)
    zh, src = validate_regions(chinese_roi, source_roi, original.info['width'], original.info['height'], mode=mode)
    options['chinese_language'] = _language(options.get('chinese_language', 'chi_sim'), '中文')
    options['source_language'] = _language(options.get('source_language', 'jpn+eng'), '原文')
    for role in ('chinese', 'source'):
        options[role + '_engine'] = role_ocr_settings(options, role, options[role + '_language'])['engine']
    output.mkdir(parents=True, exist_ok=True)
    # Save the ordered reference before decoding/models, so cancellation leaves
    # an explicit unchecked list, even if not one frame could be processed.
    report = local_audit(video_path, translation_path, output, project_path=project_path, ktv_path=ktv_path,
                        ass_path=ass_path, settings=options, _observed=[], _expected_ids=expected_ids)
    project, _ = _audit_project(project_path)
    mappings = {('audit-'+m.id):m for m in project.mappings} if project else {}
    lyrics = {l.id:l.text for l in project.lyrics} if project else {}
    if project and report.get('expected_basis')=='project_current_chinese':
        original_numbers={'audit-'+m.id:n for n,m in enumerate(project.mappings,1)}
    else:
        from .subtitles import import_translation
        original_numbers={t.id:n for n,t in enumerate(import_translation(translation_path,options.get('txt_groups',False)),1)}
    rows = []
    for local_number, expected in enumerate(report['expected_cues'], 1):
        number=original_numbers.get(expected['id'],local_number)
        mapping = mappings.get(expected['id'])
        source = '\n'.join(lyrics[i] for i in mapping.lyric_ids if i in lyrics) if mapping else ''
        rows.append(dict(id=stable_id('sentence', expected['id']), number=number, track='sentence',
            expected_ids=[expected['id']], expected_text=expected['text'], expected_texts=[expected['text']],
            reference_lyrics=[source] if source else [], source_text=source, source_ocr='', text='',
            reference_start=expected.get('start'), reference_end=expected.get('end'),
            start=expected.get('start'), end=expected.get('end'), first_frame=None, last_frame=None,
            confidence=0, boundary_status='sampled_not_frame_verified', content_status='not_checked', overall_status='not_checked',
            review_status='needs_review', counts_as_pass=False, checked=False, samples=[], source_samples=[],
            evidence_frames=[], raw_observations=[], warnings=[], differences=['尚未检查']))
    report.update(cues=rows, source_cues=[], unobserved_cues=[], unmatched_cues=[], issues=[],
        strategy='sentence', local_status='running', regions={'chinese':zh, 'source':src},
        ocr_settings={k:options[k] for k in ('engine','chinese_engine','source_engine','ocr_device','ocr_threads','ocr_model','ocr_preprocess','chinese_language','source_language','audit_strategy','audit_use_current_project') if k in options},
        scope=NOTICE, scope_detail={'kind':'sentence_samples', 'complete_video_verified':False,
           'frame_range':frame_range, 'sampled_frames':[], 'unsampled_frames_checked':False},
        input_versions={}, warnings=[NOTICE])
    reference_video=(project.inputs.get('video') if project else None)
    report['reference_media']={'selected_video':str(video_path),'project_video':reference_video,
        'same_file_path':bool(reference_video and Path(reference_video).resolve()==video_path),
        'edit_timeline_verified':False}
    if reference_video and Path(reference_video).resolve()!=video_path:
        report['warnings'].append('所选成片与工程原视频并非同一文件，本次按工程时间点抽样，请留意成片是否有剪辑偏移')
    def checkpoint(final=False):
        from .storage import atomic_json
        _coverage(report, started)
        if final:
            write_started=time.perf_counter();save_report(report, output)
            report['performance'].update(report_write_seconds=time.perf_counter()-write_started,total_seconds=time.perf_counter()-started)
            atomic_json(output/'report.json',report,inputs.values())
        else: atomic_json(output/'report.json', report, inputs.values())
    checkpoint()
    try:
        progress(.01, '正在校验视频文件与视频帧轴…')
        versions = fingerprint_inputs(inputs, cancel)
        report['input_versions'] = versions
        with rendered_input(video_path, ass_path, input_kind=input_kind, cancel=cancel,
                            timeout=options.get('render_timeout', 1800), max_bytes=options.get('render_max_bytes', 8*1024**3)) as (scan_path, stage):
            report['stage'] = stage
            video = Video(scan_path)
            clock = AuditClock.build(video, cancel)
            report['video'] = video.info
            if frame_range is not None and (not isinstance(frame_range, (list, tuple)) or len(frame_range) != 2
                    or any(type(n) is not int for n in frame_range) or not 1 <= frame_range[0] <= frame_range[1] <= len(clock.stamps)):
                raise ValueError('局部检查的起止帧范围无效')
            target_rows = {}
            for cue in rows:
                chosen = _targets(cue, clock)
                if frame_range:
                    first, last = frame_range
                    lo = max(first, clock.activation(cue['reference_start'])) if cue.get('reference_start') is not None else first
                    hi = min(last, clock.activation(cue['reference_end'])-1) if cue.get('reference_end') is not None else last
                    chosen = sorted(set(round(lo+(hi-lo)*f) for f in (.2,.5,.8))) if lo <= hi else []
                cue['planned_frames'] = chosen
                if not chosen:
                    cue['differences'] = ['本句缺少有效时间或不在本次抽检范围内']
                    cue['content_status'] = 'unable_to_judge'
                    cue['overall_status'] = 'unable_to_judge'
                for n in chosen: target_rows.setdefault(n, []).append(cue)
            # One sample per uncovered temporal gap, capped at 24. It can reveal
            # unexpected subtitles, but never claims exhaustive missing-line detection.
            spans = sorted((c['reference_start'], c['reference_end']) for c in rows if valid_time(c['reference_start'], c['reference_end']))
            gaps = []; cursor = 0
            for a, b in spans:
                if a-cursor >= .5: gaps.append((cursor+a)/2)
                cursor = max(cursor, b)
            if video.info['duration']-cursor >= .5: gaps.append((cursor+video.info['duration'])/2)
            if len(gaps) > 24: gaps = [gaps[round(i*(len(gaps)-1)/23)] for i in range(24)]
            for stamp in gaps:
                n = min(len(clock.stamps), clock.activation(stamp))
                if not frame_range or frame_range[0] <= n <= frame_range[1]: target_rows.setdefault(n, [])
            targets = sorted(target_rows)
            report['scope_detail'].update(planned_frames=targets, planned_gap_samples=len(gaps),
                gap_sampling_limit=24, gap_sampling_exhaustive=False)
            checkpoint()
            progress(.08, '正在启动本地 OCR（快速逐句抽检）…')
            providers = {}
            roles = [('chinese', zh, options['chinese_language'])] + ([('source', src, options['source_language'])] if src else [])
            for role, region, language in roles:
                _cancelled(cancel)
                providers[role] = make_ocr(role_ocr_settings(options, role, language))
            frames_dir = output/'frames'; frames_dir.mkdir(exist_ok=True)
            evidence_bytes = 0; limit = options.get('evidence_max_bytes', 512*1024**2)
            if type(limit) is not int or limit <= 0: raise ValueError('质检截图缓存上限设置无效')
            done = 0; sample_started = time.perf_counter(); report['suppressed_noise'] = 0
            # Decode each needed frame once. Do not convert every decoded frame
            # to pixels, seek once per sentence, or accumulate video frames in RAM.
            target_set = set(targets)
            with av.open(str(scan_path)) as container:
                stream = container.streams.video[0]; stream.thread_type = 'AUTO'
                for index, frame in enumerate(container.decode(stream), 1):
                    _cancelled(cancel)
                    if not targets or index > targets[-1]: break
                    if index not in target_set: continue
                    image = frame.to_ndarray(format='bgr24')
                    evidence = clock.evidence(index)
                    path = frames_dir/f'frame-{index:09d}.png'
                    ok, encoded = cv2.imencode('.png', image)
                    saved = bool(ok and evidence_bytes+len(encoded) <= limit)
                    if saved:
                        protect_output(path, inputs.values()).write_bytes(encoded.tobytes()); evidence_bytes += len(encoded)
                    observations = {}
                    for role, region, language in roles:
                        _cancelled(cancel)
                        detections = _recognized(providers[role], image, region['box'])
                        kept = [d for d in detections if len(normalized(d['text'])) >= 2]
                        report['suppressed_noise'] += len(detections)-len(kept)
                        kept.sort(key=lambda d:(d['box'][1], d['box'][0]))
                        observations[role] = {**evidence, 'text':'\n'.join(d['text'] for d in kept),
                            'confidence':min((d['confidence'] for d in kept), default=0),
                            'detections':kept, 'path':str(path) if saved else None, 'label':'sample_only'}
                    for cue in target_rows[index]:
                        cue['samples'].append(sentence_observation(observations['chinese'], cue['expected_text']))
                        if src:
                            cue['source_samples'].append(sentence_observation(observations['source'], cue['source_text']))
                            cue['source_ocr'] = observations['source']['text']
                        if saved: cue['evidence_frames'].append({**evidence, 'path':str(path), 'label':'sample_only'})
                        assess_samples(cue)
                        if not saved: cue['warnings'] = ['质检截图保存失败或已达空间上限，无法预览该帧']
                    if not target_rows[index]:
                        observation = observations['chinese']
                        if observation['text'] and observation['confidence'] >= .8:
                            unmatched = dict(id=stable_id('unmatched', index), track='unmatched', text=observation['text'],
                                start=evidence['seconds'], end=evidence['seconds'], confidence=observation['confidence'],
                                representative_frame=index, representative_seconds=evidence['seconds'],
                                first_frame=None, last_frame=None, boundary_status='sampled_not_frame_verified',
                                content_status='unable_to_judge', review_status='needs_review', counts_as_pass=False,
                                warnings=['未匹配到工程字幕（可能是工程漏句、画面其他文字或 OCR 误检），请结合质检截图核对'],
                                evidence_frames=[{**evidence, 'path':str(path), 'label':'sample_only'}] if saved else [],
                                screenshot=str(path) if saved else None)
                            report['unmatched_cues'].append(unmatched)
                    report['scope_detail']['sampled_frames'].append(index); done += 1
                    checkpoint()
                    elapsed = time.perf_counter()-started
                    eta = (time.perf_counter()-sample_started)/done*(len(targets)-done)
                    remaining = ('预计还需 '+str(round(eta))+' 秒') if done >= 3 else '正在估算剩余时间'
                    progress(.1+.8*done/max(1,len(targets)), f'抽样识别 {done}/{len(targets)} 帧 · 已用 {elapsed:.0f} 秒 · {remaining}')
            for cue in rows:
                if cue.get('source_samples'):
                    source_cue = dict(cue, id=cue['id']+'-source', track='source', expected_text=cue['source_text'],
                                      samples=cue['source_samples'], source_samples=[])
                    assess_samples(source_cue)
                    cue['source_ocr'] = source_cue['text']; cue['source_differences'] = source_cue['differences']
                    cue['source_content_status'] = source_cue['content_status']; report['source_cues'].append(source_cue)
                else:
                    cue['source_content_status']='unable_to_judge'
                    cue['source_differences']=['未框选原文区域，已跳过原文核对']
                states=(cue['content_status'],cue['source_content_status'])
                cue['overall_status']='text_difference' if 'text_difference' in states else 'sample_text_matches' if all(s=='sample_text_matches' for s in states) else 'unable_to_judge'
                cue['warnings'].append('抽检帧仅用于核对文字内容')
            report['issues']=[{'cue_ids':[c['id']],'kind':'content' if c['overall_status']=='text_difference' else 'uncertain',
                'confidence':c.get('confidence',0),'status':'needs_review','origin':'local',
                'reason':'；'.join(c.get('differences',[])+c.get('source_differences',[]))}
                for c in rows if c.get('checked') and c.get('overall_status')!='sample_text_matches']
            report['coverage']['saved_evidence_bytes'] = evidence_bytes
            report['local_status'] = 'completed'
            if fingerprint_inputs(inputs, cancel) != versions:
                report['local_status'] = 'invalid_inputs_changed'
                for cue in rows: cue.update(content_status='unable_to_judge',overall_status='unable_to_judge')
                raise ValueError('质检期间素材文件发生了变化，请重新运行质检')
        checkpoint()
        if mode == 'text':
            # Never send unchecked placeholders as observed OCR.
            semantic_runner(report, output, cancel=cancel,
                progress=lambda p,m:progress(.9+.09*p, '画面抽检已完成 · '+m))
        else: report['api']['text'].update(status='disabled', reason='未执行 API 语义检查')
    except InterruptedError:
        if report['local_status'] == 'running': report['local_status'] = 'cancelled'
        if mode == 'text' and report['api']['text'].get('status') in ('not_run', 'running'):
            report['api']['text'].update(status='cancelled', reason='已取消，未核对的句子仍保留在报告中')
        report['warnings'].append('质检已取消，已完成的抽检结果已保留')
        raise
    except Exception as exc:
        if report['local_status'] == 'running': report['local_status'] = 'failed'
        if mode == 'text' and report['local_status'] == 'completed':
            from .api_client import error_status, ERROR_HELP
            status = getattr(exc, 'code', None) if isinstance(getattr(exc, 'code', None), str) else error_status(exc)
            if report['api']['text'].get('status') in ('not_run','running'):
                report['api']['text']['status']='partial' if report['api']['text'].get('checked_ids') else 'failed'
            report['api']['text'].update(reason=ERROR_HELP.get(status, '接口请求失败，请检查网络和 API 设置。') + ' 未完成语义核对的句子已保留。')
        report['warnings'].append('未完成部分已标记为待检查，已生成的本地抽检结果不受影响')
        raise
    finally:
        checkpoint(final=True)
    progress(1, f'快速逐句抽检完成 · 耗时 {time.perf_counter() - started:.1f} 秒')
    return report