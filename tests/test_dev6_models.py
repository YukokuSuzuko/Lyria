"""Dev6 pinned backup separation and layered validation; no network."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import pytest
from lyricsync import local_models as lm

@pytest.fixture
def model(tmp_path, monkeypatch):
    data = b"verified-model-content"
    spec = replace(lm.UVR_HQ1, files=(lm.ModelFile("unit.onnx", len(data), hashlib.sha256(data).hexdigest()),))
    monkeypatch.setitem(lm.CATALOG, spec.id, spec)
    manager = lm.ModelManager(tmp_path / "cache")
    path = manager.path(spec.id)
    path.mkdir(parents=True)
    (path / "unit.onnx").write_bytes(data)
    return manager, spec, path

def test_second_model_is_separator_not_aligner():
    assert set(lm.SEPARATION_MODELS) == {lm.UVR.id, lm.UVR_HQ1.id}
    assert lm.UVR_HQ1.size == 66759214
    assert lm.UVR_HQ1.files[0].sha256 == "38a045c4ded87e3bf97b609ec5be7910e8a7cecec455f507227ab12b5e29f7f9"
    assert lm.SEPARATION_PROFILES[lm.UVR_HQ1.id]["compensation"] == 1.035
    assert lm.UVR_HQ1.files[0].name in lm.download_url(lm.UVR_HQ1, lm.UVR_HQ1.files[0], "uvr")
    with pytest.raises(lm.ModelError): lm.separation_spec(lm.QWEN.id)
    with pytest.raises(lm.ModelError): lm.download_url(lm.UVR_HQ1, lm.UVR_HQ1.files[0], "huggingface")

def test_first_use_full_then_reuse_across_manager_instances(model, monkeypatch):
    manager, spec, path = model
    assert not manager.status(spec.id)["verification_cached"]
    manager.ensure_verified(spec.id)
    assert manager.status(spec.id)["verification_cached"]
    monkeypatch.setattr(lm, "_hash_file", lambda *a: pytest.fail("unchanged model rehashed"))
    assert lm.ModelManager(manager.root).ensure_verified(spec.id) == path
    assert manager.status(spec.id)["verified"] is False  # cheap check never claims new full verification

@pytest.mark.parametrize("change", ["same_size", "truncate", "extra", "missing", "receipt_corrupt", "receipt_missing", "pin_changed"])
def test_changed_or_untrusted_receipt_never_skips_full_check(model, monkeypatch, change):
    manager, spec, path = model
    manager.verify(spec.id)
    receipt = manager._receipt_path(spec.id)
    if change == "same_size": (path / "unit.onnx").write_bytes(b"x" * spec.size)
    elif change == "truncate": (path / "unit.onnx").write_bytes(b"x")
    elif change == "extra": (path / "evil.py").write_text("pass")
    elif change == "missing": (path / "unit.onnx").unlink()
    elif change == "receipt_corrupt": receipt.write_text("not json")
    elif change == "receipt_missing": receipt.unlink()
    else:
        monkeypatch.setitem(lm.CATALOG, spec.id, replace(spec, files=(replace(spec.files[0], sha256="f" * 64),)))
    called=[]
    def full(*a, **kw): called.append(True); raise lm.ModelError("full verification required")
    monkeypatch.setattr(manager, "verify", full)
    with pytest.raises(lm.ModelError, match="full verification required"): manager.ensure_verified(spec.id)
    assert called

def test_manual_verification_always_rehashes_and_invalidates_failure(model, monkeypatch):
    manager, spec, path = model
    manager.verify(spec.id)
    def bad(*a): raise lm.ModelError("bad hash")
    monkeypatch.setattr(lm, "_hash_file", bad)
    with pytest.raises(lm.ModelError, match="bad hash"): manager.verify(spec.id)
    assert not manager.status(spec.id)["verification_cached"]

def test_invalidate_and_cancel(model):
    manager, spec, path = model
    manager.verify(spec.id)
    with pytest.raises(InterruptedError): manager.ensure_verified(spec.id, cancel=lambda: True)
    manager.invalidate(spec.id)
    assert not manager.status(spec.id)["verification_cached"]

def test_second_model_ui_choice_roundtrip(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from lyricsync.project_document import ProjectDocument
    from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
    dialog = LocalAlignmentDialog(ProjectDocument(__import__("lyricsync.model", fromlist=["Project"]).Project()), setup_only=True,
        initial_options={"cache_root": str(tmp_path), "separation_model_id": lm.UVR_HQ1.id})
    assert dialog.separation_model.count() == 2
    assert dialog._raw_options()["separation_model_id"] == lm.UVR_HQ1.id
    dialog.separation_model.setCurrentIndex(0)
    assert dialog._raw_options()["separation_model_id"] == lm.UVR.id
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()

def test_saved_separator_preference_roundtrip(tmp_path):
    from desktop_qt import local_alignment_settings as prefs
    prefs.save(tmp_path, {"separation_model_id": lm.UVR_HQ1.id, "audio_path": "private", "text": "private"})
    assert prefs.load(tmp_path) == {"separation_model_id": lm.UVR_HQ1.id}

def test_same_size_edit_with_restored_mtime_is_detected(model):
    manager,spec,path=model
    manager.verify(spec.id)
    target=path/'unit.onnx'; before=target.stat()
    target.write_bytes(b'x'*spec.size)
    os.utime(target,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert not manager.status(spec.id)['verification_cached']
    with pytest.raises(lm.ModelError,match='校验未通过'):manager.ensure_verified(spec.id)

def test_no_change_stamp_means_no_cache_reuse(model,monkeypatch):
    manager,spec,path=model
    def unsupported(*a):raise OSError('unsupported file system')
    monkeypatch.setattr(lm,'_change_stamp',unsupported)
    assert manager.verify(spec.id)==path
    assert not manager.status(spec.id)['verification_cached']

def test_missing_selected_backup_does_not_run_default_or_download(tmp_path,monkeypatch):
    from lyricsync import local_separation as sep
    source=tmp_path/'audio';source.write_bytes(b'fixture')
    monkeypatch.setattr(lm.ModelManager,'download',lambda *a,**k:pytest.fail('implicit download'))
    monkeypatch.setattr(sep,'run_worker',lambda *a,**k:pytest.fail('implicit fallback'))
    with pytest.raises(lm.ModelError,match='模型文件缺失'):
        sep.prepare_vocals({'audio_path':str(source),'cache_root':str(tmp_path/'cache'),
            'separation_model_id':lm.UVR_HQ1.id})
