"""Plan first; explicitly build one new shared runtime, never alter source envs.
Run with the existing build Python (packaging installed). No network/install.
"""
from __future__ import annotations
import argparse
import base64
import csv
import hashlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from packaging.requirements import Requirement
from packaging.markers import default_environment
from packaging.utils import canonicalize_name

ROOTS = ['torch', 'transformers', 'numpy', 'librosa', 'jinja2', 'nagisa',
         'soynlp', 'onnxruntime-gpu', 'soundfile', 'regex', 'pykakasi']
SKIP_PARTS = {'__pycache__', '.git'}
BASE_FILES = ['python.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll',
              'vcruntime140_1.dll', 'LICENSE.txt']


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def dump_new(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def safe_file(path, root):
    path = Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Escaping or linked source: '+str(path))
    for parent in [path, *path.parents]:
        if parent == root.parent: break
        if getattr(parent.stat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Reparse point not allowed: '+str(parent))
    if not path.is_file(): raise ValueError('Missing source: '+str(path))


def plan(base, env, output):
    base, env = Path(base).resolve(), Path(env).resolve()
    site = env/'Lib/site-packages'
    installed = {}
    for d in md.distributions(path=[str(site)]):
        name = canonicalize_name(d.metadata['Name'])
        if name in installed: raise ValueError('Duplicate distribution: '+name)
        installed[name] = d
    marker = default_environment()
    marker.update(python_version='3.12', python_full_version='3.12.15',
        os_name='nt', sys_platform='win32', platform_system='Windows',
        platform_machine='AMD64', implementation_name='cpython', extra='')
    chosen, pending = {}, [Requirement(x) for x in ROOTS]
    while pending:
        req = pending.pop()
        if req.marker and not req.marker.evaluate(marker): continue
        name = canonicalize_name(req.name)
        if name not in installed: raise ValueError('Missing dependency: '+str(req))
        d = installed[name]
        if req.specifier and d.version not in req.specifier:
            raise ValueError('Version mismatch: '+str(req)+' installed '+d.version)
        if req.extras: raise ValueError('Unresolved extras, must explicitly audit: '+str(req))
        if name in chosen: continue
        chosen[name] = d
        pending.extend(Requirement(x) for x in d.requires or [])
    entries, distributions, omissions = {}, [], []
    def add(source, target, owner, record_hash=None):
        if set(source.parts) & SKIP_PARTS or source.suffix in ('.pyc', '.pyo'):
            return
        if source.name == 'direct_url.json':
            omissions.append({'owner':owner,'path':target,'reason':'Local install provenance may reveal user paths; retained only in private plan source'})
            return
        if source.suffix == '.pth':
            omissions.append({'owner':owner,'path':target,'reason':'No executable .pth allowed in bundle'})
            return
        safe_file(source, base if owner=='python' else site)
        size = source.stat().st_size
        sha = digest(source)
        if record_hash:
            mode, encoded = record_hash.split('=', 1)
            if mode != 'sha256': raise ValueError('Unsupported RECORD digest: '+mode)
            want = base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)).hex()
            if sha != want: raise ValueError('Installed file differs from wheel RECORD: '+str(source))
        if target in entries and entries[target]['sha256'] != sha:
            raise ValueError('Conflicting package ownership: '+target)
        entries[target] = dict(source=str(source), path=target, bytes=size, sha256=sha, owner=owner)
    for name in BASE_FILES: add(base/name, name, 'python')
    for folder in ['Lib', 'DLLs', 'tcl']:
        for parent, dirs, files in os.walk(base/folder):
            dirs[:] = [x for x in dirs if x not in SKIP_PARTS and x != 'site-packages']
            for name in files:
                p = Path(parent)/name; add(p,p.relative_to(base).as_posix(),'python')
    for name, d in sorted(chosen.items()):
        notices = []
        record_text = d.read_text('RECORD')
        if not record_text: raise ValueError('Distribution lacks RECORD: '+name)
        for record_name, record_hash, record_size in csv.reader(record_text.splitlines()):
            rel = Path(record_name)
            if '..' in rel.parts or rel.is_absolute():
                if rel.is_absolute() or (rel.parts[:3] != ('..', '..', 'Scripts') and rel.parts != ('..', '..', 'share', 'man', 'man1', 'isympy.1')):
                    raise ValueError('Unreviewed out-of-site package record: '+str(rel))
                omissions.append({'owner':name,'path':str(rel),'reason':'External console launcher is not needed by worker'})
                continue
            source = site/rel
            target = 'Lib/site-packages/'+rel.as_posix()
            add(source,target,name,record_hash)
            if any(x in source.name.lower() for x in ('license','notice','copying','copyright')):
                notices.append(target)
        distributions.append(dict(name=name,version=d.version,
            requires=d.requires or [], license=d.metadata.get('License-Expression') or d.metadata.get('License'),
            notices=notices, metadata_path=str(d._path)))
    files = sorted(entries.values(),key=lambda x:x['path'])
    result = dict(schema=1, python='3.12.15', platform='windows-amd64',
        source_base=str(base), source_environment=str(env), roots=ROOTS,
        distributions=distributions, files=files, omissions=omissions,
        total_bytes=sum(x['bytes'] for x in files), distribution_license_review_complete=False,
        relocation_verified=False, clean_machine_verified=False)
    dump_new(output,result)
    print(json.dumps({'files':len(files),'bytes':result['total_bytes'],
        'packages':len(distributions),'plan':str(output)},ensure_ascii=False))


def build(plan_path, destination):
    p = json.loads(Path(plan_path).read_text('utf-8'))
    dest = Path(destination).resolve()
    if dest.exists(): raise ValueError('Destination must not exist')
    for root in [p['source_base'],p['source_environment']]:
        r=Path(root).resolve()
        if dest.is_relative_to(r) or r.is_relative_to(dest): raise ValueError('Source/destination overlap')
    if shutil.disk_usage(dest.parent).free < p['total_bytes'] + 2*1024**3:
        raise ValueError('Insufficient free space; leave 2 GiB safety margin')
    dest.mkdir()
    (dest/'LYRIA_RUNTIME_INCOMPLETE').write_text('Do not use incomplete runtime.\n',encoding='utf-8')
    manifest = {k:p[k] for k in ('schema','python','platform','roots','distribution_license_review_complete','relocation_verified','clean_machine_verified')}
    manifest['distributions']=[{k:d[k] for k in ('name','version','requires','license','notices')} for d in p['distributions']]
    manifest['files']=[]
    for item in p['files']:
        src=Path(item['source']); rel=Path(item['path']); out=dest/rel
        if rel.is_absolute() or '..' in rel.parts: raise ValueError('Invalid output path')
        if src.stat().st_size!=item['bytes'] or digest(src)!=item['sha256']:
            raise ValueError('Source changed after plan: '+str(src))
        out.parent.mkdir(parents=True,exist_ok=True)
        with src.open('rb') as inp, out.open('xb') as target: shutil.copyfileobj(inp,target,4*1024*1024)
        if digest(out)!=item['sha256']: raise ValueError('Copy mismatch: '+str(out))
        manifest['files'].append({k:item[k] for k in ('path','bytes','sha256','owner')})
    dump_new(dest/'lyria-shared-runtime.json',manifest)
    (dest/'LYRIA_RUNTIME_INCOMPLETE').unlink()
    print(json.dumps({'built':str(dest),'manifest_sha256':digest(dest/'lyria-shared-runtime.json')}))


def verify(root, report):
    root=Path(root).resolve()
    if (root/'LYRIA_RUNTIME_INCOMPLETE').exists(): raise ValueError('Incomplete runtime')
    m=json.loads((root/'lyria-shared-runtime.json').read_text('utf-8'))
    expected={x['path']:x for x in m['files']}
    for rel,item in expected.items():
        f=root/rel;safe_file(f,root)
        if f.stat().st_size!=item['bytes'] or digest(f)!=item['sha256']: raise ValueError('Hash mismatch: '+rel)
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    extras=actual-set(expected)-{'lyria-shared-runtime.json'}
    if extras: raise ValueError('Unexpected runtime files: '+str(sorted(extras)[:10]))
    # New child, restricted search paths; no model loading or internet.
    env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','PIP_','HF_','CUDA')) and k not in ('HUGGING_FACE_HUB_TOKEN',)}
    env.update(PATH=str(Path(os.environ['SystemRoot'])/'System32'),PYTHONDONTWRITEBYTECODE='1',
        HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    code="""import sys,json,importlib,importlib.metadata as md
from pathlib import Path
root=Path(sys.executable).resolve().parent
assert sys.version_info[:2]==(3,12) and Path(sys.prefix).resolve()==root
origins={}
for name in ['torch','numpy','transformers','librosa','onnxruntime','nagisa','soynlp','soundfile','pykakasi']:
 m=importlib.import_module(name);p=Path(m.__file__).resolve();assert p.is_relative_to(root),(name,str(p));origins[name]=str(p.relative_to(root))
import torch
assert (torch.ones(2,2)@torch.ones(2,2)).sum().item()==8
from transformers import AutoProcessor,AutoModelForCTC,AutoModelForTokenClassification,AutoModelForMultimodalLM
print(json.dumps({'python':sys.version,'prefix_is_bundle':True,'origins':origins,'cuda_available':torch.cuda.is_available(),'torch':torch.__version__}))
"""
    done=subprocess.run([str(root/'python.exe'),'-I','-B','-c',code],cwd=root.parent,env=env,
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    result={'hashes_verified':len(expected),'returncode':done.returncode,'stdout':done.stdout,
            'stderr':done.stderr,'manifest_sha256':digest(root/'lyria-shared-runtime.json'),
            'clean_machine_verified':False,'inference_verified':False}
    dump_new(report,result)
    if done.returncode: raise RuntimeError('Relocation import probe failed; inspect report')


def relocate(root, destination, report):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if root.parent != destination.parent or root == destination:
        raise ValueError('Relocation test only permits a new sibling directory')
    if destination.exists() or not (root/'lyria-shared-runtime.json').is_file():
        raise ValueError('Need owned complete runtime and unused destination')
    if (root/'LYRIA_RUNTIME_INCOMPLETE').exists(): raise ValueError('Incomplete source')
    # Rename only the explicit freshly built runtime, never source Python/venvs.
    root.rename(destination)
    verify(destination, report)


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('plan');p.add_argument('--base',required=True);p.add_argument('--env',required=True);p.add_argument('--out',required=True)
    p=sub.add_parser('build');p.add_argument('--plan',required=True);p.add_argument('--destination',required=True)
    p=sub.add_parser('verify');p.add_argument('--root',required=True);p.add_argument('--report',required=True)
    p=sub.add_parser('relocate');p.add_argument('--root',required=True);p.add_argument('--destination',required=True);p.add_argument('--report',required=True)
    a=parser.parse_args()
    if a.command=='plan':plan(a.base,a.env,a.out)
    elif a.command=='build':build(a.plan,a.destination)
    elif a.command=='verify':verify(a.root,a.report)
    else:relocate(a.root,a.destination,a.report)
if __name__=='__main__':main()
