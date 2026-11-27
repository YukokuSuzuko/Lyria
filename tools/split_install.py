"""Install an exact offline archive into a sibling profile. Does not touch editor, models or CPU files."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,re,shutil,stat,sys,tempfile,zipfile
BASE=Path(__file__).resolve().parent
def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def plain(p):
    for parent in (p,*p.parents):
        if parent.exists() and parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f'Reparse point: {parent}')
    return p
def relative(name):
    pieces=name.split('/')
    if not name or '\\' in name or ':' in name or any(x in ('','.','..') or x.endswith((' ','.')) or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',x,re.I) for x in pieces):
        raise ValueError('Invalid package path')
    return Path(*pieces)
def located(name):
    file=BASE/('location-'+name+'.json')
    if file.exists():
        path=Path(json.loads(plain(file).read_bytes())['directory'])
        if not path.is_absolute() or path.name!=name or path.parent.name!='Lyria-0.29-accelerators':raise ValueError('Invalid registered location')
        return plain(path.parent)
    return plain(BASE/'accelerators')
def install(path,directory=None):
    catalog=json.loads((BASE/'extension-catalog.json').read_bytes())
    sha=digest(path)
    name,config=next(((n,c) for n,c in catalog.items() if c['sha256']==sha),('',{}))
    if name not in ('directml','nvidia'):raise ValueError('未知扩展或 SHA-256 校验失败。')
    if (located(name)/name).exists():raise FileExistsError('扩展已经安装。')
    container=plain(Path(directory).resolve()/'Lyria-0.29-accelerators') if directory else located(name)
    container.mkdir(exist_ok=True)
    dest=plain(container/name)
    if dest.exists():raise FileExistsError('此扩展已安装。原有扩展保留。')
    with tempfile.TemporaryDirectory(dir=container,prefix='.install-') as tmp:
        stage=Path(tmp)/name
        stage.mkdir()
        with zipfile.ZipFile(path) as z:
            members={}
            for f in z.infolist():
                relative(f.filename)
                if f.filename.casefold() in members or stat.S_ISLNK(f.external_attr>>16) or f.is_dir():raise ValueError('Ambiguous archive member')
                members[f.filename.casefold()]=f
            m=config['manifest_path']; data=z.read(m)
            if hashlib.sha256(data).hexdigest()!=config['manifest_sha256']:raise ValueError('Manifest digest mismatch')
            doc=json.loads(data)
            entries=doc if name=='directml' else doc['files']
            seen={m.casefold()}
            for f in entries:
                rel=relative(f['path'])
                targetrel=rel if name=='directml' else Path('shared-runtime')/rel
                key=targetrel.as_posix().casefold()
                if key in seen:raise ValueError('Duplicate payload')
                seen.add(key)
                out=stage/targetrel; out.parent.mkdir(parents=True,exist_ok=True)
                if key in members:
                    member=members[key]
                    if member.file_size!=f['bytes']:raise ValueError('Archive size mismatch')
                    stream=z.open(member)
                elif name=='nvidia':
                    stream=plain(BASE/'shared-runtime'/rel).open('rb')
                else: raise ValueError('Incomplete payload')
                with stream,out.open('xb') as sink:shutil.copyfileobj(stream,sink,4<<20)
                if out.stat().st_size!=f['bytes'] or digest(out)!=f['sha256']:raise ValueError(f'File integrity failed: {rel}')
            if set(members)-seen:raise ValueError('Unexpected payload')
            manifest=stage/relative(m);manifest.parent.mkdir(parents=True,exist_ok=True);manifest.write_bytes(data)
        if dest.resolve().parent!=container.resolve() or stage.resolve().parent!=Path(tmp).resolve():raise ValueError('Unsafe target')
        stage.rename(dest)
        if directory:
            locator=BASE/('location-'+name+'.json')
            with tempfile.NamedTemporaryFile(mode='w',dir=BASE,encoding='utf8',delete=False) as registration:
                json.dump({'directory':str(dest)},registration)
            try:os.replace(registration.name,locator)
            except OSError:
                dest.rename(stage);Path(registration.name).unlink(missing_ok=True);raise
    print('扩展已安装，重启 Lyria 后自动检查设备。',name)
def uninstall(name):
    if name not in ('directml','nvidia'):raise ValueError('Unknown profile')
    container=located(name)
    src=plain(container/name)
    if src.resolve().parent!=container.resolve() or not src.is_dir():raise ValueError('Extension unavailable')
    catalog=json.loads((BASE/'extension-catalog.json').read_bytes())
    m=catalog[name]['manifest_path']; p=plain(src/m)
    if digest(p)!=catalog[name]['manifest_sha256']:raise ValueError('清单损坏，请关闭应用并隔离整个扩展目录。')
    doc=json.loads(p.read_bytes()); files=doc if name=='directml' else doc['files']
    allowed={m}|{(('shared-runtime/' if name=='nvidia' else '')+f['path']) for f in files}
    # Refuse to remove unknown user files. Model caches and projects live outside this directory.
    for path in src.rglob('*'):
        plain(path)
        if path.is_file() and path.relative_to(src).as_posix() not in allowed:raise ValueError('扩展目录含额外文件，已停止卸载。')
    with tempfile.TemporaryDirectory(dir=container,prefix='.removed-') as tmp:
        dest=Path(tmp)/name
        if dest.resolve().parent!=Path(tmp).resolve() or dest.resolve().parents[1]!=container.resolve():raise ValueError('Unsafe target')
        src.rename(dest)
    (BASE/('location-'+name+'.json')).unlink(missing_ok=True)
    print('扩展已卸载，重启后默认使用可用后端。',name)
if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--remove':uninstall(sys.argv[2])
    elif len(sys.argv)==4 and sys.argv[2]=='--directory':install(Path(sys.argv[1]),sys.argv[3])
    elif len(sys.argv)==2:install(Path(sys.argv[1]))
    else:
        from tkinter import Tk,filedialog
        root=Tk();root.withdraw()
        value=filedialog.askopenfilename(title='选择已下载的 Lyria 加速扩展',filetypes=[('Lyria 扩展','*.zip')])
        root.destroy()
        if value:install(Path(value))
