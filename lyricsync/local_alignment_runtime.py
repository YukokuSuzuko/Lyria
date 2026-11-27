"""Cancellable process boundary shared by alignment and the explicit probe."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from .local_models import check_cancel
from .owned_process import launch_owned


def task_timeout(duration, operation='alignment'):
    """Scale the owned worker budget with complete audio, including CPU runs."""
    import math
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('任务音频时长无效。')
    multiplier = 30 if operation in ('alignment', 'transcribe') else 15
    return max(1800., 300. + duration * multiplier)

def worker_script():
    # PyInstaller must ship this executable .py plus local_models.py as data.
    return Path(__file__).resolve().with_name("local_alignment_worker.py")

def validate_runtime_path(value, *, allow_incomplete=False):
    from .shared_runtime import discover_shared_runtime
    try:shared = discover_shared_runtime()
    except (OSError,ValueError):
        if not str(value).strip():raise
        shared = None
    if not str(value).strip() and shared is not None:value = str(shared)
    path = Path(value).expanduser().resolve()
    if shared is not None and path == shared:
        return path  # Identity only here; complete background check in run_worker.
    if not path.is_file() or path.name.lower() not in ("python.exe", "python"):
        raise ValueError("请选择独立 Python 3.12 虚拟环境中的 python.exe。")
    if path == Path(sys.executable).resolve():
        raise ValueError('不能直接选择 Lyria 软件自身的 Python，请选择独立虚拟环境中的 python.exe。')
    if not (path.parent.parent / "pyvenv.cfg").is_file():
        raise ValueError('所选路径不是 Python 虚拟环境（缺少 pyvenv.cfg），请重新选择。')
    if not allow_incomplete and (path.parent.parent / "LYRIA_INSTALL_INCOMPLETE").exists():
        raise ValueError('该运行环境上次未安装完整，请更换新目录重新创建环境。')
    config=(path.parent.parent/"pyvenv.cfg").read_text(encoding="utf-8")
    entries=[]
    for line in config.splitlines():
        key,separator,value=line.partition("=")
        if separator and key.strip().casefold()=="include-system-site-packages":
            entries.append(value.strip().casefold())
    if entries != ["false"]:
        raise ValueError('虚拟环境开启了系统包继承，请使用不带 --system-site-packages 的纯净虚拟环境。')
    return path

def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _stop_worker(process):
    """Stop our active Windows PID tree before killing a venv redirector alone."""
    owned_job=getattr(process,"_lyria_job",None)
    if owned_job is not None:
        try:
            owned_job.stop()
        finally:
            _stop(process)
        return
    tree_error = None
    if os.name == "nt" and process.poll() is None:
        try:
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                tree_error = RuntimeError('未能确认后台模型进程已全部退出，请检查是否有残留进程：' +
                                          str(result.stdout or "")[-1000:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            tree_error = RuntimeError('结束后台模型进程失败，可能仍有残留子进程：' + str(exc))
    _stop(process)
    if tree_error is not None:
        raise tree_error

class PartialAlignmentError(RuntimeError):
    def __init__(self,result,reason):
        super().__init__('对齐已中断；存在已完成分段，须确认保留或放弃。')
        self.result=result
        self.reason=reason


def run_worker(request,runtime_python,**kwargs):
    if not getattr(sys,'frozen',False) or request.get('device') not in ('cuda:0','directml') or kwargs.get('script'):
        return _run_worker(request,runtime_python,**kwargs)
    from .shared_runtime import TASK_VARIANT, application_dir, extension_root
    from .split_profiles import ACTIVE
    app=application_dir()
    def _bundle_py(variant):
        try:return extension_root(app,variant)/'shared-runtime/python.exe'
        except (ValueError,KeyError,TypeError,OSError):return None
    bundled_paths=[app.parent/'shared-runtime/python.exe', _bundle_py('nvidia'), _bundle_py('amd')]
    if runtime_python and Path(runtime_python).absolute() not in bundled_paths:
        return _run_worker(request,runtime_python,**kwargs)
    if request['device'] == 'cuda:0':
        if ACTIVE == 'amd' and request.get('operation') == 'separate':
            request = dict(request, device='directml')
            requested_profiles = ('directml', 'amd')
        elif ACTIVE in ('nvidia', 'amd'):
            requested_profiles = (ACTIVE,)
        else:
            requested_profiles = ('nvidia', 'amd')
    else:
        requested_profiles = ('directml', 'nvidia', 'amd')
    try:
        if ACTIVE in requested_profiles:
            token = TASK_VARIANT.set('cpu' if request.get('device') == 'directml' else ACTIVE)
            try:
                return _run_worker(request,runtime_python,**kwargs)
            finally:
                TASK_VARIANT.reset(token)
        reason='缺少兼容扩展'
    except RuntimeError as exc:
        if isinstance(exc,PartialAlignmentError):raise
        reason=str(exc)
        if not any(x in reason.lower() for x in ('cuda','cudnn','directml','execution provider','executionprovider','dml','显卡','gpu','device','runtimeexception','onnxruntimeerror','dll','共享运行环境','shared runtime')):raise
    except (OSError,ValueError) as exc:
        reason=str(exc)
        if 'runtime' not in reason.lower() and not isinstance(exc,FileNotFoundError):raise
    token=TASK_VARIANT.set('cpu')
    try:
        kwargs.get('progress',lambda p,m:None)(0,'加速不可用，本次任务改用 CPU。'+reason)
        fallback=dict(request,device='cpu')
        if request.get('operation')=='separate':
            output=Path(request['output_wav'])
            with tempfile.TemporaryDirectory(prefix='lyria-cpu-retry-',dir=output.parent) as folder:
                partial=Path(folder)/'vocals.wav'
                result=_run_worker(dict(fallback,output_wav=str(partial)),'',**kwargs)
                os.replace(partial,output)
        else:result=_run_worker(fallback,'',**kwargs)
        result.setdefault('runtime',{}).update(fallback_from=request['device'],fallback_reason=reason)
        return result
    finally:TASK_VARIANT.reset(token)

def _run_worker(request, runtime_python, *, cancel=lambda: False, progress=lambda p, m: None,
               timeout=1800, script=None, allow_incomplete_runtime=False):
    check_cancel(cancel)
    from .bundled_reading import BUNDLED_RUNTIME, bundled_available
    bundled = runtime_python == BUNDLED_RUNTIME
    if bundled:
        if script is not None or request.get('operation') not in ('readings','reading_probe') or not bundled_available():
            raise ValueError('Bundled dictionary worker unavailable or invalid operation')
        runtime = Path(sys.executable).resolve()
    else:
        runtime = validate_runtime_path(runtime_python, allow_incomplete=allow_incomplete_runtime)
    if not bundled and getattr(sys,'frozen',False):
        from .split_profiles import ACTIVE, REASON
        from .shared_runtime import discover_shared_runtime
        if runtime == discover_shared_runtime():
            requested = request.get('device')
            if (requested == 'cuda:0' and ACTIVE not in ('nvidia', 'amd')) or (requested == 'directml' and ACTIVE not in ('directml', 'nvidia', 'amd')):
                progress(0,'加速扩展不可用，本次任务使用 CPU。' + REASON)
                request = dict(request, device='cpu')
    shared_attestation = None
    if not bundled:
        from .shared_runtime import discover_shared_runtime, verify_shared_runtime
        try:shared = discover_shared_runtime()
        except (OSError,ValueError):shared = None
        if shared is not None and runtime == shared:
            if script is not None and Path(script).resolve() != worker_script().resolve():
                raise ValueError('Shared runtime only launches the shipped model worker')
            shared_attestation = verify_shared_runtime(runtime, cancel=cancel, progress=progress)
    script = Path(script or worker_script())
    if not bundled and not script.is_file():
        raise RuntimeError('缺少本地模型运行组件，请使用完整安装包或重新解压软件。')
    with tempfile.TemporaryDirectory(prefix="lyria-local-") as folder:
        root = Path(folder)
        incoming, outgoing = root / "request.json", root / "result.json"
        checkpoint=root/'partial.json'
        request=dict(request)
        request.pop('_shared_runtime_attestation', None)
        if shared_attestation is not None:request['_shared_runtime_attestation']=shared_attestation
        if request.get('operation','align')=='align':request['_partial_result_path']=str(checkpoint)
        incoming.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            env.pop(key, None)
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", PYTHONNOUSERSITE="1")
        if shared_attestation is not None:
            cache = Path(request.get('cache_root') or (root / 'runtime-cache')).absolute() / 'runtime-cache'
            from .shared_runtime import discover_shared_runtime
            runtime_root = discover_shared_runtime().parent
            if cache.resolve().is_relative_to(runtime_root.resolve()):
                raise ValueError('Runtime caches must be outside the shared software root')
            cache.mkdir(parents=True, exist_ok=True)
            env['PATH']=os.pathsep.join([str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'),str(runtime_root),str(runtime_root/'DLLs')])
            if request.get('device')=='cuda:0':
                site = runtime_root / 'Lib' / 'site-packages'
                extra = [str(site / 'torch' / 'lib')]
                for rocm_bin in (site / '_rocm_sdk_core' / 'bin', site / '_rocm_sdk_libraries_custom' / 'bin'):
                    if rocm_bin.is_dir():
                        extra.append(str(rocm_bin))
                env['PATH'] = os.pathsep.join(extra) + os.pathsep + env['PATH']
            env.update(NUMBA_CACHE_DIR=str(cache/'numba'), HF_HOME=str(cache/'huggingface'),
                       TORCH_HOME=str(cache/'torch'), XDG_CACHE_HOME=str(cache),
                       MPLCONFIGDIR=str(cache/'matplotlib'), PYTHONDONTWRITEBYTECODE='1')
        events = queue.Queue(maxsize=128)
        command = ([str(runtime), '--reading-worker'] if bundled else [str(runtime), '-I', '-B', '-u', str(script)])
        command += ['--request', str(incoming), '--result', str(outgoing)]
        process = launch_owned(command,
                                   cwd=str(runtime.parent) if shared_attestation is not None else None,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env)
        diagnostics = []
        def stdout_reader():
            # Each reader owns its pipe. Closing it from another thread can block
            # on TextIOWrapper's read lock if a descendant inherited the handle.
            try:
                while line := process.stdout.readline(16385):
                    if len(line) > 16384:
                        continue
                    try:
                        item = json.loads(line)
                        events.put_nowait(item)
                    except (ValueError, queue.Full):
                        pass
            finally:
                process.stdout.close()
        def stderr_reader():
            try:
                while line := process.stderr.readline(4097):
                    diagnostics.append(line[-4096:])
                    del diagnostics[:-10]
            finally:
                process.stderr.close()
        threads = [threading.Thread(target=stdout_reader, daemon=True),
                   threading.Thread(target=stderr_reader, daemon=True)]
        for thread in threads:
            thread.start()
        error = ""
        started = time.monotonic()
        def drain():
            nonlocal error
            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                if isinstance(event, dict) and "error" in event:
                    error = str(event["error"])[:8000]
                elif isinstance(event, dict) and type(event.get("progress")) in (float, int):
                    progress(max(0, min(100, int(event["progress"]))), str(event.get("message", ""))[:1000])
        try:
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() - started > timeout:
                    raise TimeoutError('本地模型运行超时，已自动停止任务。')
                drain()
                time.sleep(.05)
            for thread in threads:
                thread.join(timeout=2)
            drain()
            check_cancel(cancel)
            if process.returncode != 0:
                if bundled and outgoing.is_file() and outgoing.stat().st_size <= 65536:
                    try:error = str(json.loads(outgoing.read_text('utf-8')).get('error', error))
                    except (ValueError,OSError):pass
                raise RuntimeError(error or ('本地模型运行出错：' + "".join(diagnostics)[-3000:]))
            if not outgoing.is_file() or outgoing.stat().st_size > 8 * 1024 * 1024:
                raise RuntimeError('本地模型未生成有效的结果文件。')
            result = json.loads(outgoing.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or result.get("version") != 1:
                raise RuntimeError('本地模型返回数据版本不匹配。')
            return result
        except (InterruptedError,TimeoutError,RuntimeError) as exc:
            if checkpoint.is_file() and checkpoint.stat().st_size<=8*1024*1024:
                try:
                    partial=json.loads(checkpoint.read_text(encoding='utf-8'))
                    if isinstance(partial,dict) and partial.get('version')==1 and partial.get('chunking',{}).get('batches'):
                        raise PartialAlignmentError(partial,'cancelled' if isinstance(exc,InterruptedError) else 'failed') from exc
                except (ValueError,OSError):pass
            raise
        finally:
            cleanup_error = None
            try:
                _stop_worker(process)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                cleanup_error = exc
            for thread in threads:
                thread.join(timeout=2)
            # Never hang the caller by closing a pipe still owned by a reader.
            # Report incomplete cleanup instead of claiming cancellation finished.
            if cleanup_error is not None:
                raise cleanup_error
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError('本地任务输出管道未正常关闭，请检查是否有后台残留进程。')

def probe_runtime(runtime_python, device="cuda:0", language="English", *, cancel=lambda: False, progress=lambda p, m: None, allow_incomplete=False):
    return run_worker({"operation": "probe", "device": device, "language": language},
                      runtime_python, cancel=cancel, progress=progress, timeout=90, allow_incomplete_runtime=allow_incomplete)


