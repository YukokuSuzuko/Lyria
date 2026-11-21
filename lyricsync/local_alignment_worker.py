"""Isolated offline inference executable, launched only for an explicit task.

No project is passed here. stdout is a bounded JSON-lines progress protocol;
the result is an atomic JSON file. Model code comes from the pinned installed
Transformers distribution, never from a model repository.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from contextlib import redirect_stdout
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
import traceback
import wave

# -I ignores PYTHONPATH/user-site. This adds only our shipped trusted module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_models import ModelManager, QWEN, NEXTFIRE

# Capture the protocol channel before redirecting noisy library stdout. Progress
# must still reach the parent while a model/processor call is inside that scope.
_PROTOCOL_STDOUT = sys.stdout

def emit(percent, message):
    # ASCII on the pipe is UTF-8 compatible even under a Windows GBK locale.
    # JSON decoding reconstructs the original Chinese/Japanese text exactly.
    print(json.dumps({"progress": percent, "message": message}, ensure_ascii=True),
          file=_PROTOCOL_STDOUT, flush=True)


def process_resource_usage():
    """Measure inside the actual inference process, not a Windows venv launcher."""
    if os.name != 'nt':
        return {'host_peak_measurement': 'not_available_on_this_platform'}
    import ctypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]
    counter = Counters()
    counter.cb = ctypes.sizeof(counter)
    kernel, api = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    api.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counter), counter.cb):
        return {'host_peak_measurement': 'GetProcessMemoryInfo_failed'}
    return dict(host_peak_measurement='worker-self-Windows-PeakWorkingSetSize',
                worker_pid=os.getpid(), host_peak_working_set_bytes=counter.PeakWorkingSetSize,
                host_peak_commit_bytes=counter.PeakPagefileUsage)

def runtime_info(device, language="English"):
    from shared_runtime import shared_worker_verified
    if sys.prefix == sys.base_prefix and not shared_worker_verified():
        raise RuntimeError('请选择独立虚拟环境中的 Python，勿直接使用系统全局 Python。')
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('本地模型环境要求使用 Python 3.12。')
    for name, expected in (("transformers", "5.22.2"),):
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(f'运行环境缺少依赖包 {name}，请在本地模型窗口中重新安装运行环境。') from exc
        if installed != expected:
            raise RuntimeError(f'依赖包版本不匹配：需要 {name}=={expected}（当前为 {installed}），请重新创建运行环境。')
    try:
        import torch
        import numpy
        from transformers import AutoProcessor, AutoModelForTokenClassification
    except (ImportError, OSError) as exc:
        raise RuntimeError(f'加载模型依赖库失败：{exc}。请检查运行环境及 PyTorch 安装是否完整。') from exc
    if language == "Japanese":
        try:
            import nagisa
        except (ImportError, OSError) as exc:
            raise RuntimeError('日文打轴依赖包 nagisa 加载失败，请重新创建或修复运行环境。') from exc
    if language == "Korean":
        try:
            import soynlp
        except (ImportError, OSError) as exc:
            raise RuntimeError('韩文打轴依赖包 soynlp 加载失败，请重新创建或修复运行环境。') from exc
    info = {"python": sys.version.split()[0], "transformers": "5.22.2",
            "torch": torch.__version__, "device": device, "cuda_runtime": torch.version.cuda}
    # -I and pyvenv.cfg prevent common inheritance; also verify actual package
    # origins so a custom .pth cannot silently import these from another env.
    from importlib.util import find_spec
    package_root=(Path(sys.prefix)/"Lib"/"site-packages").resolve() if os.name=="nt" else (Path(sys.prefix)/"lib"/f"python{sys.version_info.major}.{sys.version_info.minor}"/"site-packages").resolve()
    info["package_origins"]={}
    for name in ("torch","transformers","numpy","librosa","onnxruntime"):
        spec=find_spec(name)
        origin=Path(spec.origin).resolve() if spec and spec.origin else None
        if origin is None or not origin.is_relative_to(package_root):
            raise RuntimeError(f'依赖包 {name} 并非来自所选的独立运行环境，请重新创建运行环境。')
        info["package_origins"][name]=str(origin)
    if device == "cuda:0":
        if not torch.cuda.is_available():
            raise RuntimeError('当前运行环境的 GPU 加速（CUDA）不可用，请安装 CUDA 版环境或改选 CPU。')
        try:
            value = torch.ones((32, 32), device=device)
            result = (value @ value).sum()
            torch.cuda.synchronize()
            if float(result.item()) != 32768:
                raise RuntimeError('GPU 加速自检结果异常，请检查显卡驱动或改用 CPU')
            info["gpu"] = torch.cuda.get_device_name(0)
            info["vram_bytes"] = torch.cuda.get_device_properties(0).total_memory
        except Exception as exc:
            raise RuntimeError(f'GPU 加速自检失败：{exc}。请检查显卡驱动与 CUDA 环境，或改选 CPU。') from exc
    elif device != "cpu":
        raise RuntimeError('计算设备仅支持选择 CPU 或 GPU（cuda:0）。')
    return info

def prepare_row_inputs(processor, audio, rows, language):
    """Respect user lyric boundaries before language-specific word splitting.

    Nagisa can merge across a newline (e.g. "も\nほら" -> "もほら"). Splitting
    each supplied row first prevents a token spanning two stable sentence IDs.
    The same public Qwen chat-template interface builds one audio/transcript
    prompt; no per-line audio slicing or guessed timestamp interpolation occurs.
    """
    words = []
    counts = []
    for row in rows:
        row_words = processor.split_words_for_alignment(row["text"], language)
        if not isinstance(row_words, list) or any(not isinstance(w, str) or not w for w in row_words):
            raise RuntimeError('模型切词结果异常，请重新检查歌词。')
        counts.append(len(row_words))
        words.extend(row_words)
    if not words:
        raise RuntimeError('所选歌词中没有可识别的有效文字（如仅含标点或空白）。')
    content = [{"type": "audio", "audio": audio}]
    content.extend({"type": "text", "text": word} for word in words)
    inputs = processor.apply_chat_template(
        [[{"role": "user", "content": content}]], tokenize=True,
        return_dict=True, sampling_rate=16000, return_tensors="pt")
    return inputs, [words], counts


def align(request):
    with wave.open(request['audio_wav'], 'rb') as stream:
        duration = stream.getnframes() / stream.getframerate()
    if duration > 300.:
        return align_long(request, duration)
    import numpy as np
    import torch
    from transformers import AutoProcessor, AutoModelForTokenClassification
    emit(4, '正在检查运行环境与计算设备…')
    info = runtime_info(request["device"], request["language"])
    manager = ModelManager(request["cache_root"])
    model_path = manager.ensure_verified(request["model_id"])
    timings = {}
    load_started = time.perf_counter()
    emit(12, '正在加载本地对齐模型…')
    dtype = torch.float32 if request["device"] == "cpu" else (
        torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
    try:
        with redirect_stdout(sys.stderr):
            processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
            model = AutoModelForTokenClassification.from_pretrained(
                str(model_path), local_files_only=True, trust_remote_code=False,
                use_safetensors=True, dtype=dtype, attn_implementation="sdpa").to(request["device"]).eval()
    except Exception:
        manager.invalidate(request["model_id"])
        raise
    if str(next(model.parameters()).device) != request["device"]:
        raise RuntimeError('模型未能加载到所选计算设备，任务已停止。')
    if request["device"] == "cuda:0":
        torch.cuda.synchronize()
    timings["model_load"] = time.perf_counter() - load_started
    with wave.open(request["audio_wav"], "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
            raise RuntimeError('待对齐的临时音频格式不符（需为 16kHz 单声道 16 位 WAV）。')
        count = stream.getnframes()
        if count <= 0 or count > 300 * 16000:
            raise RuntimeError('单批对齐音频时长需在 300 秒以内。')
        audio = np.frombuffer(stream.readframes(count), dtype="<i2").astype(np.float32) / 32768.0
    emit(30, '正在预处理演唱原文与音频…')
    with redirect_stdout(sys.stderr):
        preprocess_started = time.perf_counter()
        inputs, word_lists, row_word_counts = prepare_row_inputs(
            processor, audio, request["rows"], request["language"])
        inputs = inputs.to(model.device, model.dtype)
        if request["device"] == "cuda:0":
            torch.cuda.synchronize()
        timings["preprocess"] = time.perf_counter() - preprocess_started
        emit(45, '正在运行 AI 对齐模型…')
        inference_started = time.perf_counter()
        with torch.inference_mode():
            output = model(**inputs)
        if request["device"] == "cuda:0":
            torch.cuda.synchronize()
        timings["inference"] = time.perf_counter() - inference_started
        decode_started = time.perf_counter()
        decoded = processor.decode_forced_alignment(
            logits=output.logits, input_ids=inputs["input_ids"], word_lists=word_lists,
            timestamp_token_id=model.config.timestamp_token_id)[0]
        # Mark upstream timestamp repairs. Never hide interpolation as acoustic evidence.
        mask = inputs["input_ids"][0] == model.config.timestamp_token_id
        selected = output.logits[0][mask].float()
        predictions = selected.argmax(dim=-1).detach().cpu().tolist()
        probabilities = selected.softmax(dim=-1).max(dim=-1).values.detach().cpu().tolist()
        segment = float(processor.timestamp_segment_time) / 1000.0
        if len(predictions) != len(decoded) * 2:
            raise RuntimeError('模型返回的时间轴标记数量异常，请重试。')
        for index, item in enumerate(decoded):
            raw_start, raw_end = predictions[index * 2] * segment, predictions[index * 2 + 1] * segment
            item["raw_start_time"],item["raw_end_time"]=raw_start,raw_end
            item["repaired"] = abs(item["start_time"] - raw_start) > .002 or abs(item["end_time"] - raw_end) > .002
            item["score"] = min(probabilities[index * 2:index * 2 + 2])
    timings["decode"] = time.perf_counter() - decode_started
    emit(95, '正在整理逐句时间轴结果…')
    if request['device'] == 'cuda:0':
        info['peak_cuda_allocated_bytes'] = torch.cuda.max_memory_allocated()
    return {"version": 1, "runtime": info, "duration": count / 16000.0, "words": decoded,
            "model_id": request["model_id"], "revision": QWEN.revision,
            "tokenization": {"strategy": "per-row-v1", "row_word_counts": row_word_counts},
            "timings_seconds": timings}


def align_long(request, duration):
    """ASR anchors select bounded Qwen batches; timestamps only come from Qwen.

    No proportional lyric split: rows without a credible global textual anchor
    keep their original identity/text with absent timing. An incomplete or bad
    batch cannot shift later lyric occurrences onto another row.
    """
    import tempfile
    from dev9_long_audio import locate_lyric_rows, alignment_groups, context_row_indices, bounded_context_indices
    from local_models import ASR
    from local_transcription_worker import transcribe
    manager = ModelManager(request['cache_root'])
    try:
        manager.ensure_verified(ASR.id)
    except Exception as exc:
        raise RuntimeError('超过 5 分钟的 Qwen 整曲打轴需要本机 Qwen 听写模型来定位歌词片段；'
                           '请在模型管理中准备听写模型后重试。不会截取前 300 秒。') from exc
    started = time.perf_counter()
    emit(3, '正在为整曲自动定位歌词片段…')
    draft = transcribe(dict(request, operation='transcribe', chunk_seconds=30.),
                       lambda p, m: emit(3 + round(p * .35), m))
    anchors, unresolved = locate_lyric_rows(request['rows'], draft['chunks'])
    groups = list(alignment_groups(anchors, duration, limit=85., context=2.))
    by_row = {}
    reports = []
    raw_batches=[]
    resolved_instances=set()
    committed_cursor=0
    runtime = draft['runtime']
    def assembled():
        words, counts, row_reports = [], [], []
        for index, row in enumerate(request['rows']):
            selected = by_row.get(index)
            if selected is None:
                selected = [dict(text=row['text'], start_time=None, end_time=None, score=0.,
                                 repaired=False, source_row_id=row['id'],
                                 reason=anchors[index].get('reason','insufficient_asr_anchor'))]
            words.extend(selected)
            counts.append(len(selected))
            row_reports.append(dict(id=row['id'], localized=index in by_row,
                                    anchor_coverage=anchors[index]['coverage'],
                                    reason=anchors[index].get('reason','')))
        return dict(version=1, model_id=request['model_id'], revision=QWEN.revision,
            duration=duration, words=words, runtime=runtime,raw_batches=deepcopy(raw_batches),
            tokenization=dict(strategy='per-row-long-asr-anchors-v1', row_word_counts=counts),
            timings_seconds=dict(total=time.perf_counter() - started, coarse_asr=draft['elapsed_seconds']),
            chunking=dict(strategy='global-asr-anchor-bounded-qwen-v2', qwen_batch_limit_seconds=85,
                batches=reports, rows=row_reports, coarse_asr=draft['chunking'],
                unresolved_overlap_indices=unresolved, covered_until_seconds=draft['duration']),
            warning='长曲先由听写粗定位，再按原词身份分段精对齐；重复副歌、遗漏和边界须试听核对。')
    with tempfile.TemporaryDirectory(prefix='lyria-qwen-batches-') as folder:
        pending_groups=[(group,False) for group in groups]
        retried_instances=set()
        index=-1
        while pending_groups:
            group,is_retry=pending_groups.pop(0);index+=1
            total_batches=len(groups)+len(retried_instances)
            first, end = group[0]['start'], max(item['end'] for item in group)
            owned_indices = {item['index'] for item in group}
            indices = bounded_context_indices(request['rows'],group,anchors,margin_rows=2)
            cursor_before=committed_cursor
            selected = [request['rows'][i] for i in indices]
            audio_path = Path(folder) / f'batch-{index}.wav'
            with wave.open(request['audio_wav'], 'rb') as source, wave.open(str(audio_path), 'wb') as target:
                target.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                source.setpos(round(first * 16000))
                target.writeframes(source.readframes(round(end * 16000) - round(first * 16000)))
            emit(40 + round(50 * index / max(1, total_batches)),
                 f'正在精对齐整曲片段 {index + 1}/{total_batches}：{first:.1f}–{end:.1f} 秒')
            try:
                response = align(dict(request, audio_wav=str(audio_path), rows=selected))
            except InterruptedError:
                raise
            except (RuntimeError,ValueError) as exc:
                for owned in owned_indices:anchors[owned]['reason']='batch_failed_'+type(exc).__name__
                reports.append(dict(start=first,end=end,row_ids=[r['id'] for r in selected],
                    output_row_ids=[],status='failed',error_code=type(exc).__name__))
                emit(40+round(50*(index+1)/max(1,total_batches)),'本段失败，保留其未定位状态并继续独立后段；不推进歌词身份。')
                continue
            raw_batches.append(dict(start=first,end=end,request_rows=deepcopy(selected),response=deepcopy(response)))
            runtime = response['runtime']
            counts = response['tokenization']['row_word_counts']
            if len(counts) != len(selected) or sum(counts) != len(response['words']):
                raise RuntimeError('整曲分段结果的原词身份映射不完整，已停止合并。')
            cursor = 0
            for original_index, row, count in zip(indices, selected, counts):
                words = response['words'][cursor:cursor + count]
                cursor += count
                for word in words:
                    for key in ('start_time', 'end_time','raw_start_time','raw_end_time'):
                        if type(word.get(key)) in (int, float):
                            word[key] += first
                    word['source_row_id'] = row['id']
                if original_index in owned_indices:
                    boundary=bool(words) and ((first>0 and type(words[0].get('start_time')) in (int,float) and words[0]['start_time']<=first+.15) or (end<duration and type(words[-1].get('end_time')) in (int,float) and words[-1]['end_time']>=end-.15))
                    if boundary:
                        for word in words:word['boundary_pending']=True
                        anchor=anchors[original_index]
                        retry_start=max(0.,anchor['start']-5.);retry_end=min(duration,anchor['end']+5.)
                        if original_index not in retried_instances and retry_end-retry_start<=85. and (retry_start<first or retry_end>end):
                            retried_instances.add(original_index)
                            pending_groups.append(([dict(index=original_index,start=retry_start,end=retry_end)],True))
                    previous=by_row.get(original_index)
                    usable=bool(words) and not boundary and all(not w.get('repaired',False) for w in words)
                    if not is_retry or usable or previous is None:by_row[original_index] = words
                    reliable=bool(words) and not boundary and all(type(w.get('start_time')) in (int,float) and type(w.get('end_time')) in (int,float) and first<=w['start_time']<w['end_time']<=end-.05 and not w.get('repaired',False) for w in words)
                    reliable=reliable and all(w.get('score') is None or w['score']>=request.get('min_timestamp_score',.01) for w in words)
                    reliable=reliable and all(a['end_time']<=b['start_time']+.002 for a,b in zip(words,words[1:]))
                    if reliable:resolved_instances.add(original_index)
            while committed_cursor in resolved_instances:committed_cursor+=1
            reports.append(dict(start=first, end=end, row_ids=[r['id'] for r in selected],
                                cursor_before=cursor_before,cursor_after=committed_cursor,expected_rows_from_anchors=len(owned_indices),is_boundary_retry=is_retry,
                                pending_row_ids=[request['rows'][i]['id'] for i in sorted(owned_indices-resolved_instances)],
                                output_row_ids=[request['rows'][i]['id'] for i in sorted(owned_indices)],
                                context_only_row_ids=[request['rows'][i]['id'] for i in indices if i not in owned_indices],
                                timings_seconds=response['timings_seconds']))
            checkpoint=request.get('_partial_result_path')
            if checkpoint:
                target=Path(checkpoint);temporary=target.with_suffix('.tmp')
                temporary.write_text(json.dumps(assembled(),ensure_ascii=False,allow_nan=False),encoding='utf-8')
                os.replace(temporary,target)
    emit(95, '整曲片段已合并，未可靠定位的原词保留空时间供核对。')
    return assembled()


def main():
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
                      TOKENIZERS_PARALLELISM="false")
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    result_path = Path(args.result)
    request_path = Path(args.request)
    try:
        if request_path.stat().st_size > 2 * 1024 * 1024:
            raise RuntimeError('单次提交给本地模型的数据量过大，请缩小歌词范围。')
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if sys.prefix == sys.base_prefix:
            from shared_runtime import verify_worker_runtime
            verify_worker_runtime(request.get('_shared_runtime_attestation'))
        operation=request.get("operation","align")
        if operation=="readings":
            # Reading conversion has no acoustic model/device dependency. Dispatch
            # before probing Torch/CUDA so a missing device cannot mask pykakasi.
            from japanese_readings import prepare_reading_rows
            result={"version":1,"rows":prepare_reading_rows(request.get("rows"),tool=request.get("reading_tool","pykakasi"),reset=bool(request.get("reset_readings",False)))}
        elif operation=="probe":
            with redirect_stdout(sys.stderr):
                info = runtime_info(request["device"], request.get("language", "English"))
            from local_separation_worker import separator_info
            with redirect_stdout(sys.stderr):info.update(separator_info(request["device"]))
            result={"version":1,"runtime":info}
        elif operation=="separate":
            from local_separation_worker import separate
            with redirect_stdout(sys.stderr):result=separate(request,emit)
        elif operation=="transcribe":
            from local_transcription_worker import transcribe
            with redirect_stdout(sys.stderr):result=transcribe(request,emit)
        elif operation=="align":
            if request.get("model_id")==NEXTFIRE.id:
                from nextfire_worker import align_nextfire
                result=align_nextfire(request,emit)
            else:
                result=align(request)
        else:
            raise ValueError('未知的本地模型任务类型')
        result['resource_usage'] = process_resource_usage()
        temporary = result_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temporary, result_path)
        emit(100, "本地任务完成")
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True),
              file=_PROTOCOL_STDOUT, flush=True)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())

