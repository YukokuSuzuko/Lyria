"""Small synthetic files only. No model download or inference quality claim."""
import hashlib
import io
from pathlib import Path
import urllib.error
import pytest
from lyricsync import local_models as lm

@pytest.fixture
def catalog(monkeypatch):
    payloads = {"config.json": b'{"model_type":"test"}', "model.safetensors": b"test-weights-not-real"}
    spec = lm.ModelSpec("unit-model", "Official/Test", "a" * 40, "Test-only",
        tuple(lm.ModelFile(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in payloads.items()))
    monkeypatch.setitem(lm.CATALOG, spec.id, spec)
    return spec, payloads

def source_folder(tmp_path, payloads):
    folder = tmp_path / "source"
    folder.mkdir()
    for name, data in payloads.items():
        (folder / name).write_bytes(data)
    return folder

def test_import_verify_no_source_mutation(tmp_path, catalog):
    spec, payloads = catalog
    source = source_folder(tmp_path, payloads)
    (source / "untrusted.py").write_text("do not execute")
    manager = lm.ModelManager(tmp_path / "cache")
    path = manager.import_folder(source, spec.id)
    assert manager.verify(spec.id) == path
    assert manager.status(spec.id)["verified"] is False
    assert not (path / "untrusted.py").exists()
    assert (source / "untrusted.py").read_text() == "do not execute"

def test_import_tampered_rejected(tmp_path, catalog):
    spec, payloads = catalog
    source = source_folder(tmp_path, payloads)
    target = source / "model.safetensors"
    target.write_bytes(b"x" * target.stat().st_size)
    manager = lm.ModelManager(tmp_path / "cache")
    with pytest.raises(lm.ModelError, match="模型文件校验未通过"):
        manager.import_folder(source, spec.id)
    assert not manager.path(spec.id).exists()
    assert not list(manager.root.glob("*.lock"))

def test_existing_invalid_is_not_overwritten_on_failure(tmp_path, catalog):
    spec, payloads = catalog
    source = source_folder(tmp_path, payloads)
    manager = lm.ModelManager(tmp_path / "cache")
    path = manager.import_folder(source, spec.id)
    (path / "config.json").write_bytes(b"corrupt")
    with pytest.raises(lm.ModelError):
        manager.verify(spec.id)
    assert (path / "config.json").read_bytes() == b"corrupt"
    manager.import_folder(source, spec.id)
    assert manager.verify(spec.id)
    assert len(list(path.parent.glob("*.replaced-*"))) == 1

def test_no_download_on_status_verify_or_import_module(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(lm.urllib.request, "build_opener", lambda *a: pytest.fail("unrequested network"))
    spec, _ = catalog
    manager = lm.ModelManager(tmp_path / "cache")
    assert not manager.root.exists()
    assert manager.status(spec.id)["present"] is False
    with pytest.raises(lm.ModelError):
        manager.verify(spec.id)
    assert not manager.root.exists()

def test_cancel_import_preserves_source_cleans_stage(tmp_path, catalog):
    spec, payloads = catalog
    source = source_folder(tmp_path, payloads)
    manager = lm.ModelManager(tmp_path / "cache")
    with pytest.raises(InterruptedError):
        manager.import_folder(source, spec.id, cancel=lambda: True)
    assert (source / "config.json").read_bytes() == payloads["config.json"]
    assert not list(manager.root.glob(".import-*"))

class Reply(io.BytesIO):
    def __init__(self, data, status=200, headers=None, url="https://huggingface.co/Official/Test"):
        super().__init__(data)
        self.status, self.headers, self.url = status, headers or {}, url
    def getcode(self): return self.status
    def geturl(self): return self.url

class Opener:
    def __init__(self, payloads, mode="normal"):
        self.payloads, self.mode, self.requests = payloads, mode, []
    def open(self, request, timeout):
        self.requests.append(request)
        data = self.payloads[request.full_url.rsplit("/", 1)[1]]
        if self.mode == "offline":
            raise urllib.error.URLError("offline")
        if self.mode == "evil":
            return Reply(data, url="https://evil.invalid/model")
        if self.mode == "corrupt":
            return Reply(b"x" * len(data))
        offset = int(request.headers.get("Range", "bytes=0-")[6:-1])
        if offset and self.mode != "ignore-range":
            return Reply(data[offset:], 206, {"Content-Range": f"bytes {offset}-{len(data)-1}/{len(data)}"})
        return Reply(data)

@pytest.mark.parametrize("mode", ["normal", "ignore-range"])
def test_download_resumes_or_restarts_safely(tmp_path, catalog, mode):
    spec, payloads = catalog
    manager = lm.ModelManager(tmp_path / "cache")
    staging = manager.root / (".download-" + spec.id + "-" + spec.revision)
    staging.mkdir(parents=True)
    first = spec.files[0]
    (staging / (first.name + ".part")).write_bytes(payloads[first.name][:4])
    opener = Opener(payloads, mode)
    manager.download(spec.id, opener=opener)
    assert opener.requests[0].headers["Range"] == "bytes=4-"
    assert manager.verify(spec.id)
    assert not staging.exists()

@pytest.mark.parametrize("mode", ["offline", "evil", "corrupt"])
def test_download_failure_never_publishes(tmp_path, catalog, mode):
    spec, payloads = catalog
    manager = lm.ModelManager(tmp_path / "cache")
    with pytest.raises(lm.ModelError):
        manager.download(spec.id, opener=Opener(payloads, mode))
    assert not manager.path(spec.id).exists()
    assert not list(manager.root.glob("*.lock"))

def test_cancel_download_is_not_wrapped_as_network_error(tmp_path, catalog):
    spec, payloads = catalog
    manager = lm.ModelManager(tmp_path / "cache")
    state = [False]
    with pytest.raises(InterruptedError):
        manager.download(spec.id, opener=Opener(payloads),
                         cancel=lambda: state[0], progress=lambda *args: state.__setitem__(0, True))
    assert not manager.path(spec.id).exists()

def test_unknown_model_and_extra_files_rejected(tmp_path, catalog):
    with pytest.raises(lm.ModelError):
        lm.ModelManager(tmp_path).path("../arbitrary")
    spec, payloads = catalog
    source = source_folder(tmp_path, payloads)
    manager = lm.ModelManager(tmp_path / "cache")
    path = manager.import_folder(source, spec.id)
    (path / "danger.py").write_text("pass")
    with pytest.raises(lm.ModelError, match="模型目录中包含多余文件"):
        manager.verify(spec.id)

def test_fixed_official_manifest():
    assert lm.QWEN.repo == "Qwen/Qwen3-ForcedAligner-0.6B-hf"
    assert len(lm.QWEN.revision) == 40
    assert lm.QWEN.license == "Apache-2.0"
    assert all(len(f.sha256) == 64 for f in lm.QWEN.files)
    assert not any(f.name.endswith((".bin", ".py", ".pkl")) for f in lm.QWEN.files)
    assert 1_800_000_000 < lm.QWEN.size < 1_900_000_000


# ModelScope tests are fully synthetic; no request reaches a network socket.
@pytest.fixture
def modelscope_catalog(monkeypatch, catalog):
    from dataclasses import replace
    _, payloads = catalog
    fake_files = tuple(lm.ModelFile(name, len(data), hashlib.sha256(data).hexdigest())
                       for name, data in payloads.items())
    spec = replace(lm.QWEN, files=fake_files)
    monkeypatch.setitem(lm.CATALOG, spec.id, spec)
    return spec, payloads

class ModelScopeOpener(Opener):
    def open(self, request, timeout):
        response = super().open(request, timeout)
        if self.mode != "evil":
            response.url = request.full_url
        return response

def test_modelscope_pin_is_separate_and_shared_manifest_is_not_replaced():
    item = next(f for f in lm.QWEN.files if f.name == "tokenizer_config.json")
    url = lm.download_url(lm.QWEN, item, "modelscope")
    assert url == ("https://modelscope.cn/models/Qwen/Qwen3-ForcedAligner-0.6B-hf/resolve/"
                   "053506a54ad1c9deea6105050330d7c500b9f098/tokenizer_config.json")
    assert lm.QWEN.revision not in url
    assert lm.MODEL_SCOPE_REVISION not in lm.download_url(lm.QWEN, item)
    assert item.sha256 == "945e980986de2ca7768f3326bfdbb4fbea3406f972b8ae0be233089f2b253c11"

@pytest.mark.parametrize("mode", ["normal", "ignore-range"])
def test_modelscope_resumes_with_same_hash_gate(tmp_path, modelscope_catalog, mode):
    spec, payloads = modelscope_catalog
    manager = lm.ModelManager(tmp_path / "cache")
    staging = manager.root / (".download-" + spec.id + "-" + spec.revision + "-modelscope")
    staging.mkdir(parents=True)
    first = spec.files[0]
    (staging / (first.name + ".part")).write_bytes(payloads[first.name][:4])
    opener = ModelScopeOpener(payloads, mode)
    result = manager.download(spec.id, source="modelscope", opener=opener)
    assert opener.requests[0].headers["Range"] == "bytes=4-"
    assert all(lm.MODEL_SCOPE_REVISION in r.full_url for r in opener.requests)
    assert result == manager.path(spec.id) == manager.verify(spec.id)
    assert result.name == lm.QWEN.revision  # canonical model identity is source-independent
    assert not staging.exists()

def test_modelscope_failure_does_not_automatically_use_hf(tmp_path, modelscope_catalog):
    spec, payloads = modelscope_catalog
    opener = ModelScopeOpener(payloads, "offline")
    manager = lm.ModelManager(tmp_path / "cache")
    with pytest.raises(lm.ModelError, match="下载中断"):
        manager.download(source="modelscope", opener=opener)
    assert len(opener.requests) == 1
    assert opener.requests[0].full_url.startswith("https://modelscope.cn/")
    assert not manager.path().exists()

def test_modelscope_tampered_and_untrusted_redirect_never_publishes(tmp_path, modelscope_catalog):
    spec, payloads = modelscope_catalog
    for mode in ("corrupt", "evil"):
        manager = lm.ModelManager(tmp_path / mode)
        with pytest.raises(lm.ModelError):
            manager.download(source="modelscope", opener=ModelScopeOpener(payloads, mode))
        assert not manager.path().exists()

def test_modelscope_cancel_remains_cancel_and_uses_distinct_partial_folder(tmp_path, modelscope_catalog):
    spec, payloads = modelscope_catalog
    manager = lm.ModelManager(tmp_path / "cache")
    hf_staging = manager.root / (".download-" + spec.id + "-" + spec.revision)
    hf_staging.mkdir(parents=True)
    (hf_staging / "untouched.part").write_bytes(b"prior HF partial")
    state = [False]
    with pytest.raises(InterruptedError):
        manager.download(source="modelscope", opener=ModelScopeOpener(payloads),
            cancel=lambda: state[0], progress=lambda *args: state.__setitem__(0, True))
    assert (hf_staging / "untouched.part").read_bytes() == b"prior HF partial"
    assert not manager.path().exists()

@pytest.mark.parametrize("url", [
    "http://modelscope.cn/file", "https://modelscope.cn.evil.invalid/file",
    "https://user:secret@modelscope.cn/file", "https://modelscope.cn:444/file",
    "https://tenant.oss-cn-beijing.aliyuncs.com/file", "https://huggingface.co/file",
])
def test_modelscope_redirect_allowlist_rejects_shared_or_cross_source_hosts(url):
    assert lm._trusted_url(url, "modelscope") is False

def test_modelscope_redirect_allowlist_only_accepts_https_official_domain():
    assert lm._trusted_url("https://modelscope.cn/file", "modelscope")
    assert lm._trusted_url("https://cdn-lfs-cn-1.modelscope.cn/file", "modelscope")
    assert not lm._trusted_url("https://modelscope.cn/file", "huggingface")

def test_unknown_source_or_unverified_model_mapping_has_no_side_effects(tmp_path, catalog):
    spec, payloads = catalog
    manager = lm.ModelManager(tmp_path / "cache")
    opener = Opener(payloads)
    with pytest.raises(lm.ModelError):
        manager.download(spec.id, source="arbitrary", opener=opener)
    with pytest.raises(lm.ModelError, match="暂不支持从魔搭"):
        manager.download(spec.id, source="modelscope", opener=opener)
    assert not opener.requests and not manager.root.exists()

