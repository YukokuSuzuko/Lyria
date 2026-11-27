"""Offline NextFire adapter; never downloads or changes an editor document."""
from contextlib import redirect_stdout
import sys
import time
import wave
import tempfile
from pathlib import Path


def chunk_frame_ranges(total_samples, strides, kernels, *, chunk_seconds=30, context_seconds=3):
    stride, receptive = 1, 1
    for step, kernel in zip(strides, kernels):
        receptive += (kernel - 1) * stride
        stride *= step
    total_frames = max(0, (total_samples - receptive) // stride + 1)
    chunk_frames = max(1, round(chunk_seconds * 16000 / stride))
    context = round(context_seconds * 16000 / stride)
    for first in range(0, total_frames, chunk_frames):
        last = min(total_frames, first + chunk_frames)
        window_first = max(0, first - context)
        window_last = min(total_frames, last + context)
        yield (window_first * stride, (window_last - 1) * stride + receptive,
               first - window_first, last - window_first, first, last)


def align_nextfire(request, emit=lambda p,m:None):
    import numpy as np
    import torch
    from transformers import AutoProcessor, AutoModelForCTC
    from local_models import ModelManager, NEXTFIRE
    from experimental_nextfire_alignment import align_reading_rows, normalize_reading
    from local_alignment_worker import runtime_info
    info = runtime_info(request['device'], request.get('language', 'Japanese'))
    if request.get('language') != 'Japanese':
        raise ValueError('NextFire 仅支持日语演唱原词。')
    rows = request.get('rows')
    if not isinstance(rows, list) or not rows or len(rows)>1000:
        raise ValueError('请选择有效原词行。')
    from japanese_readings import pure_symbol_row
    source_rows=rows
    rows=[row for row in rows if not pure_symbol_row(row)]
    if not rows:raise ValueError('全部为纯符号免对时句，无需运行声学模型。')
    for row in rows:
        normalize_reading(row.get('reading'))
    manager=ModelManager(request['cache_root'])
    manager.ensure_verified(NEXTFIRE.id)
    model_path=manager.path(NEXTFIRE.id)
    started=time.perf_counter()
    emit(10,'正在加载 NextFire 日语对时模型')
    with redirect_stdout(sys.stderr):
        processor=AutoProcessor.from_pretrained(str(model_path),local_files_only=True,trust_remote_code=False)
        model=AutoModelForCTC.from_pretrained(str(model_path),local_files_only=True,trust_remote_code=False,
                use_safetensors=True,dtype=torch.float32,attn_implementation='sdpa').to(request['device']).eval()
    if str(next(model.parameters()).device)!=request['device']:
        raise RuntimeError('模型未加载到所选设备。')
    loaded=time.perf_counter()
    with wave.open(request['audio_wav'],'rb') as stream:
        if (stream.getnchannels(),stream.getsampwidth(),stream.getframerate())!=(1,2,16000):
            raise ValueError('NextFire 输入必须为 16 kHz 单声道 PCM16。')
        count=stream.getnframes()
        if not 0<count:
            raise ValueError('NextFire 音频不能为空。')
    chunks=list(chunk_frame_ranges(count,model.config.conv_stride,model.config.conv_kernel))
    if not chunks: raise ValueError('音频太短，无法运行对齐。')
    with tempfile.TemporaryDirectory(prefix='lyria-nextfire-emissions-') as folder:
        emissions = None
        try:
            with wave.open(request['audio_wav'], 'rb') as stream, torch.inference_mode(),redirect_stdout(sys.stderr):
                for index,(start,end,left,right,first,last) in enumerate(chunks):
                    emit(20+round(55*index/len(chunks)),f'正在分析整曲音频 {index+1}/{len(chunks)}')
                    stream.setpos(start)
                    audio=np.frombuffer(stream.readframes(end-start),dtype='<i2').astype(np.float32)/32768
                    inputs=processor(audio=audio,sampling_rate=16000,return_tensors='pt').to(request['device'])
                    output=model(**inputs).logits[0]
                    if len(output)<right: raise RuntimeError('NextFire 分块帧数不一致。')
                    values=output[left:right].float().log_softmax(-1).cpu().numpy()
                    if emissions is None:
                        emissions=np.memmap(Path(folder)/'emissions.f32',dtype=np.float32,mode='w+',
                                            shape=(chunks[-1][-1],values.shape[1]))
                    emissions[first:last]=values
                    del inputs, output, values
            inferred=time.perf_counter()
            emit(80,'正在按完整原词顺序匹配演唱时间（有界内存回溯）')
            result=align_reading_rows(emissions,rows,processor.tokenizer.get_vocab(),
                blank_id=model.config.pad_token_id,duration=count/16000)
        finally:
            if emissions is not None:
                emissions._mmap.close()
                del emissions
    from nextfire_tail_guard import inspect_tail_spans
    inspect_tail_spans(result,request['audio_wav'],enabled=bool(request.get('tail_silence_guard',False)))
    for row in result:
        row['status']='forced_path_requires_review'
        row['token_spans']=[[t['token_id'],t['start_frame'],t['end_frame'],round(t['score'],7)] for t in row['token_spans']]
        row['token_span_format']='id,start_frame,end_frame,score-v1'
    by_id={row['id']:row for row in result}
    result=[by_id[row['id']] if row['id'] in by_id else dict(id=row['id'],text=row['text'],reading=row.get('reading',''),status='nonvocal_symbols',start_time=None,end_time=None,token_spans=[]) for row in source_rows]
    return dict(version=1,model_id=NEXTFIRE.id,revision=NEXTFIRE.revision,duration=count/16000,
        rows=result,device=request['device'],runtime=info,timings_seconds=dict(load=loaded-started,inference=inferred-loaded,
        ctc=time.perf_counter()-inferred),warning='强制对齐会尝试匹配提供的全部原词；有时间不代表确实唱出，须试听核对。',
        chunking=dict(seconds=30,context_seconds=3,chunks=len(chunks),
                      traceback='exact-global-checkpoint-v1',max_backtrack_cells=4_000_000,
                      covered_until_seconds=count/16000))
