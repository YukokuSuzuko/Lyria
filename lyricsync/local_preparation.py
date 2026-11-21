"""Explicit preparation orchestration. Importing/probing never installs anything."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from . import local_runtime_setup as setup
from .local_alignment_runtime import probe_runtime
from .local_models import ModelManager, QWEN, UVR, ASR, separation_spec, SEPARATION_MODELS, check_cancel


class PreparationError(RuntimeError):
    def __init__(self, message, runtime_python):
        super().__init__(message)
        self.partial_result = {'runtime_python': str(runtime_python)}


def python_candidates():
    """Known local interpreters only; no recursive disk search or shell command."""
    candidates = [getattr(sys, '_base_executable', ''), sys.executable]
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r'Software\Python\PythonCore\3.12\InstallPath',
                                        0, winreg.KEY_READ | view) as key:
                        candidates.append(str(Path(winreg.QueryValue(key, None)) / 'python.exe'))
                except OSError:
                    pass
        candidates.append(str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Python/Python312/python.exe'))
    else:
        candidates.append(shutil.which('python3.12') or '')
    return list(dict.fromkeys(p for p in candidates if p and Path(p).is_file()
                             and Path(p).name.lower() in ('python', 'python.exe', 'python3.12')))


def discover_python(*, cancel=lambda: False, progress=lambda p, m: None):
    for candidate in python_candidates():
        check_cancel(cancel)
        try:
            raw = setup._run([candidate, '-I', '-c',
                "import sys,json,venv;print(json.dumps({'version':list(sys.version_info[:2]),'bits':64 if sys.maxsize>2**32 else 32}))"],
                cancel, progress, 0, timeout=15)
            if json.loads(raw.splitlines()[-1]) == {'version': [3, 12], 'bits': 64}:
                return candidate
        except (ValueError, IndexError, OSError, RuntimeError, TimeoutError):
            continue
    raise ValueError('未找到可创建独立环境的 64 位 Python 3.12。尚未安装或下载任何内容；请在高级设置中选择已有兼容环境，或安装官方 Python 3.12 后重试。此版尚不自动下载 Python 本体。')


def prepare_models(*, cache_root, runtime_python='', device='cuda:0', language='Japanese',
                   download_source='huggingface', include_asr=False, separation_model_id=UVR.id,
                   cancel=lambda: False, progress=lambda p, m: None):
    """Called only after disclosure/confirmation; never silently replaces a runtime.

    An explicitly configured broken runtime is reported, not discarded in favour
    of an unrequested new installation. New runtimes use exclusively owned names.
    """
    check_cancel(cancel)
    separator = separation_spec(separation_model_id)
    specs = (QWEN, separator, ASR) if include_asr else (QWEN, separator)
    manager = ModelManager(cache_root)
    # Check the existing nearest ancestor before creating a directory or network call.
    ancestor = manager.root
    while not ancestor.exists() and ancestor.parent != ancestor:
        ancestor = ancestor.parent
    required = sum(spec.size for spec in specs) * 2 + (0 if runtime_python else setup.MIN_FREE_BYTES)
    if shutil.disk_usage(ancestor).free < required:
        raise ValueError(f'空间不足：本次准备至少需预留 {required / 1024**3:.1f} GiB；没有开始下载或安装。')
    if runtime_python:
        result = probe_runtime(runtime_python, device, language, cancel=cancel, progress=progress)
    else:
        base = discover_python(cancel=cancel, progress=progress)
        parent = manager.root.parent / 'runtimes'
        parent.mkdir(parents=True, exist_ok=True)
        target = parent / ('alignment-' + uuid.uuid4().hex)
        result = setup.create_runtime(base, target, device=device, language=language,
                                      cancel=cancel, progress=progress)
        runtime_python = result['runtime_python']
    try:
        for spec in specs:
            check_cancel(cancel)
            progress(0, '准备模型：' + spec.id)
            state = manager.status(spec.id)
            if state.get('wrong_size'):
                raise ValueError('模型文件大小不符，请校验或重新导入。缓存目录：' + state['path'])
            if not state['present']:
                source = download_source if spec.id == QWEN.id else ('uvr' if spec.id in SEPARATION_MODELS else 'huggingface')
                manager.download(spec.id, source=source, cancel=cancel, progress=progress)
            # Existing corrupt weights fail explicitly, never overwritten without a separate action.
            manager.ensure_verified(spec.id, cancel=cancel, progress=progress)
    except Exception as exc:
        # Preserve a successfully prepared environment even if model preparation
        # fails or is cancelled. Retrying must not create a second multi-GB env.
        raise PreparationError(str(exc), runtime_python) from exc
    check_cancel(cancel)
    progress(100, '所选模型校验与运行环境检测通过；实际歌唱识别/对时仍需运行后核对。')
    return {'runtime_python': str(runtime_python), 'runtime': result['runtime'],
            'prepared_models': [spec.id for spec in specs]}
