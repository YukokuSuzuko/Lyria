"""Opt-in isolated runtime creation and narrowly confirmed reading-tool repair.

Full runtime creation never overwrites an existing environment.

No install occurs at import or probe. The dialog discloses network/disk costs
and obtains an explicit click before invoking this function.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from .local_models import check_cancel, _plain_path
from .local_alignment_runtime import _stop_worker, probe_runtime
from .owned_process import launch_owned

# Official package registries only. No editable installs, VCS, sdist or remote code.
DEPENDENCIES = ("transformers==5.22.2", "numpy==2.2.6", "librosa==0.11.0",
                "jinja2==3.1.6", "nagisa==0.3.1", "soynlp==0.0.493", "sudachipy==0.6.11", "sudachidict_small==20260116")
ORT_VERSION = "1.23.2"
TORCH_VERSION = "2.9.1"
TORCH_INDEXES = {"cpu": "https://download.pytorch.org/whl/cpu",
                 "cuda:0": "https://download.pytorch.org/whl/cu128"}
# pip 25.0.1 supports timeout/retries but not download resume.
# Large official CUDA wheels need a less brittle socket timeout; retries stay bounded.
PIP_TIMEOUT_SECONDS = 120
PIP_RETRIES = 2
MIN_FREE_BYTES = 12 * 1024**3

def _run(command, cancel, progress, phase, env=None, timeout=1800):
    check_cancel(cancel)
    process = launch_owned(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env)
    lines = queue.Queue(maxsize=100)
    def reader():
        # Reader owns the pipe; cross-thread close can block on its read lock.
        try:
            while line := process.stdout.readline(8193):
                try:
                    lines.put_nowait(line[-8192:].strip())
                except queue.Full:
                    pass
        finally:
            process.stdout.close()
    thread = threading.Thread(target=reader, name="Lyria-installer-output", daemon=True)
    thread.start()
    started, tail = time.monotonic(), []
    def drain():
        while True:
            try: line = lines.get_nowait()
            except queue.Empty: break
            tail.append(line)
            del tail[:-8]
            progress(phase, line[:1000])
    try:
        parent_exited_at = None
        while process.poll() is None or thread.is_alive():
            check_cancel(cancel)
            if process.poll() is not None:
                parent_exited_at = parent_exited_at or time.monotonic()
                if time.monotonic() - parent_exited_at > 2:
                    raise RuntimeError('安装程序已退出，但仍有后台子进程未释放管道，环境可能未安装完整。')
            if time.monotonic() - started > timeout:
                raise TimeoutError('运行环境安装超时，请检查网络后更换新目录重试。')
            drain()
            time.sleep(.1)
        drain()
        check_cancel(cancel)
        if process.returncode:
            raise RuntimeError('运行环境安装失败：\n' + "\n".join(tail)[-5000:])
        return "\n".join(tail)
    finally:
        cleanup_error = None
        try:
            _stop_worker(process)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            cleanup_error = exc
        thread.join(timeout=.5)
        if cleanup_error is not None:
            raise cleanup_error
        if thread.is_alive():
            raise RuntimeError('安装进程管道未正常关闭，环境安装未完成，请更换新目录重试。')


def create_runtime(base_python, target, *, device="cuda:0", language="Japanese",
                   cancel=lambda: False, progress=lambda p, m: None):
    check_cancel(cancel)
    base = Path(base_python).expanduser().resolve()
    target = _plain_path(target)
    if not base.is_file() or base.name.lower() not in ("python.exe", "python"):
        raise ValueError('请选择本机已安装的 Python 3.12（python.exe）。')
    if target.exists():
        raise ValueError('目标文件夹已存在，请指定一个新的空目录名来创建运行环境。')
    if device not in TORCH_INDEXES:
        raise ValueError("请选择 CPU 或 CUDA。")
    if not target.parent.is_dir():
        raise ValueError('所选上级目录不存在，请先创建上级文件夹后再试。')
    if shutil.disk_usage(target.parent).free < MIN_FREE_BYTES:
        raise ValueError('目标磁盘剩余空间不足 12 GB，请清理空间或更换安装磁盘后再试。')
    version = _run([str(base), "-I", "-c",
        "import sys,json;print(json.dumps({'version':list(sys.version_info[:2]),'bits':64 if sys.maxsize>2**32 else 32}))"],
        cancel, progress, 1, timeout=15)
    try:
        info = json.loads(version.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ValueError('无法读取所选 Python 的版本信息，请确认所选 python.exe 可正常运行。') from exc
    if info != {"version": [3, 12], "bits": 64}:
        raise ValueError('本地模型环境需要 64 位 Python 3.12，请更换对应版本的 Python。')
    target.mkdir()  # exclusively owned new directory; never reuse or recursively delete.
    marker = target / "LYRIA_INSTALL_INCOMPLETE"
    marker.write_text("Not ready. Explicitly select another new folder to retry.\n", encoding="utf-8")
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PIP_") or key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_CACHE_DIR="1", PYTHONNOUSERSITE="1",
               HF_HUB_DISABLE_TELEMETRY="1")
    progress(3, '正在创建独立 Python 运行环境…')
    _run([str(base), "-I", "-m", "venv", str(target)], cancel, progress, 5, env, timeout=180)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    common = [str(python), "-I", "-m", "pip", "--isolated", "install",
              "--disable-pip-version-check", "--no-input", "--only-binary=:all:", "--no-cache-dir",
              "--timeout", str(PIP_TIMEOUT_SECONDS), "--retries", str(PIP_RETRIES)]
    progress(15, '正在下载并安装 PyTorch（体积较大，请耐心等待）…')
    _run(common + ["--index-url", TORCH_INDEXES[device], "torch==" + TORCH_VERSION],
         cancel, progress, 20, env)
    progress(45, '正在安装模型运行依赖包…')
    _run(common + ["--index-url", "https://pypi.org/simple", *DEPENDENCIES,
                    ("onnxruntime-gpu==" if device == "cuda:0" else "onnxruntime==") + ORT_VERSION],
         cancel, progress, 55, env)
    _run([str(python), "-I", "-m", "pip", "check"], cancel, progress, 80, env, timeout=60)
    # Do not mark ready before an actual dependency/device probe.
    result = probe_runtime(str(python), device, language, cancel=cancel,
                           progress=lambda p, m: progress(85 + int(p * .1), m), allow_incomplete=True)
    freeze = _run([str(python), "-I", "-m", "pip", "freeze"], cancel, progress, 95, env, timeout=60)
    (target / "lyria-runtime.json").write_text(json.dumps({
        "schema": 1, "torch_index": TORCH_INDEXES[device], "requirements": list(DEPENDENCIES),
        "runtime": result["runtime"], "installation": "official registries, binary only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    # A full audit freeze is generated separately, not the bounded UI tail above.
    with (target / "lyria-installed-packages.txt").open("w", encoding="utf-8") as output:
        subprocess.run([str(python), "-I", "-m", "pip", "freeze"], stdout=output,
                       stderr=subprocess.DEVNULL, check=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env)
    check_cancel(cancel)
    marker.unlink()
    progress(100, '运行环境安装完成，接下来请下载或导入所需模型')
    return {"runtime": result["runtime"], "runtime_python": str(python)}




def probe_reading_tool(runtime_python, *, tool='pykakasi', allow_incomplete=False, cancel=lambda: False, progress=lambda p, m: None):
    """Actually import/convert in the selected isolated interpreter; no GPU needed."""
    from .bundled_reading import BUNDLED_RUNTIME
    if runtime_python == BUNDLED_RUNTIME:
        from .local_alignment_runtime import run_worker
        return run_worker({'operation':'reading_probe','reading_tool':tool}, BUNDLED_RUNTIME, cancel=cancel, progress=progress, timeout=90)
    from .local_alignment_runtime import validate_runtime_path
    python = validate_runtime_path(runtime_python, allow_incomplete=allow_incomplete)
    from .shared_runtime import discover_shared_runtime, verify_shared_runtime
    try:shared=discover_shared_runtime()
    except (OSError,ValueError):shared=None
    if shared is not None and python==shared:
        verify_shared_runtime(python,cancel=cancel,progress=progress)

    code = ("import json; from importlib.metadata import version; import pykakasi; "
            "import sys; from pathlib import Path; assert Path(pykakasi.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); assert version('pykakasi')=='2.3.0'; "
            "rows=pykakasi.kakasi().convert('桜'); "
            "assert rows and rows[0].get('hepburn'); "
            "print(json.dumps({'tool':'pykakasi','version':version('pykakasi'),'ready':True}))")
    if tool=='sudachi':
        code=("import json; from importlib.metadata import version; import sudachipy; "
              "import sys,sudachidict_small; from pathlib import Path; assert all(Path(m.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()) for m in (sudachipy,sudachidict_small)); "
              "assert version('sudachipy')=='0.6.11' and version('sudachidict_small')=='20260116'; "
              "t=sudachipy.Dictionary(dict='small').create(); rows=t.tokenize('一人',sudachipy.SplitMode.A); "
              "assert rows and rows[0].reading_form(); "
              "print(json.dumps({'tool':'sudachi','version':version('sudachipy'),"
              "'dictionary_version':version('sudachidict_small'),'license':'Apache-2.0','ready':True}))")
    elif tool!='pykakasi':raise ValueError('未知日语读音工具。')
    raw = _run([str(python), '-I', '-B', '-c', code], cancel, progress, 90, timeout=30)
    try:
        result = json.loads(raw.splitlines()[-1])
        if result.get('tool') != tool or result.get('ready') is not True:
            raise ValueError('invalid probe')
    except (ValueError, IndexError, AttributeError) as exc:
        raise RuntimeError('未能确认所选环境的 '+tool+' 可用。') from exc
    return {'reading_tool': result, 'reading_runtime_python': str(python)}


def install_reading_tool(runtime_python, *, tool='pykakasi', allow_incomplete=False, cancel=lambda: False, progress=lambda p, m: None):
    """Explicit, narrow repair of pykakasi in the user-selected isolated venv."""
    from .bundled_reading import BUNDLED_RUNTIME
    if runtime_python == BUNDLED_RUNTIME:
        raise ValueError('Bundled dictionaries are updated with Lyria; pip installation into the application is not allowed')
    from .local_alignment_runtime import validate_runtime_path
    python = validate_runtime_path(runtime_python, allow_incomplete=allow_incomplete)
    from .shared_runtime import discover_shared_runtime
    try:shared=discover_shared_runtime()
    except (OSError,ValueError):shared=None
    if shared is not None and python==shared:
        raise ValueError('Shared runtime is immutable; use a new external environment for package changes')

    env = dict(os.environ)
    for key in list(env):
        if key.startswith('PIP_') or key in ('PYTHONPATH', 'PYTHONHOME'):
            env.pop(key, None)
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_CACHE_DIR='1', PYTHONNOUSERSITE='1')
    packages={'pykakasi':['pykakasi==2.3.0'],'sudachi':['sudachipy==0.6.11','sudachidict_small==20260116']}.get(tool)
    if packages is None:raise ValueError('未知日语读音工具。')
    _run([str(python), '-I', '-m', 'pip', '--isolated', 'install',
          '--disable-pip-version-check', '--no-input', '--only-binary=:all:',
          '--no-cache-dir', '--timeout', str(PIP_TIMEOUT_SECONDS), '--retries', str(PIP_RETRIES),
          '--index-url', 'https://pypi.org/simple', *packages],
         cancel, progress, 30, env, timeout=600)
    _run([str(python), '-I', '-m', 'pip', 'check'], cancel, progress, 80, env, timeout=60)
    result = probe_reading_tool(str(python), tool=tool, allow_incomplete=allow_incomplete, cancel=cancel, progress=progress)
    progress(100, '所选环境的日语注音工具已安装并通过实际转换检查。')
    return result


def create_reading_runtime(base_python, target, *, tool='sudachi', cancel=lambda: False, progress=lambda p,m:None):
    """Create a new reading-only venv; never alter the acoustic model environment."""
    check_cancel(cancel)
    if tool not in ('sudachi','pykakasi'):raise ValueError('Unknown reading tool')
    base=Path(base_python).expanduser().resolve();target=_plain_path(target)
    if not base.is_file() or base.name.lower() not in ('python.exe','python'):
        raise ValueError('Choose an existing Python 3.12 interpreter')
    if target.exists() or not target.parent.is_dir():raise ValueError('Choose a new directory under an existing parent')
    if shutil.disk_usage(target.parent).free<1024**3:raise ValueError('Reading environment requires 1 GB free disk space')
    info=json.loads(_run([str(base),'-I','-c','import json,sys;print(json.dumps([list(sys.version_info[:2]),sys.maxsize>2**32]))'],cancel,progress,1,timeout=15).splitlines()[-1])
    if info!=[[3,12],True]:raise ValueError('Reading environment requires 64-bit Python 3.12')
    target.mkdir();marker=target/'LYRIA_INSTALL_INCOMPLETE';marker.write_text('Reading environment incomplete; retry in another new directory.',encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.startswith('PIP_') and k not in ('PYTHONPATH','PYTHONHOME')}
    env.update(PIP_CONFIG_FILE=os.devnull,PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    _run([str(base),'-I','-m','venv',str(target)],cancel,progress,5,env,timeout=180)
    python=target/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    result=install_reading_tool(str(python),tool=tool,allow_incomplete=True,cancel=cancel,progress=progress)
    (target/'lyria-reading-runtime.json').write_text(json.dumps(dict(schema=1,tool=tool,probe=result,installation='official PyPI binary wheels only; no acoustic dependencies'),ensure_ascii=False,indent=2),encoding='utf-8')
    check_cancel(cancel);marker.unlink()
    return result
