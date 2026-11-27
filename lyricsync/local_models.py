"""Explicit, pinned model acquisition. Importing this module performs no I/O.

Only the named official safetensors/ONNX models with fixed hashes are accepted. Cache state is
independent of projects and of the GUI Python environment.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import uuid
import time
import socket
import threading
from collections import deque

class ModelError(RuntimeError):
    pass

@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str

@dataclass(frozen=True)
class ModelSpec:
    id: str
    repo: str
    revision: str
    license: str
    files: tuple[ModelFile, ...]
    homepage: str = ""
    license_page: str = ""

    @property
    def size(self):
        return sum(item.size for item in self.files)

    @property
    def source_url(self):
        return self.homepage or f"https://huggingface.co/{self.repo}/tree/{self.revision}"

    @property
    def license_url(self):
        return self.license_page or f"https://huggingface.co/{self.repo}/blob/{self.revision}/README.md"

QWEN = ModelSpec(
    "qwen3-fa-hf", "Qwen/Qwen3-ForcedAligner-0.6B-hf",
    "c07281df297b9905d24a508279258cccf987a064", "Apache-2.0", (
        ModelFile("README.md", 9419, "30de8315ece609b239c719539336b99188a2a92aef89a1fdcd60b11e40abd063"),
        ModelFile("chat_template.jinja", 551, "59b5ae119bda7c00089bba5f49af775791ea67c7aeb7d2b828cddda9463121bf"),
        ModelFile("config.json", 248029, "82ac390b36a8f80c5a0c4e202367debd149e5d8c30abe4cf5eed439101b1c5ce"),
        ModelFile("model.safetensors", 1835545960, "00568245ceca5af1991d28562a75fe1ddc9bfeb041c27fda66947ea05c47fb86"),
        ModelFile("processor_config.json", 487, "bc0b230081b44e629dd5b9045b78495615c1831b4b9f4cffe97bd37e82a6156a"),
        ModelFile("tokenizer.json", 11429842, "cadfb9cbd9ff8f4e309075ec6a71b063295ff796afb4b5dbc0fd2873fbb301f2"),
        ModelFile("tokenizer_config.json", 998, "945e980986de2ca7768f3326bfdbb4fbea3406f972b8ae0be233089f2b253c11"),
    ))
UVR = ModelSpec("uvr-mdx-inst-hq3", "TRvlvr/model_repo", "asset-112310332-sha317554b0",
    "MIT (UVR official model-use statement)", (
    ModelFile("UVR-MDX-NET-Inst_HQ_3.onnx", 66759214, "317554b07fe1ea5279a77f2b1520a41ea4b93432560c4ffd08792c30fddf9adc"),),
    "https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models",
    "https://github.com/Anjok07/ultimatevocalremovergui/blob/a5f88453bfb2b38b05a965bcf67727243e0cbf19/README.md#license")
# Official UVR release asset 101350754, fetched and SHA-256 checked 2026-11-13.
# DSP metadata: upstream a5f88453, tail-10240000 MD5 291c2049608edb52648b96e27eb80e95.
UVR_HQ1 = ModelSpec("uvr-mdx-inst-hq1", "TRvlvr/model_repo", "asset-101350754-sha38a045c4",
    UVR.license, (
    ModelFile("UVR-MDX-NET-Inst_HQ_1.onnx", 66759214, "38a045c4ded87e3bf97b609ec5be7910e8a7cecec455f507227ab12b5e29f7f9"),),
    UVR.homepage, UVR.license_page)
SEPARATION_MODELS = {model.id: model for model in (UVR, UVR_HQ1)}
SEPARATION_PROFILES = {
    UVR.id: {"compensation": 1.022, "adapter": "lyria-mdx-hq3-ola-v1"},
    UVR_HQ1.id: {"compensation": 1.035, "adapter": "lyria-mdx-hq1-ola-v1"},
}


def separation_spec(model_id=UVR.id):
    try:
        return SEPARATION_MODELS[model_id]
    except (KeyError, TypeError) as exc:
        raise ModelError('请选择受支持的人声分离模型；不会自动切换或下载备用模型。') from exc


ASR = ModelSpec("qwen3-asr-hf", "Qwen/Qwen3-ASR-0.6B-hf",
    "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c", "Apache-2.0", (
    ModelFile('README.md', 16538, '742006d0f99ce6475b1c5e7b66e4a2f166cce836dd7ce3d7f20707252859195a'),
    ModelFile('chat_template.jinja', 1434, 'f50e6b694fbf4a683206e37869990d68333fe95d285730f084c838a34b0d98c2'),
    ModelFile('config.json', 2398, '9eecf6f1b383e343889c2e6010e632590fa57d4bc678e151c7d6a160a0dfb04a'),
    ModelFile('generation_config.json', 165, '9939fc9388b79bd70757f938b87381e817173d6a6158f5af6506c0b73e775c3c'),
    ModelFile('model.safetensors', 1564928088, 'd3f212dd20abecd315d830bc54ae3865e56ebfc3276484e57b771288ba27fd35'),
    ModelFile('processor_config.json', 487, 'bc0b230081b44e629dd5b9045b78495615c1831b4b9f4cffe97bd37e82a6156a'),
    ModelFile('tokenizer.json', 11429653, 'fe1fad59be22a41ee293363fcf95fdedbc7c93f3b49270b1d2e18bd1399a7a05'),
    ModelFile('tokenizer_config.json', 998, '945e980986de2ca7768f3326bfdbb4fbea3406f972b8ae0be233089f2b253c11'),
))
NEXTFIRE = ModelSpec("nextfire-ja-latn", 'NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn',
    '2ab2b5f46539ee284703c281f286b01d2410ee12', "CC-BY-NC-SA-4.0", (
    ModelFile('README.md', 597, '3a23befdfce500c6da47026854b547698166f11d8567670344959d0fb4c1208d'),
    ModelFile('added_tokens.json', 30, 'ad6e3518e1ac75c1f277c3af874f8d73e62efe8bfb9ed78ff1745e6cffdd8318'),
    ModelFile('config.json', 2034, '665dbd5be3a170d0b53f86e702a4f65f755471a2d81c1e0cf35ba566a72bf382'),
    ModelFile('model.safetensors', 1261938632, '4d1d6fbb543534ab32bcceac5f5a42052c7fde3d16a83cbbce858473d5735042'),
    ModelFile('processor_config.json', 299, '8bc3c8d6d97ea61524d5c15d8554b4baf072d2264c0bda1839bc7a822c200d66'),
    ModelFile('tokenizer_config.json', 1364, 'b96525cd12d87182b9edd1613b47bc691f64184ad9d37c8a2f8b44a5fb3b8043'),
    ModelFile('vocab.json', 331, '26dc928520b2bde28e1c9e7fd425aae6b4eb40cd992bcb0bcc16ca6fada46491'),
) )
ALIGNMENT_MODELS = {model.id: model for model in (QWEN, NEXTFIRE)}
CATALOG = {model.id:model for model in (QWEN, UVR, UVR_HQ1, ASR, NEXTFIRE)}
# Evaluated experimental choices retain explicit limitations and separate licenses.
EVALUATED_MODELS = ({
    "repo": "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn",
    "license": "CC-BY-NC-SA-4.0",
    "enabled": True,
    "reason": "Explicit Japanese-only experimental choice; editable readings and review of forced-path candidates required. Code license does not change noncommercial weight terms.",
    "source": "https://huggingface.co/NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn",
},)
MODEL_SCOPE_REVISION = "053506a54ad1c9deea6105050330d7c500b9f098"
DOWNLOAD_SOURCES = {
    "mirror": {"label": "国内镜像源（推荐 · HF-Mirror / 魔搭 / GitHub 加速）", "revision": QWEN.revision,
               "page": "https://hf-mirror.com"},
    "official": {"label": "官方直连源（Hugging Face / GitHub）", "revision": QWEN.revision,
                 "page": "https://huggingface.co"},
    "huggingface": {"label": "Hugging Face · 官方 Qwen", "revision": QWEN.revision,
                   "page": QWEN.source_url},
    "modelscope": {"label": "ModelScope · 官方 Qwen", "revision": MODEL_SCOPE_REVISION,
                  "page": "https://modelscope.cn/models/Qwen/Qwen3-ForcedAligner-0.6B-hf"},
}
DOWNLOAD_SOURCES["uvr"] = {"label": 'GitHub · 官方 UVR', "revision": UVR.revision, "page": UVR.source_url}
BLOCK = 1024 * 1024
DOWNLOAD_READ_TIMEOUT = 30

def _source(source):
    try:
        return DOWNLOAD_SOURCES[source]
    except (KeyError, TypeError) as exc:
        raise ModelError('请选择软件内置的官方下载源或国内镜像源，或使用离线导入。') from exc

def download_urls(spec, item, source="mirror"):
    """Build ordered pinned candidate URLs for explicit source mode."""
    _source(source)
    if item not in spec.files:
        raise ModelError('模型文件不在所需清单中。')
    quoted = urllib.parse.quote(item.name, safe='')
    gh_url = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/" + item.name
    hf_url = f"https://huggingface.co/{spec.repo}/resolve/{spec.revision}/{quoted}"
    hf_mirror_url = f"https://hf-mirror.com/{spec.repo}/resolve/{spec.revision}/{quoted}"
    if source == "mirror":
        if spec.id in SEPARATION_MODELS:
            return [
                "https://gh-proxy.com/" + gh_url,
                "https://ghfast.top/" + gh_url,
                gh_url,
            ]
        urls = [hf_mirror_url]
        if spec.id == QWEN.id and spec.repo == QWEN.repo and spec.revision == QWEN.revision:
            urls.append(f"https://modelscope.cn/models/{spec.repo}/resolve/{MODEL_SCOPE_REVISION}/{quoted}")
        urls.append(hf_url)
        return urls
    if source == "official":
        if spec.id in SEPARATION_MODELS:
            return [gh_url]
        return [hf_url]
    if spec.id in SEPARATION_MODELS:
        if source != "uvr":
            raise ModelError('人声分离模型（UVR）仅支持从 GitHub 官方源或国内镜像源下载或离线导入。')
        return [gh_url]
    if source == "uvr":
        raise ModelError('所选模型不属于 UVR 官方发布模型。')
    if source == "huggingface":
        return [hf_url]
    if spec.id != QWEN.id or spec.repo != QWEN.repo or spec.revision != QWEN.revision:
        raise ModelError('该模型暂不支持从魔搭（ModelScope）下载，请改用国内镜像源、Hugging Face 或离线导入。')
    return [f"https://modelscope.cn/models/{spec.repo}/resolve/{MODEL_SCOPE_REVISION}/{quoted}"]


def download_url(spec, item, source="huggingface"):
    return download_urls(spec, item, source)[0]


def default_cache():
    preset=os.environ.get("LYRIA_SPLIT_MODEL_CACHE")
    if preset and Path(preset).is_absolute():return Path(preset)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "Lyria" / "models-v1"

def check_cancel(cancel):
    if cancel():
        raise InterruptedError('已取消本地模型任务。')

def _plain_path(path):
    """Reject symlinks/junctions rather than follow a model cache elsewhere."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ModelError('模型目录不支持符号链接或映射目录，请选择普通本地文件夹。')
    return path

def _spec(model_id):
    try:
        return CATALOG[model_id]
    except KeyError as exc:
        raise ModelError('不支持加载该模型文件。') from exc

def _hash_file(path, item, cancel=lambda: False):
    path = _plain_path(path)
    if not path.is_file() or path.stat().st_size != item.size:
        raise ModelError(f"模型文件缺失或大小不符：{item.name}")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(BLOCK):
            check_cancel(cancel)
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ModelError(f'校验期间模型文件发生了变动：{item.name}')
    if digest.hexdigest() != item.sha256:
        raise ModelError(f'模型文件校验未通过（可能已损坏）：{item.name}，请重新下载或导入。')

def _trusted_url(url, source="huggingface"):
    _source(source)
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    uvr_hosts = ("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com")
    hf_trusted = (host == "huggingface.co" or host.endswith(".huggingface.co") or
                  host == "hf.co" or host.endswith(".hf.co"))
    ms_trusted = host == "modelscope.cn" or host.endswith(".modelscope.cn")
    if source == "uvr":
        return host in uvr_hosts
    if source == "modelscope":
        # Deliberately do not allow all aliyuncs.com (shared tenants). A newly
        # observed CDN host must be reviewed before a release expands this list.
        return ms_trusted
    if source == "official":
        return hf_trusted or host in uvr_hosts
    if source == "mirror":
        return (host in uvr_hosts or hf_trusted or ms_trusted or
                host == "hf-mirror.com" or host.endswith(".hf-mirror.com") or
                host == "gh-proxy.com" or host.endswith(".gh-proxy.com") or
                host == "ghfast.top" or host.endswith(".ghfast.top"))
    return hf_trusted

class _TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, source="huggingface"):
        super().__init__()
        _source(source)
        self.source = source

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl, self.source):
            raise ModelError('下载链接跳转到了非官方域名，已停止下载；请切换下载源或使用离线导入。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def _change_stamp(path, stat):
    """Windows st_ctime is creation time; use NTFS ChangeTime for cache reuse.

    If the OS/filesystem cannot supply it, fail closed to full hashing rather
    than trusting a timestamp that can be preserved by an ordinary copy tool.
    """
    if os.name != "nt":
        return stat.st_ctime_ns
    import ctypes
    from ctypes import wintypes
    class BasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
                    ("CreationTime", "LastAccessTime", "LastWriteTime", "ChangeTime")] + [
                    ("FileAttributes", wintypes.DWORD)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    create = api.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    query = api.GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    close = api.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80, 7, None, 3, 0, None)  # READ_ATTRIBUTES, share R/W/delete
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot query model file change stamp")
    try:
        info = BasicInfo()
        if not query(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "Cannot query model file change stamp")
        return info.ChangeTime
    finally:
        close(handle)


class ModelManager:
    def __init__(self, root=None):
        self.root = _plain_path(root or default_cache())

    def path(self, model_id=QWEN.id):
        spec = _spec(model_id)
        return _plain_path(self.root / spec.id / spec.revision)

    def file_problem(self, model_id, details=""):
        spec = _spec(model_id)
        name = spec.files[0].name if model_id in SEPARATION_MODELS else spec.repo
        hint = "也可取消「先分离人声」继续打轴。" if model_id in SEPARATION_MODELS else ""
        return f"{name} 模型文件缺失或不完整。目录：{self.path(model_id)}。{details}请在「模型与环境」下载或本地导入后校验。{hint}"

    def _snapshot(self, spec):
        path = self.path(spec.id)
        names = {item.name for item in spec.files}
        if not path.is_dir():
            raise ModelError(self.file_problem(spec.id))
        actual_names = {entry.name for entry in path.iterdir()}
        if actual_names - names:
            raise ModelError('模型目录中包含多余文件，请清理目录或选择干净文件夹重新导入。')
        if names - actual_names:
            raise ModelError(self.file_problem(spec.id, "缺少 " + "、".join(sorted(names - actual_names)) + "。"))
        files = []
        for item in spec.files:
            target = _plain_path(path / item.name)
            stat = target.stat()
            if not target.is_file() or stat.st_size != item.size:
                raise ModelError(self.file_problem(spec.id, f"{item.name} 大小不符。"))
            try:
                changed = _change_stamp(target, stat)
            except OSError:
                changed = None
            files.append([item.name, item.size, item.sha256, stat.st_dev, stat.st_ino,
                          stat.st_mtime_ns, changed])
        return {"version": 1, "path": str(path), "model_id": spec.id,
                "revision": spec.revision, "files": files}

    def _receipt_path(self, model_id):
        spec = _spec(model_id)
        return _plain_path(self.root / '.verification-v1' / (spec.id + '.json'))

    def _record_verified(self, spec, snapshot):
        # This is a local performance receipt, NOT a signature or security boundary.
        # Only fixed bundled hashes authorize downloaded/imported model content.
        receipt = self._receipt_path(spec.id)
        try:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            temporary = receipt.with_name(receipt.name + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, receipt)
        except OSError:
            # Read-only caches remain usable; they will be fully verified next run.
            pass

    def invalidate(self, model_id=QWEN.id):
        try:
            self._receipt_path(model_id).unlink(missing_ok=True)
        except OSError:
            pass

    def _receipt_matches(self, spec):
        try:
            receipt = self._receipt_path(spec.id)
            if receipt.stat().st_size > 65536:
                return False
            snapshot = self._snapshot(spec)
            if any(row[-1] is None for row in snapshot['files']):
                return False
            with receipt.open('r', encoding='utf-8') as stream:
                cached = json.loads(stream.read(65537))
            return cached == snapshot
        except (OSError, ValueError, ModelError):
            return False

    def ensure_verified(self, model_id=QWEN.id, *, cancel=lambda: False, progress=lambda p, m: None):
        """Reuse a prior SHA check only for unchanged local file identities.

        Not tamper-proof against a local actor who can alter models AND receipts.
        Explicit verify() always hashes all bytes. Runtime load failures invalidate.
        """
        check_cancel(cancel)
        spec = _spec(model_id)
        if self._receipt_matches(spec):
            progress(100, '模型文件未变动，沿用已完成的完整校验；运行效果仍需实际检查')
            return self.path(model_id)
        return self.verify(model_id, cancel=cancel, progress=progress)

    def status(self, model_id=QWEN.id):
        """Cheap presence only. Never mistake this for integrity verification."""
        spec = _spec(model_id)
        target = self.path(model_id)
        missing = [f.name for f in spec.files if not (target / f.name).is_file()]
        wrong_size = [f.name for f in spec.files if (target / f.name).is_file()
                      and (target / f.name).stat().st_size != f.size]
        present = not missing and not wrong_size
        return {"present": present, "verified": False, "verification_cached": present and self._receipt_matches(spec), "missing": missing, "wrong_size": wrong_size, "path": str(target),
                "bytes": spec.size, "license": spec.license, "revision": spec.revision}

    def verify(self, model_id=QWEN.id, *, cancel=lambda: False, progress=lambda p, m: None):
        spec = _spec(model_id)
        path = self.path(model_id)
        self.invalidate(model_id)
        before = self._snapshot(spec)
        for index, item in enumerate(spec.files):
            check_cancel(cancel)
            progress(round(100 * index / len(spec.files)), f'正在校验模型文件：{item.name}')
            _hash_file(path / item.name, item, cancel)
        # A local directory is not an arbitrary model plugin. No extra executable files.
        unexpected = [p.name for p in path.iterdir() if p.name not in {f.name for f in spec.files}]
        if unexpected:
            raise ModelError('模型目录中包含多余文件，请清理目录或选择干净文件夹重新导入。')
        if before != self._snapshot(spec):
            raise ModelError('校验期间模型文件发生了变动，请重新校验。')
        self._record_verified(spec, before)
        progress(100, '模型文件完整性校验通过')
        return path

    @contextmanager
    def _lock(self, spec):
        _plain_path(self.root).mkdir(parents=True, exist_ok=True)
        lock = _plain_path(self.root / (spec.id + ".lock"))
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ModelError('该模型正在下载或校验中；若上次异常退出导致卡住，可手动删除模型目录中的 .lock 文件后重试。') from exc
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink(missing_ok=True)

    def _publish(self, staging, spec, cancel):
        for item in spec.files:
            _hash_file(staging / item.name, item, cancel)
        check_cancel(cancel)
        target = self.path(spec.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # Preserve corrupt/previous content for inspection, never delete it.
            target.rename(target.with_name(target.name + ".replaced-" + uuid.uuid4().hex))
        staging.rename(target)
        self._record_verified(spec, self._snapshot(spec))
        return target

    def import_folder(self, folder, model_id=QWEN.id, *, cancel=lambda: False, progress=lambda p, m: None):
        """Copy only allowlisted files; supplied manifests cannot alter the pin."""
        spec = _spec(model_id)
        source = _plain_path(folder)
        if source == self.path(model_id):
            return self.verify(model_id, cancel=cancel, progress=progress)
        with self._lock(spec):
            staging = _plain_path(self.root / (".import-" + uuid.uuid4().hex))
            staging.mkdir()
            try:
                copied = 0
                for item in spec.files:
                    _hash_file(source / item.name, item, cancel)
                    with (source / item.name).open("rb") as src, (staging / item.name).open("xb") as dst:
                        while chunk := src.read(BLOCK):
                            check_cancel(cancel)
                            dst.write(chunk)
                            copied += len(chunk)
                            progress(min(99, round(copied * 100 / spec.size)), f'正在导入模型文件：{item.name}')
                result = self._publish(staging, spec, cancel)
                progress(100, '模型导入并校验完成')
                return result
            finally:
                if staging.exists():
                    for file in staging.iterdir():
                        file.unlink()
                    staging.rmdir()

    def download(self, model_id=QWEN.id, *, source="huggingface", cancel=lambda: False, progress=lambda p, m: None, opener=None):
        """Explicit only. Resume pinned files; incomplete data is never a model."""
        spec = _spec(model_id)
        source_info = _source(source)
        # Validate a ModelScope model mapping before creating a cache or network client.
        download_url(spec, spec.files[0], source)
        check_cancel(cancel)
        opener = opener or urllib.request.build_opener(_TrustedRedirect(source))
        with self._lock(spec):
            suffix = "" if source in ("huggingface", "official") else "-" + source
            staging = _plain_path(self.root / (".download-" + spec.id + "-" + spec.revision + suffix))
            staging.mkdir(exist_ok=True)
            total = 0
            meter = DownloadMeter(spec.size)
            for item in spec.files:
                check_cancel(cancel)
                target = _plain_path(staging / item.name)
                part = _plain_path(staging / (item.name + ".part"))
                if target.exists():
                    _hash_file(target, item, cancel)
                    total += item.size
                    continue
                candidates = download_urls(spec, item, source)
                last_exc = None
                for candidate_index, url in enumerate(candidates):
                    offset = part.stat().st_size if part.exists() else 0
                    if offset > item.size:
                        part.unlink()
                        offset = 0
                    if offset < item.size:
                        headers = {"User-Agent": "Lyria-local-model-manager/1", "Accept-Encoding": "identity"}
                        if offset:
                            headers["Range"] = f"bytes={offset}-"
                        try:
                            response = opener.open(urllib.request.Request(url, headers=headers), timeout=DOWNLOAD_READ_TIMEOUT)
                            with response, _cancel_response_read(response, cancel):
                                if not _trusted_url(response.geturl(), source):
                                    raise ModelError('下载连接非可信 HTTPS 地址，已停止下载。')
                                status = response.getcode()
                                if offset and status == 206:
                                    value = response.headers.get("Content-Range", "")
                                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value)
                                    if (not match or int(match[1]) != offset or int(match[3]) != item.size
                                            or not offset <= int(match[2]) < item.size):
                                        raise ModelError('服务器断点续传响应异常，请重试下载。')
                                elif status == 200:
                                    offset = 0  # Server ignored Range: restart, never append duplicate bytes.
                                else:
                                    raise ModelError(f"模型服务器返回异常状态：{status}")
                                meter.mark(total + offset)
                                # Incremental reads return even during a continuous slow drip.
                                read = getattr(response, "read1", None) or response.read
                                last_progress = 0.
                                with part.open("ab" if offset else "wb") as output:
                                    while True:
                                        check_cancel(cancel)
                                        chunk = read(BLOCK)
                                        check_cancel(cancel)
                                        if not chunk:
                                            break
                                        offset += len(chunk)
                                        if offset > item.size:
                                            raise ModelError('下载文件大小超出预期，已停止下载。')
                                        output.write(chunk)
                                        now = time.monotonic()
                                        if now - last_progress >= .15 or offset == item.size:
                                            progress(min(99, round((total + offset) * 100 / spec.size)), f"{source_info['label']} | {item.name}\n" + meter.update(total + offset))
                                            last_progress = now
                        except (OSError, urllib.error.URLError, ModelError) as exc:
                            if isinstance(exc, InterruptedError):
                                raise
                            check_cancel(cancel)
                            last_exc = exc
                            if isinstance(exc, ModelError) and '206' in str(exc):
                                raise
                            if candidate_index + 1 < len(candidates):
                                progress(min(99, round((total + offset) * 100 / spec.size)), f"{source_info['label']} 当前节点连接受阻，自动切换备用线路…")
                                continue
                            raise ModelError(f"{source_info['label']} 下载中断，已保留下载进度，可点击重试、切换下载源或离线导入。") from exc
                    try:
                        _hash_file(part, item, cancel)
                        last_exc = None
                        break
                    except ModelError as exc:
                        # Wrong bytes must not be resumed forever.
                        part.unlink(missing_ok=True)
                        last_exc = exc
                        if candidate_index + 1 < len(candidates):
                            continue
                        raise
                if last_exc is not None:
                    raise last_exc
                part.rename(target)
                total += item.size
            result = self._publish(staging, spec, cancel)
            progress(100, '模型下载并校验完成')
            return result



@contextmanager
def _cancel_response_read(response, cancel):
    """Cancel only our response socket. Connect/header phases use OS timeouts."""
    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is None:
        yield
        return
    done = threading.Event()
    def watch():
        while not done.wait(.05):
            if cancel():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                # Windows shutdown does not reliably wake a stalled timed read.
                # Detach prevents a later close from touching a reused descriptor.
                try:
                    socket.close(sock.detach())
                except (OSError, ValueError):
                    pass
                return
    monitor = threading.Thread(target=watch, name="Lyria-model-read-cancel", daemon=True)
    monitor.start()
    try:
        yield
    finally:
        done.set()
        monitor.join()


def readable_bytes(value):
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} GB"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} MB"
    if value >= 1000:
        return f"{value / 1000:.1f} KB"
    return f"{value:.0f} B"


class DownloadMeter:
    """Measure only newly received bytes, never restored partial file bytes."""
    def __init__(self, total, clock=time.monotonic):
        self.total, self.clock = total, clock
        self.samples = deque()
    def mark(self, done):
        self.samples.clear()
        self.samples.append((self.clock(), done))
    def update(self, done):
        now = self.clock()
        if not self.samples or done < self.samples[-1][1]:
            self.mark(done)
        self.samples.append((now, done))
        while len(self.samples) > 2 and self.samples[1][0] < now - 8:
            self.samples.popleft()
        elapsed = now - self.samples[0][0]
        received = done - self.samples[0][1]
        speed = received / elapsed if elapsed >= .5 and received > 0 else None
        rate = readable_bytes(speed) + "/s" if speed else "未知"
        eta = "未知"
        if speed and elapsed >= 2:
            seconds = max(0, round((self.total - done) / speed))
            eta = f"{seconds // 60} 分 {seconds % 60} 秒" if seconds >= 60 else f"{seconds} 秒"
        return f"已下载 {readable_bytes(done)} / {readable_bytes(self.total)} · 速度 {rate} · 预计剩余 {eta}"
