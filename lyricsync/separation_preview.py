"""Lyrics-free separation: validated local media -> independent audition record.

Never imports or invokes ASR/alignment generation and never receives a project.
Uses the existing verified separation worker; no implicit model downloads.
"""
import math
from pathlib import Path
from .local_models import UVR, separation_spec, check_cancel
from .local_alignment_runtime import validate_runtime_path
from .local_separation import prepare_vocals


def validate_separation_options(options):
    value=dict(options)
    value.setdefault('separation_model_id', UVR.id)
    separation_spec(value['separation_model_id'])
    value['runtime_python']=str(validate_runtime_path(value.get('runtime_python','')))
    source=Path(value.get('audio_path','')).expanduser()
    if not source.is_file():raise ValueError('请选择有效的音频或视频文件。')
    value['audio_path']=str(source.resolve())
    value.setdefault('offset_seconds',0.)
    if value.get('duration_seconds') is None:
        from .local_alignment import discover_media_duration
        value['duration_seconds']=discover_media_duration(source)-value['offset_seconds']
    if value.get('device') not in ('cpu','cuda:0','directml'):raise ValueError('请选择 GPU（CUDA）或 CPU 计算设备。')
    for key in ('offset_seconds','duration_seconds'):
        if type(value.get(key)) not in (int,float) or not math.isfinite(value[key]):
            raise ValueError('人声分离的片段时间填写有误。')
    if value['offset_seconds']<0 or not 0<value['duration_seconds']:
        raise ValueError('人声分离的起点需非负，时长需大于零。')
    return value


def file_stamp(path):
    stat=Path(path).stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def generate_separation_preview(*, options, cancel=lambda:False, progress=lambda p,m:None):
    check_cancel(cancel)
    settings=validate_separation_options(options)
    source_stamp=file_stamp(settings['audio_path'])
    stem=prepare_vocals(settings,cancel=cancel,progress=progress)
    check_cancel(cancel)
    if file_stamp(settings['audio_path'])!=source_stamp:
        raise ValueError('人声分离期间原素材发生变化，请重新分离后试听。')
    result=dict(stem,kind='separation_preview',stem_path=stem['path'],
                source_stamp=source_stamp,stem_stamp=file_stamp(stem['path']))
    progress(100,'人声分离完成，可试听比较；未运行歌词识别或对齐，未生成时间轴。')
    return result
