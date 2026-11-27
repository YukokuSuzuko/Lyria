"""Acquire two pinned upstream wheels and reproduce the exact offline NVIDIA runtime."""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import http.client
import json
import msvcrt
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import zipfile

from . import shared_runtime as shared
from .split_pins import PINS
from .storage import atomic_json

TORCH_FILE = 'torch-2.9.1+cu128-cp312-cp312-win_amd64.whl'
ORT_FILE = 'onnxruntime_gpu-1.23.2-cp312-cp312-win_amd64.whl'
TORCH_URL = TORCH_FILE.replace('+','%2B')
WHEELS = {
    'torch': (TORCH_FILE, 2862033275, '3a01f0b64c10a82d444d9fd06b3e8c567b1158b76b2764b8f51bfd8f535064b0', (
        'https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/' + TORCH_URL,
        'https://download-r2.pytorch.org/whl/cu128/' + TORCH_URL,
        'https://download.pytorch.org/whl/cu128/' + TORCH_URL)),
    'onnxruntime-gpu': (ORT_FILE, 244508327, 'fe925a84b00e291e0ad3fac29bfd8f8e06112abc760cdc82cb711b4f3935bd95', (
        'https://files.pythonhosted.org/packages/87/da/2685c79e5ea587beddebe083601fead0bdf3620bc2f92d18756e7de8a636/' + ORT_FILE,)),
}
def wheel_urls(owner, source_mode='mirror'):
    filename, size, digest, official_urls = WHEELS[owner]
    if source_mode == 'official':
        if owner == 'torch':
            return ('https://download.pytorch.org/whl/cu128/' + TORCH_URL,
                    'https://download-r2.pytorch.org/whl/cu128/' + TORCH_URL,
                    *official_urls)
        return official_urls
    if owner == 'onnxruntime-gpu':
        return (
            'https://pypi.tuna.tsinghua.edu.cn/packages/87/da/2685c79e5ea587beddebe083601fead0bdf3620bc2f92d18756e7de8a636/' + ORT_FILE,
            'https://mirrors.aliyun.com/pypi/packages/87/da/2685c79e5ea587beddebe083601fead0bdf3620bc2f92d18756e7de8a636/' + ORT_FILE,
            *official_urls,
        )
    return official_urls


HOSTS = {urlsplit(url).hostname for *_, urls in WHEELS.values() for url in urls} | {
    's3.jcloud.sjtu.edu.cn', 'pypi.tuna.tsinghua.edu.cn', 'mirrors.aliyun.com', 'mirrors.bfsu.edu.cn',
}
CHUNK = 1024 * 1024
RECIPE = 'c95ddfcfb6ccf528b431fbb52f71a0aa3084347748985ee7bcff76ef9bb264b7'

def safe_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (443,None):
        raise ValueError('下载来源不在应用的可信清单中')
    return url

class TrustedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, safe_url(newurl))

def fetch(url):
    request = Request(safe_url(url), headers={'Accept-Encoding':'identity','User-Agent':'Lyria/0.33'})
    return build_opener(TrustedRedirect()).open(request, timeout=20)

def check(cancel):
    if cancel():
        raise InterruptedError('安装已取消')

def driver():
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise ValueError('需要 Windows x64')
    try:
        cuda = ctypes.WinDLL(str(Path(os.environ['SystemRoot'])/'System32/nvcuda.dll'))
        count, version = ctypes.c_int(), ctypes.c_int()
        if cuda.cuInit(0) or cuda.cuDeviceGetCount(ctypes.byref(count)) or not count.value or cuda.cuDriverGetVersion(ctypes.byref(version)) or version.value < 12000:
            raise ValueError('没有可用的 CUDA 12 设备或兼容驱动')
    except OSError as exc:
        raise ValueError('无法加载 NVIDIA 显卡驱动，仍可使用 CPU') from exc
    return {'cuda_driver_api':version.value,'devices':count.value}

def directory(path):
    path = Path(path).absolute()
    for p in reversed([path,*path.parents]):
        if not p.exists():
            p.mkdir(exist_ok=True)
        shared._plain(p)
    return path

@contextmanager
def locked(base):
    from .portable_profile import profiles_root
    path=directory(profiles_root() or (base/'profiles'))/'nvidia-install.lock'
    if path.exists(): shared._plain(path)
    with path.open('a+b') as lock:
        lock.seek(0)
        try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as exc: raise ValueError('另一项 NVIDIA 安装正在进行') from exc
        try: yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

def acquire(owner, cache, temp, cancel, progress, events, source_mode='mirror'):
    filename,size,digest,_=WHEELS[owner]
    urls=wheel_urls(owner, source_mode)
    cached=cache/filename
    if cached.exists():
        shared._plain(cached)
        if cached.stat().st_size==size and shared._hash(cached)==digest:
            events.append(dict(wheel=filename,source='verified-cache',bytes=size,sha256=digest))
            return cached
    part=temp/(filename+'.part')
    for url in urls:
        check(cancel)
        received=0
        try:
            with fetch(url) as response,part.open('wb') as output:
                final=safe_url(response.geturl())
                reported=response.headers.get('Content-Length')
                if response.status!=200 or (reported is not None and int(reported)!=size):
                    raise ValueError('响应长度或状态不匹配')
                h=hashlib.sha256()
                last=-1.
                while True:
                    check(cancel)
                    data=response.read(CHUNK)
                    if not data: break
                    received+=len(data)
                    if received>size: raise ValueError('文件超出固定大小')
                    output.write(data)
                    h.update(data)
                    now=time.monotonic()
                    if now-last>.65:
                        progress(round(60*received/size),f'{owner} · {urlsplit(final).hostname} · {received//CHUNK} / {size//CHUNK} MiB')
                        last=now
            if received!=size or h.hexdigest()!=digest:
                raise ValueError('wheel 字节数或 SHA-256 不匹配')
            check(cancel)
            os.replace(part,cached)
            events.append(dict(wheel=filename,source=url,final=final,bytes=received,sha256=digest))
            return cached
        except InterruptedError:
            raise
        except (OSError,ValueError,http.client.HTTPException) as exc:
            events.append(dict(wheel=filename,source=url,received=received,error=str(exc)))
            progress(0,'下载未通过：'+str(exc)+'，检查其他固定来源…')
            part.unlink(missing_ok=True)
    raise ValueError(owner+' 未能从固定来源取得完整文件。'+str(events[-1].get('error','')))

def copy_checked(stream, dst, row, cancel):
    h=hashlib.sha256()
    count=0
    with dst.open('xb') as output:
        while True:
            check(cancel)
            data=stream.read(CHUNK)
            if not data: break
            count+=len(data)
            if count>row['bytes']:raise ValueError('文件超过清单大小：'+row['path'])
            h.update(data)
            output.write(data)
    if count!=row['bytes'] or h.hexdigest()!=row['sha256']:
        raise ValueError('运行时文件校验失败：'+row['path'])

def assemble(app, stage, wheels, cancel, progress):
    cpu=app.parent/'shared-runtime'
    recipe=shared._plain(app/'_internal/resources/nvidia-online-recipe.zip')
    if shared._hash(recipe)!=RECIPE or shared._hash(shared._plain(cpu/shared.MANIFEST))!=PINS['cpu']:
        raise ValueError('应用依赖清单签名摘要不匹配')
    cpu_files={r['path']:r['sha256'] for r in json.loads((cpu/shared.MANIFEST).read_bytes())['files']}
    rt=stage/'shared-runtime'
    rt.mkdir(parents=True)
    with ExitStack() as stack:
        archives={k:stack.enter_context(zipfile.ZipFile(v)) for k,v in wheels.items()}
        receipts=stack.enter_context(zipfile.ZipFile(recipe))
        metadata=receipts.read(shared.MANIFEST)
        if hashlib.sha256(metadata).hexdigest()!=PINS['nvidia']:
            raise ValueError('扩展清单不匹配')
        rows=json.loads(metadata)['files']
        for index,row in enumerate(rows):
            check(cancel)
            name=row['path']
            out=rt/name
            out.parent.mkdir(parents=True,exist_ok=True)
            if cpu_files.get(name)==row['sha256']:
                # All owned output directories are newly created. Reject linked input files.
                with shared._plain(cpu/name).open('rb') as stream:copy_checked(stream,out,row,cancel)
            elif name in receipts.NameToInfo:
                with receipts.open(name) as stream:copy_checked(stream,out,row,cancel)
            else:
                member=name.removeprefix('Lib/site-packages/')
                archive=archives[row['owner']]
                info=archive.getinfo(member)
                if info.file_size!=row['bytes']:raise ValueError('上游文件大小不匹配：'+member)
                with archive.open(info) as stream:copy_checked(stream,out,row,cancel)
            if index%500==0:progress(65+round(28*index/len(rows)),f'校验并组装 {index} / {len(rows)}')
        (rt/shared.MANIFEST).write_bytes(metadata)
    return rt

PROBE = """import sys,torch,json
sys.path.insert(0,sys.argv[1])
from local_separation_worker import separator_info
assert sys.version_info[:3]==(3,12,15) and torch.__version__=='2.9.1+cu128' and torch.version.cuda=='12.8'
x=torch.ones(4,device='cuda:0')
assert (x@x).item()==4
result=separator_info('cuda:0')
assert result['actual_matrix_provider']=='CUDAExecutionProvider'
print(json.dumps(dict(result,torch=torch.__version__,cuda=torch.version.cuda,cudnn=torch.backends.cudnn.version(),gpu=torch.cuda.get_device_name(0))))
"""

def trial(app,rt):
    env=dict(os.environ,PATH=os.pathsep.join([str(Path(os.environ['SystemRoot'])/'System32'),str(rt),str(rt/'DLLs')]),CUDA_CACHE_DISABLE='1')
    result=subprocess.run([rt/'python.exe','-I','-B','-c',PROBE,str(app/'_internal/lyricsync')],cwd=rt,env=env,capture_output=True,timeout=100,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('CUDA / ONNX 实算失败。请检查显卡与驱动，仍可使用 CPU。')
    return json.loads(result.stdout.splitlines()[-1])

def install(app, selected, cancel, progress, *, source_mode='mirror'):
    app=shared._plain(app)
    base=app.parent
    hw=driver()
    with locked(base):
        prior=shared.extension_root(app,'nvidia')
        if prior.is_dir():
            rt=prior/'shared-runtime'
            shared.verify_shared_runtime(rt/'python.exe',app=app,variant='nvidia',cancel=cancel,progress=progress)
            check(cancel)
            result=trial(app,rt)
            check(cancel)
            from .split_profiles import activate_now, set_preference
            set_preference('nvidia')
            activate_now('nvidia', cancel=cancel)
            return dict(status='existing-verified',device=result,target=str(prior))
        container=directory(Path(selected)/'Lyria-accelerators' if selected else prior.parent)
        dest=container/'nvidia'
        if dest.exists():raise ValueError('所选位置已有扩展。原文件已保留。')
        cache=directory(container/'.lyria-downloads')
        missing=sum(size for name,size,_,_ in WHEELS.values() if not (cache/name).is_file())
        if shutil.disk_usage(container).free < 5502000000+missing+128*1024**2:
            raise ValueError('目标磁盘可用空间不足，首次安装建议预留 9 GB')
        events=[]
        with tempfile.TemporaryDirectory(dir=container,prefix='.nvidia-install-') as temp:
            temp=Path(temp)
            wheels={k:acquire(k,cache,temp,cancel,progress,events,source_mode=source_mode) for k in WHEELS}
            stage=temp/'nvidia'
            rt=assemble(app,stage,wheels,cancel,progress)
            progress(96,'运行 PyTorch 和 ONNX Runtime 显卡矩阵试算…')
            device=trial(app,rt)
            check(cancel)
            locator=base/'location-nvidia.json'
            previous=shared._plain(locator).read_bytes() if locator.exists() else None
            changed=bool(selected)
            stage.rename(dest)
            try:
                if changed:atomic_json(locator,{'directory':str(dest)})
                from .split_profiles import activate_now, set_preference
                set_preference('nvidia')
                activate_now('nvidia', cancel=cancel)
            except Exception:
                if changed:
                    if previous is None:locator.unlink(missing_ok=True)
                    else:atomic_json(locator,json.loads(previous))
                dest.rename(stage)
                raise
        return dict(status='installed',hardware=hw,device=device,downloads=events,target=str(dest),manifest_sha256=PINS['nvidia'])

