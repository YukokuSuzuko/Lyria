"""Finalize a verified dev9 candidate without overwriting any prior delivery."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,sys,zipfile

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--delivery-dir',type=Path,required=True)
    a=parser.parse_args();root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    def git(*v):return subprocess.check_output(['git',*v],cwd=root,text=True).strip()
    if git('status','--porcelain'):raise RuntimeError('Final checkout is not clean')
    build=a.build_dir.resolve();meta=json.loads((build/'evidence/build-result.json').read_text())
    current=git('rev-parse','HEAD')
    changed=git('diff','--name-only',meta['source_commit'],current).splitlines()
    if any(not (n.startswith('docs/') or n in ('README_QT.md','THIRD_PARTY.md','tools/finalize_dev9_candidate.py')) for n in changed):
        raise RuntimeError('Non-documentation changed after binary build: '+repr(changed))
    delivery=a.delivery_dir.resolve();delivery.mkdir(parents=True,exist_ok=False)
    from lyricsync import __version__
    name='Lyria_'+__version__+'_Windows_x64_test_20261115'
    package=delivery/name
    shutil.copytree(build/'dist/Lyria',package)
    if sha(package/'Lyria.exe')!=meta['exe_sha256']:raise RuntimeError('EXE copy mismatch')
    sourcezip=delivery/('Lyria_'+__version__+'_source_20261115.zip')
    subprocess.run(['git','archive','--format=zip','--output='+str(sourcezip),current],cwd=root,check=True)
    with zipfile.ZipFile(sourcezip) as z:
        if z.testzip() is not None:raise RuntimeError('Source CRC failure')
        final_source=build/('final-source-'+current[:8]);final_source.mkdir(exist_ok=False)
        if any(not (final_source/n).resolve().is_relative_to(final_source) for n in z.namelist()):
            raise RuntimeError('Unsafe source archive path')
        z.extractall(final_source)
    shutil.copytree(root/'resources',final_source/'resources')
    # A locally rebuildable source archive includes the audited portable tools.
    # Optional alignment/separation weights, environments and user inputs remain outside.
    with zipfile.ZipFile(sourcezip,'a',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for item in sorted((final_source/'resources').rglob('*')):
            if item.is_file():z.write(item,item.relative_to(final_source))
    with zipfile.ZipFile(sourcezip) as z:
        if z.testzip() is not None:raise RuntimeError('Complete source archive CRC failure')
    manifest=json.loads((final_source/'tools/portable_runtime_manifest.json').read_text())
    for rel in manifest['external_files']:
        target=package/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(final_source/rel,target)
    evidence=delivery/'verification';evidence.mkdir()
    for phase,cmd in [('prebuild',[sys.executable,'-B',final_source/'tools/audit_portable_runtime.py','prebuild','--root',final_source,'--report',evidence/'prebuild.json']),
                      ('postbuild',[sys.executable,'-B',final_source/'tools/audit_portable_runtime.py','postbuild','--root',package,'--prebuild-report',evidence/'prebuild.json','--report',package/'portable-audit.json','--run-binaries'])]:
        with (evidence/(phase+'.log')).open('wb') as log:
            subprocess.run(list(map(str,cmd)),cwd=root,check=True,timeout=180,stdout=log,stderr=subprocess.STDOUT)
    meta.update(documentation_finalized=True,documentation_commit=current,finalization_source_changes=changed,source_archive_sha256=sha(sourcezip))
    write(package/'build-metadata.json',meta)
    archive=delivery/(name+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for f in sorted(package.rglob('*')):
            if f.is_file():z.write(f,Path(name)/f.relative_to(package))
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise RuntimeError('Portable archive CRC failure')
        file_count=len(z.infolist())
    result={'version':__version__,'binary_commit':meta['source_commit'],'documentation_commit':current,
            'portable_archive':archive.name,'portable_sha256':sha(archive),'portable_bytes':archive.stat().st_size,
            'source_archive':sourcezip.name,'source_sha256':sha(sourcezip),'exe_sha256':meta['exe_sha256'],
            'files':file_count,'zip_crc_ok':True,'foreground_acceptance':False,'uploaded':False}
    write(delivery/'DELIVERY.json',result)
    (delivery/'SHA256SUMS.txt').write_text('\n'.join(sha(f)+'  '+f.name for f in (archive,sourcezip))+'\n',encoding='ascii')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
