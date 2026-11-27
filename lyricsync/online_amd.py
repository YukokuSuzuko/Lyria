"""Acquire pinned official AMD ROCm 7.2.1 Windows wheels and build an isolated AMD runtime."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as wt
import hashlib
import http.client
import json
import msvcrt
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from . import shared_runtime as shared
from .split_pins import PINS, ROCM_WHEELS
from .storage import atomic_json

HOSTS = {urlsplit(item['url']).hostname for item in ROCM_WHEELS.values()} | {'repo.radeon.com'}
CHUNK = 1024 * 1024
SUPPORTED_AMD_SERIES = (
    'RX 90', 'RX 79', 'RX 78', 'RX 77', 'RX 76', 'RX 75', 'RADEON 890M', 'RADEON 880M',
    'RADEON 8060S', 'RADEON 8050S', 'RADEON 8040S', 'RYZEN AI MAX', 'PRO W7', 'AI PRO R',
)
LEGACY_AMD_SERIES = (
    'RX 6', 'RX 5', 'RX 4', 'VEGA', 'R9 ', 'R7 ', 'R5 ', 'HD ', '680M', '660M', '780M', '760M', '740M',
)


def safe_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (443, None):
        raise ValueError('AMD ROCm 下载来源不在应用的可信清单中')
    return url


class TrustedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, safe_url(newurl))


def fetch(url):
    request = Request(safe_url(url), headers={'Accept-Encoding': 'identity', 'User-Agent': 'Lyria/0.33'})
    return build_opener(TrustedRedirect()).open(request, timeout=25)


def check(cancel):
    if cancel():
        raise InterruptedError('安装已取消')


def _amd_display_adapters():
    class Display(ctypes.Structure):
        _fields_ = [
            ('cb', wt.DWORD), ('DeviceName', wt.WCHAR * 32), ('DeviceString', wt.WCHAR * 128),
            ('StateFlags', wt.DWORD), ('DeviceID', wt.WCHAR * 128), ('DeviceKey', wt.WCHAR * 128),
        ]
    found = []
    for i in range(32):
        d = Display()
        d.cb = ctypes.sizeof(d)
        if not ctypes.windll.user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
            break
        if 'VEN_1002' in d.DeviceID.upper() and d.DeviceString not in found:
            found.append(d.DeviceString)
    return found


def driver():
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise ValueError('AMD ROCm 加速扩展仅支持 Windows x64。')
    ver = sys.getwindowsversion()
    if ver.major < 10 or (ver.major == 10 and ver.build < 22000):
        raise ValueError('AMD ROCm for Windows 要求 Windows 11；当前系统已内置 DirectML 基础加速。')
    adapters = _amd_display_adapters()
    if not adapters:
        raise ValueError('未检测到 AMD Radeon 显卡；若为 Intel/旧卡，软件已内置 DirectML 基础加速。')
    joined = ' / '.join(adapters).upper()
    allow_unlisted = os.environ.get('LYRIA_ALLOW_UNLISTED_AMD', '').strip() == '1'
    if not allow_unlisted and any(tok in joined for tok in LEGACY_AMD_SERIES) and not any(tok in joined for tok in SUPPORTED_AMD_SERIES):
        raise ValueError(
            f'检测到旧架构 AMD 显卡（{adapters[0]}），官方 ROCm 仅支持 RX 7000/9000 与 Ryzen AI 300 系列；'
            '无需下载此包，软件已为您内置 DirectML 基础加速（支持 OCR 与人声分离）。'
        )
    return {'adapters': adapters, 'windows_build': ver.build}


def directory(path):
    path = Path(path).absolute()
    for p in reversed([path, *path.parents]):
        if not p.exists():
            p.mkdir(exist_ok=True)
        shared._plain(p)
    return path


@contextmanager
def locked(base):
    from .portable_profile import profiles_root
    path = directory(profiles_root() or (base / 'profiles')) / 'amd-install.lock'
    if path.exists():
        shared._plain(path)
    with path.open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ValueError('另一项 AMD ROCm 安装正在进行') from exc
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def acquire(key, cache, temp, cancel, progress, events):
    item = ROCM_WHEELS[key]
    filename, size, digest, url = item['filename'], item['bytes'], item['sha256'], item['url']
    cached = cache / filename
    if cached.exists():
        shared._plain(cached)
        if cached.stat().st_size == size and shared._hash(cached, cancel) == digest:
            events.append(dict(wheel=filename, source='verified-cache', bytes=size, sha256=digest))
            return cached
    part = temp / (filename + '.part')
    check(cancel)
    received = 0
    try:
        with fetch(url) as response, part.open('wb') as output:
            final = safe_url(response.geturl())
            reported = response.headers.get('Content-Length')
            if response.status != 200 or (reported is not None and int(reported) != size):
                raise ValueError('ROCm 安装包响应长度或状态不匹配')
            h = hashlib.sha256()
            last = -1.0
            while True:
                check(cancel)
                data = response.read(CHUNK)
                if not data:
                    break
                received += len(data)
                if received > size:
                    raise ValueError('ROCm 文件超出固定大小')
                output.write(data)
                h.update(data)
                now = time.monotonic()
                if now - last > 0.65:
                    progress(round(65 * received / size), f'{filename} · {received // CHUNK} / {size // CHUNK} MiB')
                    last = now
        if received != size or h.hexdigest() != digest:
            raise ValueError('AMD ROCm wheel 字节数或 SHA-256 校验未通过')
        check(cancel)
        os.replace(part, cached)
        events.append(dict(wheel=filename, source=url, final=final, bytes=received, sha256=digest))
        return cached
    except InterruptedError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as exc:
        part.unlink(missing_ok=True)
        raise ValueError(f'{filename} 下载失败：{exc}') from exc


def assemble(app, stage, wheels, cancel, progress):
    cpu = app.parent / 'shared-runtime'
    cpu_manifest_path = shared._plain(cpu / shared.MANIFEST)
    if shared._hash(cpu_manifest_path) != PINS['cpu']:
        raise ValueError('基础 CPU 运行环境清单校验不匹配')
    cpu_rows = json.loads(cpu_manifest_path.read_bytes())['files']
    rt = stage / 'shared-runtime'
    rt.mkdir(parents=True)

    # Copy base shared-runtime except CPU torch packages & manifest.
    skip_prefixes = ('Lib/site-packages/torch/', 'Lib/site-packages/torch-', 'Lib/site-packages/torchaudio', 'Lib/site-packages/torchvision')
    kept_total = len(cpu_rows)
    for index, row in enumerate(cpu_rows):
        check(cancel)
        rel = row['path']
        if rel.startswith(skip_prefixes):
            continue
        src = shared._plain(cpu / rel)
        dst = rt / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        with src.open('rb') as fin, dst.open('xb') as fout:
            shutil.copyfileobj(fin, fout, 4 << 20)
        if index % 800 == 0:
            progress(66 + round(14 * index / max(1, kept_total)), f'准备基础运行环境 {index} / {kept_total}')

    site = rt / 'Lib' / 'site-packages'
    wheel_items = list(wheels.items())
    for widx, (key, wheel_path) in enumerate(wheel_items):
        check(cancel)
        with zipfile.ZipFile(wheel_path, 'r') as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            for midx, info in enumerate(members):
                check(cancel)
                name = info.filename.replace('\\', '/')
                if name.startswith('/') or '..' in PurePosixPath(name).parts:
                    raise ValueError('ROCm wheel 包含非法路径: ' + name)
                if name.endswith(('.data/', '.pyc')):
                    continue
                target = site / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as fin, target.open('wb') as fout:
                    shutil.copyfileobj(fin, fout, 4 << 20)
                if midx % 400 == 0:
                    progress(80 + round(10 * (widx + midx / max(1, len(members))) / len(wheel_items)),
                             f'展开 {key} ({midx}/{len(members)})')

    # Generate sealed whole-tree manifest and pin receipt.
    inventory = []
    for root, dirs, files in os.walk(rt, followlinks=False):
        dirs.sort()
        files.sort()
        for fname in files:
            p = Path(root) / fname
            rel = p.relative_to(rt).as_posix()
            if rel == shared.MANIFEST:
                continue
            inventory.append({'path': rel, 'bytes': p.stat().st_size, 'sha256': shared._hash(p, cancel)})
    manifest_bytes = json.dumps({
        'schema': 1,
        'python': '3.12.15',
        'platform': 'windows-amd64',
        'variant': 'amd-rocm-7.2.1',
        'files': inventory,
    }, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    (rt / shared.MANIFEST).write_bytes(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    (stage / 'lyria-amd-receipt.json').write_text(json.dumps({
        'schema': 1,
        'variant': 'amd',
        'manifest_sha256': manifest_sha,
        'wheels': {k: {'filename': ROCM_WHEELS[k]['filename'], 'sha256': ROCM_WHEELS[k]['sha256']} for k in wheels},
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return rt, manifest_sha


PROBE = """import sys,torch,json
sys.path.insert(0,sys.argv[1])
assert sys.version_info[:3]==(3,12,15)
assert torch.cuda.is_available(), 'ROCm torch.cuda.is_available() is False'
x=torch.ones(4,device='cuda:0')
assert (x@x).item()==4
print(json.dumps({'torch':torch.__version__,'hip':getattr(torch.version,'hip',''),'gpu':torch.cuda.get_device_name(0)}))
"""


def runtime_path_entries(rt):
    site = rt / 'Lib' / 'site-packages'
    entries = [
        str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32'),
        str(rt),
        str(rt / 'DLLs'),
    ]
    for candidate in (
        site / '_rocm_sdk_core' / 'bin',
        site / '_rocm_sdk_libraries_custom' / 'bin',
        site / 'torch' / 'lib',
    ):
        if candidate.is_dir():
            entries.insert(0, str(candidate))
    return entries


def trial(app, rt):
    env = dict(os.environ, PATH=os.pathsep.join(runtime_path_entries(rt)))
    result = subprocess.run(
        [rt / 'python.exe', '-I', '-B', '-c', PROBE, str(app / '_internal/lyricsync')],
        cwd=rt, env=env, capture_output=True, text=True, timeout=100,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or '')[-500:]
        raise ValueError(f'AMD ROCm PyTorch 实算失败（建议更新 Adrenalin 26.2.2+ 驱动；仍可使用内置 DirectML/CPU）：{detail}')
    return json.loads(result.stdout.splitlines()[-1])


def install(app, selected, cancel, progress):
    app = shared._plain(app)
    base = app.parent
    hw = driver()
    with locked(base):
        prior = shared.extension_root(app, 'amd')
        if prior.is_dir() and (prior / 'shared-runtime' / 'lyria-shared-runtime.json').is_file():
            rt = prior / 'shared-runtime'
            shared.verify_shared_runtime(rt / 'python.exe', app=app, variant='amd', cancel=cancel, progress=progress)
            check(cancel)
            result = trial(app, rt)
            check(cancel)
            from .split_profiles import activate_now, set_preference
            set_preference('amd')
            activate_now('amd', cancel=cancel)
            return dict(status='existing-verified', device=result, target=str(prior))
        container = directory(Path(selected) / 'Lyria-accelerators' if selected else prior.parent)
        dest = container / 'amd'
        if dest.exists():
            raise ValueError('所选位置已有 AMD 扩展。原文件已保留。')
        cache = directory(container / '.lyria-downloads')
        missing = sum(item['bytes'] for item in ROCM_WHEELS.values() if not (cache / item['filename']).is_file())
        if shutil.disk_usage(container).free < 5_800_000_000 + missing + 128 * 1024 ** 2:
            raise ValueError('目标磁盘可用空间不足，AMD ROCm 安装建议预留 9 GB')
        events = []
        with tempfile.TemporaryDirectory(dir=container, prefix='.amd-install-') as temp_raw:
            temp = Path(temp_raw)
            wheels = {k: acquire(k, cache, temp, cancel, progress, events) for k in ROCM_WHEELS}
            stage = temp / 'amd'
            rt, manifest_sha = assemble(app, stage, wheels, cancel, progress)
            progress(94, '正在运行 AMD ROCm PyTorch 显卡矩阵试算…')
            device = trial(app, rt)
            check(cancel)
            locator = base / 'location-amd.json'
            previous = shared._plain(locator).read_bytes() if locator.exists() else None
            changed = bool(selected)
            stage.rename(dest)
            try:
                if changed:
                    atomic_json(locator, {'directory': str(dest)})
                from .split_profiles import activate_now, set_preference
                set_preference('amd')
                activate_now('amd', cancel=cancel)
            except Exception:
                if changed:
                    if previous is None:
                        locator.unlink(missing_ok=True)
                    else:
                        atomic_json(locator, json.loads(previous))
                dest.rename(stage)
                raise
        return dict(status='installed', hardware=hw, device=device, downloads=events, target=str(dest), manifest_sha256=manifest_sha)
