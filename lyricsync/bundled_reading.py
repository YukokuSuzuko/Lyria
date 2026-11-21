"""Narrow offline dictionary subprocess; no acoustic model or external Python."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

BUNDLED_RUNTIME = '@bundled-reading'
MANIFEST_SHA256 = '32e22fcf40770d5894a6ece8601a4e10ac3f0df272c4cf2a9339cb965b4648d2'
VERSIONS = {'sudachipy':'0.6.11','sudachidict_small':'20260116'}

def bundle_root():
    if not getattr(sys,'frozen',False):
        raise RuntimeError('Bundled reading is available only in the packaged application')
    return Path(sys._MEIPASS).resolve() / 'reading_bundle'

def bundled_available():
    # Cheap presence gate only. The worker verifies every byte before import;
    # GUI must not hash 123 MB on its event loop or label presence as readiness.
    try:return (bundle_root()/'manifest.json').is_file()
    except (RuntimeError,AttributeError):return False

def verify_bundle():
    root=bundle_root()
    raw=(root/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA256:raise RuntimeError('Unrecognized bundled dictionary manifest')
    manifest=json.loads(raw.decode('utf-8'))
    if manifest.get('versions')!=VERSIONS or manifest.get('schema')!=1:
        raise RuntimeError('Bundled dictionary manifest/version mismatch')
    files=manifest.get('files',{})
    required={'sudachipy/__init__.py','sudachipy/sudachipy.cp312-win_amd64.pyd','sudachidict_small/__init__.py','sudachidict_small/resources/system.dic'}
    if not required.issubset(files):raise RuntimeError('Bundled dictionary manifest incomplete')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='manifest.json'}
    if actual!=set(files):raise RuntimeError('Unexpected or missing dictionary bundle file')
    for name,info in files.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or path.is_symlink():raise RuntimeError('Unsafe dictionary bundle path')
        if path.stat().st_size!=info['bytes']:raise RuntimeError('Bundled dictionary size mismatch')
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest!=info['sha256']:raise RuntimeError('Bundled dictionary hash mismatch: '+name)
    return root

def initialize():
    sys.dont_write_bytecode = True
    root=verify_bundle()
    # The manifest-backed directory is the only additional import location.
    sys.path.insert(0,str(root))
    from importlib.metadata import version
    import sudachipy,sudachidict_small
    for module in (sudachipy,sudachidict_small):
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise RuntimeError('Dictionary imported outside verified bundle')
    if any(version(name)!=expected for name,expected in VERSIONS.items()):
        raise RuntimeError('Dictionary metadata version mismatch')
    return root

def process(request):
    if not isinstance(request,dict) or request.get('reading_tool','sudachi')!='sudachi':
        raise ValueError('This bundle contains Sudachi only; pykakasi requires its separate runtime')
    operation=request.get('operation')
    if operation not in ('readings','reading_probe'):
        raise ValueError('Bundled worker accepts dictionary operations only')
    initialize()
    if operation=='reading_probe':
        from .sudachi_readings import create_tokenizer
        tokenizer,mode=create_tokenizer();rows=tokenizer.tokenize('一緒',mode)
        if not rows or not rows[0].reading_form():raise RuntimeError('Dictionary conversion probe failed')
        return {'version':1,'reading_runtime_python':BUNDLED_RUNTIME,'reading_tool':{'tool':'sudachi','version':'0.6.11','dictionary_version':'20260116','license':'Apache-2.0; see bundled LEGAL notices','ready':True,'bundled':True}}
    from .japanese_readings import prepare_reading_rows
    return {'version':1,'rows':prepare_reading_rows(request.get('rows'),tool='sudachi',reset=bool(request.get('reset_readings',False)))}

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--result',required=True)
    args=parser.parse_args(argv);incoming=Path(args.request);outgoing=Path(args.result)
    try:
        if incoming.stat().st_size>2*1024*1024:raise ValueError('Reading request too large')
        result=process(json.loads(incoming.read_text('utf-8')));code=0
    except Exception as exc:
        result={'version':1,'error':str(exc)};code=1
    temporary=outgoing.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8');os.replace(temporary,outgoing)
    return code
