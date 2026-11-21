import base64
import hashlib
import json
from pathlib import Path
import pytest
from tools import shared_runtime_bundle as bundle


def fixture(tmp_path, monkeypatch):
    base=tmp_path/'base'; base.mkdir()
    for name in bundle.BASE_FILES: (base/name).write_bytes(b'python fixture')
    (base/'Lib').mkdir();(base/'Lib/os.py').write_text('stdlib',encoding='utf-8')
    site=tmp_path/'env/Lib/site-packages';site.mkdir(parents=True)
    dist=site/'fixture-1.0.dist-info';dist.mkdir()
    (dist/'METADATA').write_text('Name: fixture\nVersion: 1.0\nLicense: MIT\n',encoding='utf-8')
    (site/'fixture.py').write_text('x = 1\n',encoding='utf-8')
    sha=base64.urlsafe_b64encode(hashlib.sha256((site/'fixture.py').read_bytes()).digest()).decode().rstrip('=')
    (dist/'RECORD').write_text('fixture.py,sha256='+sha+',6\nfixture-1.0.dist-info/METADATA,,\nfixture-1.0.dist-info/RECORD,,\n',encoding='utf-8')
    (site/'secret-unrelated.py').write_text('do not copy',encoding='utf-8')
    monkeypatch.setattr(bundle,'ROOTS',['fixture'])
    return base,site,dist


def test_plan_exact_record_closure_and_private_paths(tmp_path,monkeypatch):
    base,site,dist=fixture(tmp_path,monkeypatch)
    out=tmp_path/'plan.json';bundle.plan(base,site.parent.parent,out)
    plan=json.loads(out.read_text('utf-8'))
    assert len(plan['distributions'])==1
    assert not any('secret-unrelated' in f['path'] for f in plan['files'])
    dest=tmp_path/'new-runtime';bundle.build(out,dest)
    manifest=json.loads((dest/'lyria-shared-runtime.json').read_text('utf-8'))
    assert all('source' not in x for x in manifest['files'])
    assert str(tmp_path) not in json.dumps(manifest)
    assert not (dest/'pyvenv.cfg').exists()
    assert not (dest/'LYRIA_RUNTIME_INCOMPLETE').exists()
    with pytest.raises(ValueError):bundle.build(out,dest)


def test_modified_source_fails_before_bundle_completion(tmp_path,monkeypatch):
    base,site,dist=fixture(tmp_path,monkeypatch)
    out=tmp_path/'plan.json';bundle.plan(base,site.parent.parent,out)
    (site/'fixture.py').write_text('tampered',encoding='utf-8')
    with pytest.raises(ValueError,match='Source changed'):bundle.build(out,tmp_path/'new-runtime')
    assert (tmp_path/'new-runtime/LYRIA_RUNTIME_INCOMPLETE').exists()
    with pytest.raises(ValueError,match='differs from wheel RECORD'):bundle.plan(base,site.parent.parent,tmp_path/'other.json')


def test_unexpected_external_record_fails_closed(tmp_path,monkeypatch):
    base,site,dist=fixture(tmp_path,monkeypatch)
    with (dist/'RECORD').open('a',encoding='utf-8') as f:f.write('../../private-file.txt,,\n')
    with pytest.raises(ValueError,match='Unreviewed out-of-site'):bundle.plan(base,site.parent.parent,tmp_path/'plan.json')


def test_move_refuses_non_sibling_or_existing_target(tmp_path):
    with pytest.raises(ValueError):bundle.relocate(tmp_path/'a',tmp_path/'x/y',tmp_path/'report.json')
