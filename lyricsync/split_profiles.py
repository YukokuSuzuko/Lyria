"""Select an application-pinned ORT build before the frozen GUI imports RapidOCR."""
from pathlib import Path
from importlib.machinery import PathFinder
import json
import os
import subprocess
import sys

if __package__:
    from . import shared_runtime as shared
    from .split_pins import PINS
else:
    import shared_runtime as shared
    from split_pins import PINS

ACTIVE = 'cpu'
REASON = ''
_DLL_HANDLES = []

class OrtPackage:
    def __init__(self, root):
        self.root = str(root)

    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'onnxruntime':
            return PathFinder.find_spec(fullname, [self.root])
        return None

def dml_root(cancel=None):
    root=shared.extension_root(shared.application_dir(),'directml')
    manifest = shared._plain(root/'manifest.json')
    if shared._hash(manifest) != PINS['directml']:
        raise ValueError('DirectML 文件清单不匹配')
    entries = json.loads(manifest.read_bytes())
    actual = set()
    for p in root.rglob('*'):
        shared._plain(p)
        if p.is_file() and p != manifest:
            actual.add(p.relative_to(root).as_posix())
    if actual != {v['path'] for v in entries}:
        raise ValueError('DirectML 文件不齐或包含额外文件')
    for f in entries:
        p = root/f['path']
        if p.stat().st_size != f['bytes'] or shared._hash(p, cancel) != f['sha256']:
            raise ValueError('DirectML 文件校验失败')
    return root

def load_ort(site, torch_lib=None):
    sys.dont_write_bytecode = True
    if 'onnxruntime' in sys.modules:
        raise RuntimeError('ORT 已加载，须重启后选择扩展')
    for directory in [site/'onnxruntime/capi'] + ([torch_lib] if torch_lib else []):
        _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
    sys.meta_path.insert(0, OrtPackage(site))
    import onnxruntime
    if torch_lib:
        # cuDNN's optional runtime compiler also uses legacy native search APIs.
        os.environ['PATH']=str(torch_lib)+os.pathsep+os.environ.get('PATH','')
        import ctypes
        _DLL_HANDLES.append(ctypes.WinDLL(str(torch_lib/'nvrtc64_120_0.dll')))
        onnxruntime.preload_dlls(directory=str(torch_lib))
    return onnxruntime

def set_preference(name):
    from .storage import atomic_json
    if name not in ('auto','cpu','nvidia','amd','directml'):raise ValueError('Invalid acceleration choice')
    override = os.environ.get('LYRIA_PROFILES_DIR', '').strip()
    root = Path(override) if override else (shared.application_dir().parent / 'profiles')
    atomic_json(root/'acceleration-choice.json',{'profile':name})

def prefer_cpu():
    global ACTIVE, OCR_ATTESTATION, REASON
    set_preference('cpu')
    cancel_check()
    with _STATE:
        ACTIVE, OCR_ATTESTATION, REASON = 'cpu', None, ''
        shared.ACTIVE_VARIANT = 'cpu'


def activate_now(preferred=None, cancel=lambda: False):
    """Immediately probe and hot-activate installed GPU/DirectML profiles without restart."""
    global REASON
    _STOP.clear()
    old = os.environ.get('LYRIA_ACCELERATOR')
    try:
        if preferred is not None:
            os.environ['LYRIA_ACCELERATOR'] = preferred
        configure(background=True, cancel=cancel)
    except Exception as exc:
        REASON = '加速校验失败：' + str(exc)
    finally:
        if old is None:
            os.environ.pop('LYRIA_ACCELERATOR', None)
        else:
            os.environ['LYRIA_ACCELERATOR'] = old
    return ACTIVE

import threading
import time
from contextlib import contextmanager

OCR_ATTESTATION = None
OCR_NOTICE = ''
CHECKING = False
_STOP = threading.Event()
_STATE = threading.Lock()
_THREAD = None
_OCR_USERS = 0
_IDLE = threading.Condition()


@contextmanager
def ocr_priority():
    global _OCR_USERS
    with _IDLE:
        _OCR_USERS += 1
    try:
        yield
    finally:
        with _IDLE:
            _OCR_USERS -= 1
            _IDLE.notify_all()


def verification_cancelled():
    with _IDLE:
        while _OCR_USERS and not _STOP.is_set():
            _IDLE.wait(.2)
    return _STOP.is_set()


def run_probe(command, cwd, env, cancel):
    with subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW) as child:
        deadline = time.monotonic() + 90
        while child.poll() is None:
            if cancel() or time.monotonic() >= deadline:
                child.kill()
                child.wait()
                raise RuntimeError('加速探测已取消或超时')
            time.sleep(.1)
        return child


def start_background():
    global CHECKING, _THREAD
    if CHECKING or _STOP.is_set():
        return
    CHECKING = True
    def work():
        global CHECKING, REASON
        try:
            configure(background=True,cancel=verification_cancelled)
        except Exception as exc:
            REASON = '加速校验失败：' + str(exc)
        finally:
            CHECKING = False
    _THREAD = threading.Thread(target=work,name='Lyria acceleration verification',daemon=True)
    _THREAD.start()


def cancel_check():
    _STOP.set()


def shutdown_check():
    cancel_check()
    if _THREAD is not None:
        _THREAD.join(timeout=1.5)


def configure(*, background=False, cancel=lambda: False):
    global ACTIVE, REASON, OCR_ATTESTATION
    app=shared.application_dir()
    if app is None: return
    base=app.parent/'shared-runtime'
    extensions=app.parent/'accelerators'
    override = os.environ.get('LYRIA_PROFILES_DIR', '').strip()
    prof = Path(override) if override else (app.parent / 'profiles')
    try: preference=json.loads((prof/'acceleration-choice.json').read_bytes()).get('profile','auto')
    except (OSError,ValueError,AttributeError):preference='auto'
    priority=os.environ.get('LYRIA_ACCELERATOR',preference)
    choices=['nvidia','amd','directml'] if priority=='auto' else [priority]
    for name in choices:
        if cancel():return
        if name not in ('directml','nvidia','amd'):continue
        try:
            chosen=shared.extension_root(app,name)
            if not chosen.is_dir():continue
            if name=='nvidia':
                runtime=chosen/'shared-runtime'
                attestation = shared.verify_shared_runtime(runtime/'python.exe',variant=name,cancel=cancel)
                # Check real hardware, ABI and kernel before loading an optional native module in the GUI.
                child_env=dict(os.environ, PATH=os.pathsep.join([str(Path(os.environ['SystemRoot'])/'System32'),str(runtime),str(runtime/'DLLs')]))
                probe="import sys,torch;sys.path.insert(0,sys.argv[1]);from local_separation_worker import separator_info;assert (torch.ones(4,device='cuda:0') @ torch.ones(4,device='cuda:0')).item()==4;separator_info('cuda:0')"
                result=run_probe([runtime/'python.exe','-I','-B','-c',probe,str(app/'_internal/lyricsync')],cwd=runtime,env=child_env,cancel=cancel)
                if result.returncode: raise RuntimeError('CUDA 设备/驱动/推理探测失败')
                site=runtime/'Lib/site-packages'
                torch_lib=site/'torch/lib'
            elif name=='amd':
                runtime=chosen/'shared-runtime'
                attestation = shared.verify_shared_runtime(runtime/'python.exe',variant='amd',cancel=cancel)
                from .online_amd import runtime_path_entries
                child_env=dict(os.environ, PATH=os.pathsep.join(runtime_path_entries(runtime)))
                probe="import torch;assert torch.cuda.is_available();assert (torch.ones(4,device='cuda:0') @ torch.ones(4,device='cuda:0')).item()==4"
                result=run_probe([runtime/'python.exe','-I','-B','-c',probe],cwd=runtime,env=child_env,cancel=cancel)
                if result.returncode: raise RuntimeError('AMD ROCm 设备或推理探测失败')
                # AMD ONNX tasks (RapidOCR / UVR) run via DirectML while PyTorch runs via ROCm.
                site=dml_root(cancel)
                torch_lib=None
                dml_probe="import sys;sys.path.insert(0,sys.argv[1]);from split_profiles import dml_root,load_ort;load_ort(dml_root());from local_separation_worker import separator_info;separator_info('directml')"
                dml_res=run_probe([base/'python.exe','-I','-B','-c',dml_probe,str(app/'_internal/lyricsync')],cwd=base,env=dict(os.environ),cancel=cancel)
                if dml_res.returncode: raise RuntimeError('DirectML 设备或推理探测失败')
            else:
                attestation=None
                site=dml_root(cancel)
                torch_lib=None
                # The DML provider also requires an actual compatible adapter. Verify its kernel in a separate process.
                probe="import sys;sys.path.insert(0,sys.argv[1]);from split_profiles import dml_root,load_ort;load_ort(dml_root());from local_separation_worker import separator_info;separator_info('directml')"
                result=run_probe([base/'python.exe','-I','-B','-c',probe,str(app/'_internal/lyricsync')],cwd=base,env=dict(os.environ),cancel=cancel)
                if result.returncode:raise RuntimeError('DirectML 设备或推理探测失败')
        except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as exc:
            REASON=str(exc)
            continue
        if cancel():return
        if not background:
            load_ort(site,torch_lib)
        with _STATE:
            if background and _STOP.is_set():
                return
            OCR_ATTESTATION = attestation
            REASON = ''
            ACTIVE=name
            shared.ACTIVE_VARIANT=name if name in ('nvidia','amd') else 'cpu'
        return
    if not background:
        load_ort(base/'Lib/site-packages')
