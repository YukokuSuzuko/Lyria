from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import pytest
from lyricsync import shared_runtime as sr


def digest(data): return hashlib.sha256(data).hexdigest()


def setup(tmp_path):
    app=tmp_path/'中文 space'/'程序';(app/'_internal').mkdir(parents=True)
    root=app.parent/'shared-runtime';root.mkdir()
    names=['python.exe','python3.dll','python312.dll','Lib/os.py','Lib/encodings/__init__.py']
    entries=[]
    for name in names:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode())
        entries.append(dict(path=name,bytes=p.stat().st_size,sha256=digest(p.read_bytes()),owner='python-base'))
    manifest=dict(schema=1,python='3.12.15',platform='windows-amd64',files=entries)
    pin(app,root,manifest)
    return app,root,manifest


def pin(app,root,manifest,relative='../shared-runtime'):
    data=json.dumps(manifest).encode();(root/sr.MANIFEST).write_bytes(data)
    (app/'_internal'/sr.REFERENCE).write_text(json.dumps(dict(schema=1,runtime_relative=relative,manifest_sha256=digest(data))),encoding='utf8')


def test_discovery_is_lightweight_not_full_ready(tmp_path,monkeypatch):
    app,root,m=setup(tmp_path)
    monkeypatch.setattr(sr,'_snapshot',lambda *_:pytest.fail('GUI must not walk 5 GB tree'))
    assert sr.discover_shared_runtime(app)==root/'python.exe'


def test_full_verification_cache_and_same_size_change(tmp_path,monkeypatch):
    app,root,m=setup(tmp_path);seen=[];original=sr._hash
    monkeypatch.setattr(sr,'_hash',lambda p:(seen.append(p.name),original(p))[1])
    first=sr.verify_shared_runtime(root/'python.exe',app=app)
    assert 'os.py' in seen
    seen.clear();assert sr.verify_shared_runtime(root/'python.exe',app=app)==first
    assert seen==[sr.MANIFEST]
    p=root/'Lib/os.py';p.write_bytes(b'X'*p.stat().st_size)
    with pytest.raises(ValueError,match='hash changed'):sr.verify_shared_runtime(root/'python.exe',app=app)


@pytest.mark.parametrize('kind',['missing','extra','incomplete','unpin','empty','case_duplicate','escape','absolute','ads','reserved'])
def test_reject_broken_or_unsafe_bundle(tmp_path,kind):
    app,root,m=setup(tmp_path)
    if kind=='missing':(root/'python3.dll').unlink()
    elif kind=='extra':(root/'extra.py').write_text('')
    elif kind=='incomplete':(root/'LYRIA_RUNTIME_INCOMPLETE').write_text('')
    elif kind=='unpin':(root/sr.MANIFEST).write_text('{}')
    else:
        if kind=='empty':m['files']=[]
        else:
            entry=deepcopy(m['files'][0]);entry['path']={'case_duplicate':'PYTHON.EXE','escape':'../other.exe','absolute':'/root.exe','ads':'Lib/a:payload','reserved':'Lib/CON'}[kind]
            m['files'].append(entry)
        pin(app,root,m)
    with pytest.raises((ValueError,OSError)):sr.verify_shared_runtime(root/'python.exe',app=app)


def test_invalid_relative_reference_cannot_pick_system_python(tmp_path):
    app,root,m=setup(tmp_path);pin(app,root,m,relative='../../Windows')
    with pytest.raises(ValueError):sr.discover_shared_runtime(app)


def test_cancel_does_not_cache_success(tmp_path):
    app,root,m=setup(tmp_path)
    with pytest.raises(InterruptedError):sr.verify_shared_runtime(root/'python.exe',app=app,cancel=lambda:True)
    assert str(root) not in sr._CACHE


def test_symlink_is_rejected(tmp_path):
    app,root,m=setup(tmp_path);target=root/'Lib/os.py';target.unlink()
    outside=tmp_path/'outside.py';outside.write_bytes(b'Lib/os.py')
    try:target.symlink_to(outside)
    except OSError:pytest.skip('symlink permission unavailable')
    with pytest.raises(ValueError,match='reparse'):sr.verify_shared_runtime(root/'python.exe',app=app)


def test_worker_attestation_and_changed_tree(tmp_path,monkeypatch):
    app,root,m=setup(tmp_path)
    attestation=sr.verify_shared_runtime(root/'python.exe',app=app)
    monkeypatch.setattr(sr,'application_dir',lambda:app)
    monkeypatch.setattr(sr.sys,'executable',str(root/'python.exe'))
    monkeypatch.setattr(sr.sys,'prefix',str(root));monkeypatch.setattr(sr.sys,'base_prefix',str(root))
    monkeypatch.setattr(sr.sys,'version_info',(3,12,15))
    monkeypatch.setattr(sr.sys,'path',[str(root/'Lib'),str(Path(sr.__file__).absolute().parent)])
    sr.verify_worker_runtime(attestation);assert sr.shared_worker_verified()
    (root/'python.exe').write_bytes(b'X'*len('python.exe'))
    with pytest.raises(ValueError):sr.verify_worker_runtime(attestation)
    assert not sr.shared_worker_verified()


def test_unverified_global_worker_is_not_exception(monkeypatch):
    monkeypatch.setattr(sr,'_WORKER_ROOT',None)
    assert not sr.shared_worker_verified()


def test_existing_external_venv_stays_supported(tmp_path,monkeypatch):
    from lyricsync.local_alignment_runtime import validate_runtime_path
    app,root,m=setup(tmp_path);(root/sr.MANIFEST).write_text('{}')
    monkeypatch.setattr(sr,'application_dir',lambda:app)
    venv=tmp_path/'external';(venv/'Scripts').mkdir(parents=True)
    (venv/'Scripts/python.exe').write_bytes(b'fake');(venv/'pyvenv.cfg').write_text('include-system-site-packages = false')
    assert validate_runtime_path(str(venv/'Scripts/python.exe'))==venv/'Scripts/python.exe'



def test_reparse_attribute_is_rejected_without_needing_symlink_privilege(tmp_path,monkeypatch):
    from types import SimpleNamespace
    app,root,m=setup(tmp_path);original=Path.lstat
    def lstat(path,*a,**k):
        value=original(path,*a,**k)
        if path==root:return SimpleNamespace(st_mode=value.st_mode,st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path,'lstat',lstat)
    with pytest.raises(ValueError,match='reparse'):sr.discover_shared_runtime(app)


def test_system_python_still_rejected(tmp_path,monkeypatch):
    from lyricsync.local_alignment_runtime import validate_runtime_path
    monkeypatch.setattr(sr,'application_dir',lambda:None)
    system=tmp_path/'python.exe';system.write_bytes(b'fake')
    with pytest.raises(ValueError,match='pyvenv'):validate_runtime_path(str(system))


def test_pip_never_modifies_shared_tree(tmp_path,monkeypatch):
    from lyricsync import local_runtime_setup as setup_module
    app,root,m=setup(tmp_path)
    monkeypatch.setattr(sr,'application_dir',lambda:app)
    monkeypatch.setattr(setup_module,'_run',lambda *a,**k:pytest.fail('pip must not run'))
    with pytest.raises(ValueError,match='immutable'):
        setup_module.install_reading_tool(str(root/'python.exe'))


def test_worker_global_prefix_without_validation_is_rejected(monkeypatch):
    import lyricsync.local_alignment_worker as worker
    monkeypatch.setattr(sr,'_WORKER_ROOT',None)
    # Loose worker imports its own shared_runtime name; clear that independent module too.
    import shared_runtime as loose
    monkeypatch.setattr(loose,'_WORKER_ROOT',None)
    monkeypatch.setattr(worker.sys,'prefix',worker.sys.base_prefix)
    with pytest.raises(RuntimeError,match='系统全局'):
        worker.runtime_info('cpu')



def test_worker_rejects_external_pth_search_path(tmp_path,monkeypatch):
    app,root,m=setup(tmp_path);attestation=sr.verify_shared_runtime(root/'python.exe',app=app)
    monkeypatch.setattr(sr,'application_dir',lambda:app)
    monkeypatch.setattr(sr.sys,'executable',str(root/'python.exe'))
    monkeypatch.setattr(sr.sys,'prefix',str(root));monkeypatch.setattr(sr.sys,'base_prefix',str(root))
    monkeypatch.setattr(sr.sys,'version_info',(3,12,15))
    monkeypatch.setattr(sr.sys,'path',[str(tmp_path/'another-environment')])
    with pytest.raises(ValueError,match='search paths outside'):sr.verify_worker_runtime(attestation)
