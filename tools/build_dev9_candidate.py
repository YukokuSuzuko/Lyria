"""Build an isolated Windows dev9 test candidate from a clean committed checkout.

Copies frozen tracked files and required resources; never changes an existing
package or the shared Python environment. No upload, publishing or user storage.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,os,shutil,subprocess,sys,time,zipfile

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()

def write(path,data):
    Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    evidence=output/'evidence';evidence.mkdir()
    source=output/'source';source.mkdir()
    def git(*arguments):return subprocess.check_output(['git',*arguments],cwd=root,text=True).strip()
    if git('status','--porcelain'):raise RuntimeError('Commit the final source before packaging.')
    commit=git('rev-parse','HEAD')
    source_zip=output/'source-snapshot.zip'
    subprocess.run(['git','archive','--format=zip','--output='+str(source_zip),commit],cwd=root,check=True)
    with zipfile.ZipFile(source_zip) as z:
        if z.testzip() is not None:raise RuntimeError('Invalid source archive')
        if any(not (source/name).resolve().is_relative_to(source) for name in z.namelist()):
            raise RuntimeError('Invalid source archive path')
        z.extractall(source)
    shutil.copytree(root/'resources',source/'resources')
    resources=[]
    for f in sorted((root/'resources').rglob('*')):
        if f.is_file():
            rel=f.relative_to(root);before=sha(f);after=sha(source/rel)
            if before!=after:raise RuntimeError('Resource copy mismatch')
            resources.append({'path':rel.as_posix(),'sha256':before})
    write(evidence/'resource-copy.json',resources)
    write(evidence/'frozen-input.json',{'source_commit':commit,'source_zip_sha256':sha(source_zip)})
    commands=[]
    def run(name,arguments,env=None,timeout=900):
        start=time.monotonic()
        with (evidence/(name+'.log')).open('wb') as log:
            child=subprocess.Popen(list(map(str,arguments)),cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            rec={'name':name,'pid':child.pid,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
            commands.append(rec);write(evidence/'commands.json',commands)
            print(name,'START',child.pid,flush=True)
            try:rec['exit_code']=child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                rec['timeout_seconds']=timeout
                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,timeout=30)
                write(evidence/'commands.json',commands)
                raise
            rec['seconds']=round(time.monotonic()-start,3);write(evidence/'commands.json',commands)
        if rec['exit_code']:raise RuntimeError(name+' failed; see isolated evidence log')
        print(name,'PASS',rec['seconds'],flush=True)
    audit=source/'tools/audit_portable_runtime.py'
    for mode in ['source','prebuild']:
        run(mode,[sys.executable,'-B',audit,mode,'--root',source,'--report',evidence/(mode+'.json')])
    run('pyinstaller',[sys.executable,'-B','-m','PyInstaller','--noconfirm','--distpath',output/'dist','--workpath',output/'work',source/'LyriaQt.spec'],timeout=1200)
    package=output/'dist/Lyria'
    manifest=json.loads((source/'tools/portable_runtime_manifest.json').read_text())
    for name in manifest['external_trees']+manifest['external_files']:
        origin=source/name;target=package/name;target.parent.mkdir(parents=True,exist_ok=True)
        if origin.is_dir():shutil.copytree(origin,target)
        else:shutil.copy2(origin,target)
    shutil.copy2(source/'tools/portable_runtime_manifest.json',package)
    run('postbuild',[sys.executable,'-B',audit,'postbuild','--root',package,'--prebuild-report',evidence/'prebuild.json','--report',package/'portable-audit.json','--run-binaries'])
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QT_QPA_FONTDIR=r'C:\Windows\Fonts',
             PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    cases={
        'startup':['--video',package/'examples/synthetic_mv.mp4','--exit-after','5000','--screenshot',evidence/'startup.png','--self-check',evidence/'startup.json','--storage-dir',evidence/'startup-storage'],
        'foundation':['--foundation-self-check',evidence/'foundation/report.json'],
        'editing':['--editing-self-check',evidence/'editing/report.json']}
    smokes={}
    for name,arguments in cases.items():
        run(name,[package/'Lyria.exe',*arguments],env=env,timeout=120)
        report=json.loads((evidence/('startup.json' if name=='startup' else name+'/report.json')).read_text(encoding='utf-8'))
        if not report['ok']:raise RuntimeError(name+' selfcheck failed')
        smokes[name]={'ok':True,'checks':len(report.get('checks',[]))}
    # Ensure resources and loose external-model workers are exactly the frozen inputs.
    for row in resources:
        if sha(package/'_internal'/row['path'])!=row['sha256']:raise RuntimeError('Packaged resource mismatch')
    for rel in manifest['internal_files']:
        if sha(package/'_internal'/rel)!=sha(source/rel):raise RuntimeError('Loose worker mismatch')
    sys.path.insert(0,str(source))
    from lyricsync import __version__
    # Read the real PE strings, not the archive's name.
    import pefile
    pe=pefile.PE(str(package/'Lyria.exe'))
    strings={}
    for group in pe.FileInfo:
        for item in group:
            if hasattr(item,'StringTable'):
                for table in item.StringTable:
                    strings.update({k.decode():v.decode() for k,v in table.entries.items()})
    if strings.get('ProductVersion')!=__version__ or strings.get('FileVersion')!=__version__:
        raise RuntimeError('Windows version resource mismatch')
    metadata={'app_version':__version__,'source_commit':commit,'exe_sha256':sha(package/'Lyria.exe'),
              'resource_files_verified':len(resources),'smokes':smokes,'publication':False,
              'foreground_acceptance':False,'source_snapshot_sha256':sha(source_zip),
              'pe_version':strings.get('ProductVersion'),'documentation_finalized':False}
    write(package/'build-metadata.json',metadata);write(evidence/'build-result.json',metadata)
    print(json.dumps(metadata,indent=2),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
