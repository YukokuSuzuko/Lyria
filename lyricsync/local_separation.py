"""Mandatory local separation orchestration and verifiable time mapping.

Rebuilt after the recovery incident; no cached-stem bypass or source upload.
"""
import array
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import uuid
from .local_models import ModelManager, UVR, separation_spec, SEPARATION_PROFILES, check_cancel, _plain_path
from .local_alignment_runtime import run_worker, task_timeout


def file_digest(path, cancel=lambda:False):
    path=Path(path)
    before=path.stat()
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        while chunk:=stream.read(1024*1024):
            check_cancel(cancel)
            digest.update(chunk)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise ValueError('读取期间原音频文件发生了变化，请重试。')
    check_cancel(cancel)
    return digest.hexdigest()


def _validate_float_wav(path, expected_samples, cancel):
    """Validate the actual float WAV independently, without editor ML packages."""
    with Path(path).open("rb") as stream:
        header=stream.read(12)
        if len(header)!=12 or header[:4]!=b"RIFF" or header[8:]!=b"WAVE":
            raise ValueError('人声分离输出的 WAV 文件格式无效。')
        fmt=None; data=None
        while block:=stream.read(8):
            if len(block)!=8:
                raise ValueError('人声分离输出的 WAV 文件不完整。')
            tag,size=struct.unpack("<4sI",block)
            position=stream.tell()
            if position+size>Path(path).stat().st_size:
                raise ValueError('人声分离输出的 WAV 文件已损坏。')
            if tag==b"fmt ":fmt=stream.read(min(size,40))
            if tag==b"data":data=(position,size)
            stream.seek(position+size+(size%2))
        if fmt is None or len(fmt)<16 or data is None:
            raise ValueError('人声分离输出的 WAV 文件缺少音频数据。')
        kind,channels,rate,byte_rate,align,bits=struct.unpack("<HHIIHH",fmt[:16])
        if (kind,channels,rate,align,bits)!=(3,2,44100,8,32) or data[1]!=expected_samples*8:
            raise ValueError('人声分离输出的音频参数异常，请重试。')
        stream.seek(data[0]);remaining=data[1];peak=0.
        while remaining:
            check_cancel(cancel)
            block=stream.read(min(remaining,65536))
            if not block:raise ValueError('人声分离输出文件不完整，请检查磁盘空间后重试。')
            samples=array.array("f");samples.frombytes(block)
            if sys.byteorder!="little":samples.byteswap()
            if any(not math.isfinite(x) or abs(x)>16 for x in samples):
                raise ValueError('人声分离输出包含异常音频数据，请重试。')
            peak=max(peak,max(map(abs,samples),default=0.))
            remaining-=len(block)
        return peak


def prepare_vocals(settings, *, cancel=lambda:False, progress=lambda p,m:None):
    from .local_alignment import _extract_audio, discover_media_duration
    source=Path(settings["audio_path"]).resolve()
    manager=ModelManager(settings["cache_root"])
    spec = separation_spec(settings.get("separation_model_id", UVR.id))
    profile = SEPARATION_PROFILES[spec.id]
    manager.ensure_verified(spec.id,cancel=cancel)
    source_hash=file_digest(source,cancel)
    offset=settings.get('offset_seconds',0.)
    duration=settings.get('duration_seconds')
    if duration is None:
        duration=discover_media_duration(source)-offset
    if not all(type(v) in (int,float) and math.isfinite(v) for v in (offset,duration)) or offset<0 or not 0<duration:
        raise ValueError('人声分离的起点需非负，时长需大于零。')
    root=_plain_path(manager.root/"stems-v1");root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lyria-separate-",dir=root) as folder:
        incoming=Path(folder)/"mix.wav";outgoing=Path(folder)/"vocals.wav"
        actual=_extract_audio(source,incoming,offset,duration,cancel,sample_rate=44100,channels=2)
        response=run_worker(dict(operation="separate",audio_wav=str(incoming),output_wav=str(outgoing),
                           cache_root=str(manager.root),device=settings["device"],separation_model_id=spec.id),settings["runtime_python"],
                           cancel=cancel,progress=progress,timeout=task_timeout(actual,'separate'))
        samples=round(actual*44100)
        if (response.get("model_id")!=spec.id or response.get("revision")!=spec.revision or
            response.get("adapter")!=profile["adapter"] or response.get("sample_rate")!=44100 or
            response.get("samples")!=samples or response.get("latency_samples")!=0 or
            response.get("padding_removed") is not True or abs(response.get("duration",-1)-actual)>.0001):
            raise ValueError('人声分离结果的时长或校验信息不匹配，请重试。')
        peak=_validate_float_wav(outgoing,samples,cancel)
        if file_digest(source,cancel)!=source_hash:raise ValueError('人声分离期间原音频文件发生了变化，请重试。')
        ident=uuid.uuid4().hex;target=_plain_path(root/ident);target.mkdir()
        output=target/"vocals.wav";manifest=target/"mapping.json"
        check_cancel(cancel);shutil.copy2(outgoing,output)
        result=dict(version=1,id=ident,source_path=str(source),source_sha256=source_hash,path=str(output),
                    sha256=file_digest(output,cancel),manifest_path=str(manifest),offset_seconds=offset,
                    duration_seconds=actual,sample_rate=44100,samples=samples,latency_samples=0,padding_removed=True,
                    time_mapping="project_seconds = offset_seconds + stem_sample / sample_rate",
                    model_id=spec.id,model_revision=spec.revision,model_sha256=spec.files[0].sha256,
                    adapter=response["adapter"],runtime=response["runtime"],vocal_peak=peak,
                    alignment_gain=1/max(1,peak),warning=response["warning"])
        check_cancel(cancel)
        temp=manifest.with_suffix(".tmp");temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        os.replace(temp,manifest)
        return result


def stem_for_candidate(record, *, cancel=lambda:False):
    """Worker-only lookup. Hashes source/stem/manifest, never infers a vocal path."""
    files=record.get("provenance",{}).get("auxiliary_files",{})
    roles=("alignment_audio","separated_vocals","separation_manifest")
    if any(role not in files for role in roles):raise ValueError('该结果缺少对应的人声分轨记录，请重新运行打轴。')
    for role in roles:
        if file_digest(files[role]["path"],cancel)!=files[role]["sha256"]:
            raise ValueError('原音频或人声分轨文件已变动，请重新生成。')
    data=json.loads(Path(files["separation_manifest"]["path"]).read_text(encoding="utf-8"))
    spec = separation_spec(data.get("model_id"))
    if (data.get("model_revision")!=spec.revision or
        Path(data["path"]).resolve()!=Path(files["separated_vocals"]["path"]).resolve() or
        data.get("sha256")!=files["separated_vocals"]["sha256"] or
        data.get("source_sha256")!=files["alignment_audio"]["sha256"]):
        raise ValueError('人声分轨记录与当前音频不匹配，请重新生成。')
    return data
