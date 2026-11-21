import json
import pytest
from lyricsync import bundled_reading as bundle

def test_nonfrozen_never_claims_bundled_available(monkeypatch):
    monkeypatch.setattr(bundle.sys,'frozen',False,raising=False)
    assert not bundle.bundled_available()
    with pytest.raises(RuntimeError):bundle.bundle_root()

def test_manifest_tamper_rejected_before_import(tmp_path,monkeypatch):
    root=tmp_path/'reading_bundle';root.mkdir();(root/'manifest.json').write_text('{}')
    monkeypatch.setattr(bundle.sys,'frozen',True,raising=False)
    monkeypatch.setattr(bundle.sys,'_MEIPASS',str(tmp_path),raising=False)
    assert bundle.bundled_available() # presence is explicitly not readiness
    with pytest.raises(RuntimeError,match='Unrecognized'):bundle.verify_bundle()

def test_only_sudachi_dictionary_operations_allowed(monkeypatch):
    def forbidden():raise AssertionError('must not import bundle')
    monkeypatch.setattr(bundle,'initialize',forbidden)
    for request in ({'operation':'align'},{'operation':'readings','reading_tool':'pykakasi'}):
        with pytest.raises(ValueError):bundle.process(request)

def test_early_app_worker_dispatch_does_not_start_gui(monkeypatch):
    import app_qt
    calls=[]
    monkeypatch.setattr(bundle,'main',lambda args:calls.append(args) or 7)
    assert app_qt.main(['--reading-worker','--request','a','--result','b'])==7
    assert calls==[['--request','a','--result','b']]

def test_builtin_probe_uses_owned_worker_and_no_external_validation(monkeypatch):
    from lyricsync import local_alignment_runtime as runtime
    from lyricsync.local_runtime_setup import probe_reading_tool
    calls=[]
    monkeypatch.setattr(runtime,'run_worker',lambda request,path,**kwargs:calls.append((request,path)) or {'ok':True})
    assert probe_reading_tool(bundle.BUNDLED_RUNTIME,tool='sudachi')=={'ok':True}
    assert calls==[({'operation':'reading_probe','reading_tool':'sudachi'},bundle.BUNDLED_RUNTIME)]

def test_build_contract_missing_bundle_fails():
    from tools.reading_bundle_build import collect_reading_bundle
    with pytest.raises(RuntimeError):collect_reading_bundle('')

def test_cannot_pip_install_into_bundled_runtime():
    from lyricsync.local_runtime_setup import install_reading_tool
    with pytest.raises(ValueError,match='pip installation'):
        install_reading_tool(bundle.BUNDLED_RUNTIME,tool='sudachi')
