"""Offline Qwen ASR with bounded internal windows and explicit merge evidence."""
import time
import wave
from contextlib import redirect_stdout
import sys
from dev9_long_audio import disjoint_speech_windows, merge_transcript_chunks, silence_intervals


def transcribe(request, emit):
    import numpy as np
    import torch
    from transformers import AutoProcessor, AutoModelForMultimodalLM
    from local_models import ModelManager, ASR
    from local_alignment_worker import runtime_info
    info = runtime_info(request['device'], request['language'])
    manager = ModelManager(request['cache_root'])
    model_path = manager.ensure_verified(ASR.id)
    with wave.open(request['audio_wav'], 'rb') as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
            raise ValueError('转写音频需为 16kHz 单声道 16 位 WAV。')
        count = stream.getnframes()
        if count <= 0:
            raise ValueError('转写音频为空。')
    duration = count / 16000.
    warning = 'AI 听写仅供参考，不生成时间轴；请检查漏词、重复词和分段衔接。'
    started = time.monotonic()
    dtype = torch.float32 if request['device'] == 'cpu' else (
        torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
    emit(10, '正在加载离线听写模型…')
    with redirect_stdout(sys.stderr):
        processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
        model = AutoModelForMultimodalLM.from_pretrained(str(model_path), local_files_only=True,
            trust_remote_code=False, use_safetensors=True, dtype=dtype,
            attn_implementation='sdpa').to(request['device']).eval()
    if str(next(model.parameters()).device) != request['device']:
        raise RuntimeError('听写模型未加载到所选设备。')
    # Resource window, not the ForcedAligner's five-minute model limit.
    seconds = max(20., min(90., float(request.get('chunk_seconds', 90.))))
    quiet_regions=silence_intervals(request['audio_wav'])
    pending = list(disjoint_speech_windows(request['audio_wav'], seconds=seconds,quiet_regions=quiet_regions))
    chunks = []
    with wave.open(request['audio_wav'], 'rb') as stream:
        while pending:
            start, end = pending.pop(0)
            first_sample, last_sample = round(start * 16000), round(end * 16000)
            stream.setpos(first_sample)
            audio = np.frombuffer(stream.readframes(last_sample - first_sample), dtype='<i2').astype(np.float32) / 32768.
            emit(15 + round(80 * start / duration), f'正在听写 {start:.1f}–{end:.1f} 秒 / {duration:.1f} 秒')
            if not np.any(audio):
                text, complete = '', True
            else:
                with redirect_stdout(sys.stderr), torch.inference_mode():
                    inputs = processor.apply_transcription_request(audio=audio, language=request['language'],
                        sampling_rate=16000, return_tensors='pt').to(model.device, model.dtype)
                    generated = model.generate(**inputs, max_new_tokens=4096, do_sample=False)
                    tokens = generated[0, inputs['input_ids'].shape[1]:]
                    text = processor.decode(tokens, return_format='transcription_only')
                    eos = model.generation_config.eos_token_id
                    eos = set(eos if isinstance(eos, list) else [eos])
                    complete = bool(len(tokens) and int(tokens[-1]) in eos)
                    del inputs, generated, tokens
            # Retry truncated output internally. Never silently accept a cutoff.
            if not complete and end - start > 20.:
                middle = ((first_sample + last_sample) // 2) / 16000.
                pending[:0] = [(start, middle), (middle, end)]
                continue
            chunks.append(dict(start=start, end=end, text=text, complete=complete))
    text, _, unresolved = merge_transcript_chunks(chunks)
    if unresolved:
        warning += f' 有 {len(unresolved)} 处分段重叠无法可靠去重，已保留文字供核对。'
    complete = all(chunk['complete'] for chunk in chunks)
    if not complete:
        warning += ' 部分短段仍达到模型输出上限，初稿不完整。'
    if request['device'] == 'cuda:0':
        torch.cuda.synchronize()
        info['peak_cuda_allocated_bytes'] = torch.cuda.max_memory_allocated()
    del model, processor
    if request['device'] == 'cuda:0':
        torch.cuda.empty_cache()
    return dict(version=1, model_id=ASR.id, revision=ASR.revision, runtime=info, duration=duration,
        text=text, complete=complete, elapsed_seconds=time.monotonic() - started, warning=warning,
        pause_intervals=silence_intervals(request['audio_wav'],minimum_seconds=.3),
        chunks=chunks, chunking=dict(strategy='disjoint-continuous-silence-preferred-v2', seconds=seconds,
            silence=dict(kind='PCM-energy-not-semantic-VAD',minimum_seconds=.6,threshold_db=-50.,intervals=quiet_regions),
            overlap_seconds=0., chunks=len(chunks), unresolved_overlap_indices=unresolved,
            windows=[dict(start=c['start'],end=c['end'],complete=c['complete']) for c in chunks],
            covered_until_seconds=chunks[-1]['end'] if chunks else 0.))
