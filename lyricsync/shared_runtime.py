"""Pinned application-local acoustic runtime. No system-Python fallback.

Full checks occur off the GUI thread in run_worker. In-memory reuse requires an
unchanged whole-tree metadata digest; this is integrity checking, not a defense
against an attacker who can rewrite the application itself or spoof filesystem metadata.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import threading

MANIFEST = 'lyria-shared-runtime.json'
REFERENCE = 'shared-runtime-reference.json'
_CACHE = {}
_LOCK = threading.Lock()
_WORKER_ROOT = None


def application_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).absolute().parent
    here = Path(__file__).absolute()
    if here.parent.name == 'lyricsync' and here.parent.parent.name == '_internal':
        return here.parent.parent.parent
    return None


def _plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Shared runtime path contains a symlink or reparse point')
    return path


def _hash(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _json(path, limit):
    _plain(path)
    if path.stat().st_size > limit:
        raise ValueError('Shared runtime metadata is too large')
    def unique(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('Duplicate metadata key')
            value[key] = item
        return value
    return json.loads(path.read_text('utf-8'), object_pairs_hook=unique)


def _header(app=None):
    app = Path(app) if app is not None else application_dir()
    if app is None: return None
    app = _plain(app)
    ref_path = app / '_internal' / REFERENCE
    if not ref_path.exists(): return None
    ref = _json(ref_path, 4096)
    if (not isinstance(ref, dict) or ref.get('schema') != 1 or
            ref.get('runtime_relative') not in ('../shared-runtime', 'shared-runtime') or
            not re.fullmatch('[0-9a-f]{64}', str(ref.get('manifest_sha256', '')))):
        raise ValueError('Invalid application shared runtime reference')
    root = (app.parent if ref['runtime_relative'].startswith('../') else app) / 'shared-runtime'
    _plain(root)
    if (root / 'LYRIA_RUNTIME_INCOMPLETE').exists() or (root / 'pyvenv.cfg').exists():
        raise ValueError('Shared runtime is incomplete or not a standalone root')
    manifest_path = root / MANIFEST
    manifest = _json(manifest_path, 32 * 1024 * 1024)
    if _hash(manifest_path) != ref['manifest_sha256']:
        raise ValueError('Shared runtime manifest is not pinned by this application')
    if (not isinstance(manifest, dict) or manifest.get('schema') != 1 or
            manifest.get('python') != '3.12.15' or manifest.get('platform') != 'windows-amd64'):
        raise ValueError('Unsupported shared runtime identity')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not 5 <= len(entries) <= 100000:
        raise ValueError('Invalid shared runtime file inventory')
    files = {}
    for item in entries:
        if not isinstance(item, dict): raise ValueError('Invalid shared runtime file entry')
        name = item.get('path')
        if (not isinstance(name, str) or not name or len(name) > 1024 or
                '\\' in name or ':' in name or '\0' in name):
            raise ValueError('Invalid shared runtime relative path')
        rel = PurePosixPath(name)
        if rel.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')):
            raise ValueError('Shared runtime path escapes root')
        if any(x.endswith((' ', '.')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',x,re.I) for x in rel.parts):
            raise ValueError('Ambiguous Windows runtime path')
        key = name.casefold()
        if key in files or key == MANIFEST.casefold(): raise ValueError('Duplicate runtime path')
        if (type(item.get('bytes')) is not int or item['bytes'] < 0 or
                not re.fullmatch('[0-9a-f]{64}', str(item.get('sha256', '')))):
            raise ValueError('Invalid runtime file hash/size')
        files[key] = item
    required = {'python.exe', 'python3.dll', 'python312.dll', 'lib/os.py', 'lib/encodings/__init__.py'}
    if not required <= set(files): raise ValueError('Runtime lacks complete Python base')
    _plain(root / 'python.exe')
    return dict(root=root, manifest=manifest, files=files, identity=ref['manifest_sha256'])


def discover_shared_runtime(app=None):
    """Lightweight GUI discovery: identity only, NOT a ready/inference claim."""
    info = _header(app)
    return info['root'] / 'python.exe' if info else None


def _snapshot(info):
    root = _plain(info['root']); actual = {}; digest = hashlib.sha256()
    def entry(path):
        value=path.lstat()
        if stat.S_ISLNK(value.st_mode) or getattr(value,'st_file_attributes',0)&0x400:
            raise ValueError('Shared runtime path contains a symlink or reparse point')
        return value
    for parent, directories, names in os.walk(root, followlinks=False):
        for name in directories: entry(Path(parent)/name)
        for name in names:
            path=Path(parent)/name; value=entry(path)
            relative=path.relative_to(root).as_posix()
            if relative==MANIFEST:continue
            key=relative.casefold()
            if key not in info['files'] or key in actual:
                raise ValueError('Unexpected shared runtime file: '+relative)
            item=info['files'][key]
            if not stat.S_ISREG(value.st_mode) or value.st_size!=item['bytes']:
                raise ValueError('Shared runtime file size/type changed: '+relative)
            actual[key]=value
    if set(actual)!=set(info['files']):raise ValueError('Missing shared runtime files')
    for key,value in sorted(actual.items()):
        digest.update(json.dumps([key,value.st_size,value.st_mtime_ns,
                                  value.st_ctime_ns,value.st_ino],separators=(',',':')).encode())
    return digest.hexdigest()


def verify_shared_runtime(python, *, app=None, cancel=lambda:False, progress=lambda p,m:None):
    info = _header(app)
    if info is None or Path(python).absolute() != info['root'] / 'python.exe':
        raise ValueError('Python is not the application-pinned shared runtime')
    key = str(info['root']); signature = _snapshot(info)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached != (info['identity'], signature):
            _CACHE.pop(key, None)
            total = len(info['files'])
            for index, item in enumerate(info['files'].values()):
                if cancel(): raise InterruptedError('Shared runtime verification cancelled')
                if _hash(info['root'] / item['path']) != item['sha256']:
                    raise ValueError('Shared runtime file hash changed: ' + item['path'])
                if index % 500 == 0: progress(0, '正在后台校验随包模型运行环境 '+str(index)+'/'+str(total))
            if _snapshot(info) != signature:
                raise ValueError('Shared runtime changed during verification')
            _CACHE[key] = (info['identity'], signature)
    return dict(manifest_sha256=info['identity'], metadata_sha256=signature)


def verify_worker_runtime(attestation):
    """Validate each child against the parent's just-verified tree before imports."""
    global _WORKER_ROOT
    _WORKER_ROOT = None
    info = _header()
    if (info is None or Path(sys.executable).absolute() != info['root'] / 'python.exe' or
            Path(sys.prefix).absolute() != info['root'] or Path(sys.base_prefix).absolute() != info['root'] or
            tuple(sys.version_info[:3]) != (3,12,15)):
        raise ValueError('Worker is not running the pinned complete Python root')
    if (not isinstance(attestation, dict) or attestation.get('manifest_sha256') != info['identity'] or
            attestation.get('metadata_sha256') != _snapshot(info)):
        raise ValueError('Shared runtime changed between parent validation and worker launch')
    trusted_worker = Path(__file__).absolute().parent
    for entry in sys.path:
        location = Path(entry).absolute()
        if not location.is_relative_to(info['root']) and location != trusted_worker:
            raise ValueError('Shared runtime imported search paths outside its controlled root')
    for key in ('python.exe', 'python3.dll', 'python312.dll'):
        item = info['files'][key]
        if _hash(info['root'] / item['path']) != item['sha256']:
            raise ValueError('Shared runtime interpreter changed')
    _WORKER_ROOT = info['root']


def shared_worker_verified():
    return _WORKER_ROOT is not None and Path(sys.prefix).absolute() == _WORKER_ROOT
