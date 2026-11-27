"""Independent fixed MDX ONNX adapter; no SUG/UVR/audio-separator code copied.

Rebuilt from saved source excerpts and static bytecode after the recovery incident.
All DSP/mapping/device behavior requires a fresh validation receipt.
"""
from __future__ import annotations
import importlib.metadata
from pathlib import Path
import time
SAMPLE_RATE = 44100
N_FFT, HOP, BINS, FRAMES = 6144, 1024, 3072, 256
COMPENSATION = 1.022
ADAPTER_VERSION = "lyria-mdx-hq3-ola-v1"


def _matrix_probe_model():
    """Independent protobuf encoding of a 32x32 ONNX MatMul, no onnx dependency."""
    def varint(value):
        result = bytearray()
        while value > 127:
            result.append((value & 127) | 128)
            value >>= 7
        return bytes(result) + bytes([value])
    def integer(field, value):
        return varint(field << 3) + varint(value)
    def message(field, value):
        value = value.encode() if isinstance(value, str) else value
        return varint((field << 3) | 2) + varint(len(value)) + value
    shape = message(1, integer(1, 32)) * 2
    tensor_type = integer(1, 1) + message(2, shape)
    def value(name):
        return message(1, name) + message(2, message(1, tensor_type))
    node = message(1, "a") + message(1, "b") + message(2, "c") + message(4, "MatMul")
    graph = message(1, node) + message(2, "Lyria actual device probe") + message(11, value("a")) + message(11, value("b")) + message(12, value("c"))
    return integer(1, 8) + message(7, graph) + message(8, integer(2, 13))


def separator_info(device):
    import torch  # Load the selected official CUDA/cuDNN libraries first.
    import onnxruntime as ort
    import numpy as np
    if ort.__version__ != ("1.23.0" if device=="directml" else "1.23.2"):
        raise RuntimeError('人声分离需要 ONNX Runtime 1.23.2，请在本地模型窗口中重新创建运行环境。')
    if device not in ("cpu", "cuda:0", "directml"):
        raise ValueError('不支持的人声分离计算设备。')
    if device == "cuda:0":
        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError('当前环境缺少 ONNX Runtime GPU 加速支持（CUDAExecutionProvider），请重装 CUDA 环境或改选 CPU。')
    provider = {"cpu":"CPUExecutionProvider","cuda:0":"CUDAExecutionProvider","directml":"DmlExecutionProvider"}[device]
    options = ort.SessionOptions()
    if device == "directml": options.enable_mem_pattern=False
    if device != "cpu":
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    probe = ort.InferenceSession(_matrix_probe_model(), sess_options=options, providers=[provider])
    probe.disable_fallback()
    if probe.get_providers()[0] != provider:
        raise RuntimeError('未能启用所选的人声分离加速设备，请检查显卡驱动或改选 CPU。')
    result = probe.run(None, {"a":np.ones((32,32),np.float32), "b":np.ones((32,32),np.float32)})[0]
    if not np.array_equal(result, np.full((32,32),32,np.float32)):
        raise RuntimeError('ONNX Runtime 运行自检失败，请检查显卡驱动或重新创建运行环境。')
    return dict(onnxruntime=ort.__version__, separation_adapter=ADAPTER_VERSION,
                available_providers=ort.get_available_providers(), actual_matrix_provider=provider)


def infer_chunks(mix, session, *, progress=lambda p,m:None, compensation=COMPENSATION):
    """Exact sample identity mapping, including short final blocks."""
    import numpy as np
    import torch
    if mix.ndim != 2 or mix.shape[0] != 2 or not 0 < mix.shape[1]:
        raise ValueError('待分离音频需非空，且为 44.1kHz 双声道格式。')
    if not np.isfinite(mix).all():
        raise ValueError('输入音频包含损坏的采样数据，请检查音频文件。')
    chunk = HOP * (FRAMES-1)
    margin = N_FFT//2
    usable = chunk - 2*margin
    stride = usable//2
    n = mix.shape[1]
    source = np.pad(mix, ((0,0),(margin,chunk)))
    instrument = np.zeros((2,n),dtype=np.float64)
    weights = np.zeros(n,dtype=np.float64)
    window = torch.hann_window(N_FFT)
    blend = np.maximum(np.sin(np.pi*(np.arange(usable)+.5)/usable)**2,.001)
    positions = list(range(0,n,stride))
    name = session.get_inputs()[0].name
    with torch.inference_mode():
        for index,start in enumerate(positions):
            block = torch.from_numpy(source[:,start:start+chunk].copy())
            spectrum = torch.stft(block,n_fft=N_FFT,hop_length=HOP,window=window,center=True,return_complex=True)
            packed = torch.view_as_real(spectrum[:,:BINS,:]).permute(0,3,1,2).reshape(1,4,BINS,FRAMES)
            values = packed.numpy().copy()
            values[:,:,:3,:] = 0
            output = session.run(None,{name:values})[0]
            if output.shape != (1,4,BINS,FRAMES) or not np.isfinite(output).all():
                raise RuntimeError('人声分离模型输出异常，请重试或改用 CPU。')
            complex_pair = torch.from_numpy(output).reshape(2,2,BINS,FRAMES).permute(0,2,3,1).contiguous()
            predicted = torch.view_as_complex(complex_pair)
            padded = torch.nn.functional.pad(predicted,(0,0,0,N_FFT//2+1-BINS))
            wave = torch.istft(padded,n_fft=N_FFT,hop_length=HOP,window=window,center=True,length=chunk).numpy()
            count = min(usable,n-start)
            instrument[:,start:start+count] += wave[:,margin:margin+count] * blend[:count]
            weights[start:start+count] += blend[:count]
            progress(15+round(75*(index+1)/len(positions)),f'正在分离人声：{index + 1} / {len(positions)}')
    if not np.all(weights>0):
        raise RuntimeError('人声分离未覆盖完整片段，请重试。')
    instrumental = (instrument/weights*compensation).astype(np.float32)
    vocals = mix-instrumental
    if not np.isfinite(vocals).all() or float(np.abs(vocals).max())>16:
        raise RuntimeError('人声分离结果异常，已停止后续打轴，请重试或改用 CPU。')
    return vocals,instrumental


def infer_stream(incoming, outgoing, session, *, progress=lambda p,m:None, compensation=COMPENSATION):
    """Same global OLA grid as infer_chunks, with bounded audio accumulators.

    Finalize one stride only after all preceding overlapping windows contribute.
    No outer-block seam, sample insertion/deletion or per-song in-memory arrays.
    """
    import numpy as np
    import torch
    import soundfile as sf
    chunk = HOP * (FRAMES - 1)
    margin = N_FFT // 2
    usable = chunk - 2 * margin
    stride = usable // 2
    window = torch.hann_window(N_FFT)
    blend = np.maximum(np.sin(np.pi * (np.arange(usable) + .5) / usable) ** 2, .001)
    accum = np.zeros((2, usable), dtype=np.float64)
    weights = np.zeros(usable, dtype=np.float64)
    name = session.get_inputs()[0].name
    vocal_peak = instrument_peak = 0.
    with sf.SoundFile(incoming) as source, sf.SoundFile(outgoing, 'w', samplerate=SAMPLE_RATE,
            channels=2, subtype='FLOAT', format='WAV') as target, torch.inference_mode():
        if source.samplerate != SAMPLE_RATE or source.channels != 2 or len(source) <= 0:
            raise ValueError('待分离音频需非空，且为 44.1kHz 双声道格式。')
        n = len(source)
        positions = range(0, n, stride)
        for index, start in enumerate(positions):
            first, last = max(0, start - margin), min(n, start - margin + chunk)
            source.seek(first)
            data = source.read(last - first, dtype='float32', always_2d=True).T
            block = np.zeros((2, chunk), dtype=np.float32)
            left = first - (start - margin)
            block[:, left:left + data.shape[1]] = data
            if not np.isfinite(block).all():
                raise ValueError('输入音频包含异常采样数据。')
            spectrum = torch.stft(torch.from_numpy(block), n_fft=N_FFT, hop_length=HOP,
                window=window, center=True, return_complex=True)
            packed = torch.view_as_real(spectrum[:, :BINS, :]).permute(0, 3, 1, 2).reshape(1, 4, BINS, FRAMES)
            values = packed.numpy().copy()
            values[:, :, :3, :] = 0
            output = session.run(None, {name: values})[0]
            if output.shape != (1, 4, BINS, FRAMES) or not np.isfinite(output).all():
                raise RuntimeError('人声分离模型输出异常。')
            pair = torch.from_numpy(output).reshape(2, 2, BINS, FRAMES).permute(0, 2, 3, 1).contiguous()
            predicted = torch.nn.functional.pad(torch.view_as_complex(pair), (0, 0, 0, N_FFT // 2 + 1 - BINS))
            wave = torch.istft(predicted, n_fft=N_FFT, hop_length=HOP, window=window,
                center=True, length=chunk).numpy()
            count = min(usable, n - start)
            accum[:, :count] += wave[:, margin:margin + count] * blend[:count]
            weights[:count] += blend[:count]
            finished = min(stride, n - start)
            if not np.all(weights[:finished] > 0):
                raise RuntimeError('人声分离未覆盖完整音频。')
            instrument = (accum[:, :finished] / weights[:finished] * compensation).astype(np.float32)
            vocal = block[:, margin:margin + finished] - instrument
            if not np.isfinite(vocal).all() or float(np.abs(vocal).max()) > 16:
                raise RuntimeError('人声分离结果包含异常采样数据。')
            target.write(vocal.T)
            vocal_peak = max(vocal_peak, float(np.abs(vocal).max()))
            instrument_peak = max(instrument_peak, float(np.abs(instrument).max()))
            accum[:, :-stride] = accum[:, stride:]
            accum[:, -stride:] = 0
            weights[:-stride] = weights[stride:]
            weights[-stride:] = 0
            progress(15 + round(75 * (index + 1) / len(positions)),
                     f'正在分离整曲人声：{index + 1} / {len(positions)}')
    return dict(samples=n, vocal_peak=vocal_peak, instrument_peak=instrument_peak,
                chunk_samples=chunk, stride_samples=stride, chunks=len(positions),
                strategy='streaming-global-overlap-add-v1')


def separate(request, emit):
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf
    from local_models import ModelManager,UVR,separation_spec,SEPARATION_PROFILES
    info=separator_info(request["device"])
    spec=separation_spec(request.get("separation_model_id",UVR.id))
    profile=SEPARATION_PROFILES[spec.id]
    manager=ModelManager(request["cache_root"])
    model=manager.ensure_verified(spec.id)/spec.files[0].name
    source_info=sf.info(request["audio_wav"])
    if source_info.samplerate!=SAMPLE_RATE or source_info.channels!=2:
        raise ValueError('待分离音频需为 44.1kHz 双声道格式。')
    providers=[info["actual_matrix_provider"]]
    options=ort.SessionOptions()
    options.intra_op_num_threads=min(8,__import__("os").cpu_count() or 1)
    if request["device"]=="directml": options.enable_mem_pattern=False
    if request["device"]!="cpu":
        options.add_session_config_entry("session.disable_cpu_ep_fallback","1")
    started=time.monotonic()
    try:
        session=ort.InferenceSession(str(model),sess_options=options,providers=providers)
    except Exception:
        raise
    session.disable_fallback()
    if session.get_providers()[0]!=providers[0]:
        raise RuntimeError('人声分离未能启用所选计算设备，请检查显卡驱动或改选 CPU。')
    try:
        streaming=infer_stream(request['audio_wav'],request['output_wav'],session,
                               progress=emit,compensation=profile["compensation"])
    except Exception:
        raise
    actual=sf.info(request["output_wav"])
    if actual.frames!=source_info.frames or actual.samplerate!=SAMPLE_RATE or actual.channels!=2:
        raise RuntimeError('分离后的人声时长或采样率异常，请重试。')
    info.update(separation_adapter=profile["adapter"],compensation=profile["compensation"],providers=session.get_providers(),requested_providers=providers,
                cpu_ep_fallback_disabled=request["device"]!="cpu",elapsed_seconds=time.monotonic()-started)
    return dict(version=1,model_id=spec.id,revision=spec.revision,runtime=info,adapter=profile["adapter"],
                sample_rate=SAMPLE_RATE,samples=actual.frames,duration=actual.frames/SAMPLE_RATE,
                latency_samples=0,padding_removed=True,vocal_peak=streaming['vocal_peak'],
                instrument_peak=streaming['instrument_peak'],chunking=streaming,
                warning='人声分离可能残留少量伴奏或弱化轻声尾音，打轴后请结合试听微调。')
