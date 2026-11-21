"""Fail-closed PyInstaller collection of the separately prepared reading bundle."""
import hashlib,json
from pathlib import Path
from lyricsync.bundled_reading import MANIFEST_SHA256

def collect_reading_bundle(path):
    if not path:raise RuntimeError('Set LYRIA_READING_BUNDLE to the verified prepared Sudachi bundle')
    root=Path(path).resolve();raw=(root/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA256:raise RuntimeError('Reading bundle manifest mismatch')
    files=json.loads(raw)['files'];datas=[];binaries=[]
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='manifest.json'}
    if actual!=set(files):raise RuntimeError('Reading bundle unexpected/missing file')
    for name,info in files.items():
        p=(root/name).resolve()
        if not p.is_relative_to(root):raise RuntimeError('Unsafe bundle path')
        with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        if p.stat().st_size!=info['bytes'] or digest!=info['sha256']:raise RuntimeError('Reading bundle file mismatch: '+name)
        pair=(str(p),str(Path('reading_bundle')/Path(name).parent))
        (binaries if p.suffix=='.pyd' else datas).append(pair)
    datas.append((str(root/'manifest.json'),'reading_bundle'))
    return datas,binaries
