"""Fail-closed two-edition candidate assembly. `plan` never builds or installs.
No dependency install, source/resource copy, runtime copy, ZIP or publication.
"""
from __future__ import annotations
import argparse, ast, hashlib, importlib.util, json, os, shutil, subprocess, sys, time
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

LABELS = ('v0.23', 'v0.23 OCR修改')
GIB = 1024 ** 3


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def safe_relative(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name:
        raise ValueError('Invalid relative file name')
    p=PurePosixPath(name)
    if p.is_absolute() or any(x in ('','.','..') for x in name.split('/')):
        raise ValueError('Path escapes source')
    if any('private' in x.casefold() or x.casefold() in ('advice','audits','evidence') for x in p.parts):
        raise ValueError('Private material cannot be copied into delivery')
    return p


def git(root,*args):
    return subprocess.check_output(['git',*args],cwd=root,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def identity(root):
    root=Path(root).resolve()
    literals={}
    for node in ast.parse((root/'lyricsync/__init__.py').read_text('utf8')).body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            if node.targets[0].id in ('__version__','__edition__'):
                literals[node.targets[0].id]=ast.literal_eval(node.value)
    version=literals['__version__'];edition=literals['__edition__']
    return version+(' '+edition if edition else '')


def fingerprint(root):
    root=Path(root).resolve();digest=hashlib.sha256()
    paths=git(root,'ls-files','-z').decode('utf8').split('\0')
    for rel in sorted(filter(None,paths)):
        p=root/rel
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('Unsafe/missing tracked file')
        digest.update(rel.encode('utf8')+b'\0'+bytes.fromhex(sha(p)))
    return dict(commit=git(root,'rev-parse','HEAD').decode().strip(),
                tracked_sha256=digest.hexdigest(),clean=not bool(git(root,'status','--porcelain').strip()))


def validate_layout(sources,delivery,workspace,runtime):
    sources=[Path(x).resolve() for x in sources];delivery=Path(delivery).resolve();workspace=Path(workspace).resolve();runtime=Path(runtime).resolve()
    if sources[0]==sources[1] or any(a.is_relative_to(b) for a,b in ((sources[0],sources[1]),(sources[1],sources[0]))):raise ValueError('Edition sources must be distinct')
    if runtime!=delivery/'shared-runtime':raise ValueError('Runtime must be the one existing delivery/shared-runtime')
    if workspace.is_relative_to(delivery) or delivery.is_relative_to(workspace):raise ValueError('Private build workspace must be outside delivery')
    for source in sources:
        if any(a.is_relative_to(b) for a,b in ((workspace,source),(source,workspace),(delivery,source),(source,delivery))):raise ValueError('Build outputs overlap source')
    for label in LABELS:
        if (delivery/label).exists():raise ValueError('Refusing to overwrite existing edition '+label)
    return sources,delivery,workspace,runtime


def make_plan(sources,delivery,workspace,runtime,python,reading_bundle):
    sources,delivery,workspace,runtime=validate_layout(sources,delivery,workspace,runtime)
    python=Path(python).resolve();reading_bundle=Path(reading_bundle).resolve()
    if not python.is_file():raise ValueError('Existing build interpreter required; never install one')
    if workspace.exists():raise ValueError('Choose a fresh private build workspace')
    editions=[]
    for source,label in zip(sources,LABELS):
        if identity(source)!=label:raise ValueError('Source edition does not match output label: '+label)
        editions.append(dict(source=str(source),label=label,**fingerprint(source)))
    manifest=runtime/'lyria-shared-runtime.json'
    if not manifest.is_file():raise ValueError('Prepare the shared runtime separately first')
    required_free=11*GIB  # two <=2.5 GiB packages + two <=2 GiB work/cache dirs + 2 GiB reserve
    return dict(schema=1,editions=editions,delivery=str(delivery),workspace=str(workspace),runtime=str(runtime),
                runtime_manifest_sha256=sha(manifest),python=str(python),reading_bundle=str(reading_bundle),
                reading_manifest_sha256=sha(reading_bundle/'manifest.json'),
                incremental_budget_bytes=required_free,free_bytes=shutil.disk_usage(delivery).free,
                includes_zip=False,source_and_runtime_copied=False,legal_clear=False,publication=False)


def require_junit(path):
    root=ET.parse(path).getroot();suites=[root] if root.tag=='testsuite' else list(root.iter('testsuite'))
    if not suites or sum(int(x.get('tests','0')) for x in suites)<=0:raise RuntimeError('Missing/empty test coverage')
    if any(int(x.get('failures','0')) or int(x.get('errors','0')) for x in suites):raise RuntimeError('Tests failed; no bypass')
    return dict(tests=sum(int(x.get('tests','0')) for x in suites),skipped=sum(int(x.get('skipped','0')) for x in suites))


def copy_public(source,package,contract):
    tracked=set(filter(None,git(source,'ls-files','-z').decode('utf8').split('\0')))
    for name in contract['external_trees']+contract['external_files']:
        rel=safe_relative(name);origin=source/rel
        if not origin.resolve().is_relative_to(source):raise ValueError('Public material outside source')
        files=list(origin.rglob('*')) if origin.is_dir() else [origin]
        for p in files:
            if not p.is_file():continue
            relative=p.relative_to(source).as_posix();safe_relative(relative)
            if p.is_symlink() or relative not in tracked:raise ValueError('Untracked/private external material: '+relative)
            target=package/relative;target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():raise ValueError('Unexpected existing public-material destination')
            shutil.copyfile(p,target)


def run(command,cwd,log,env,timeout=1200):
    # All commands start with the explicit build Python. Own only their new tree.
    spec=importlib.util.spec_from_file_location('dual_build_owned',Path(cwd)/'lyricsync/owned_process.py')
    owned=importlib.util.module_from_spec(spec);spec.loader.exec_module(owned)
    with Path(log).open('wb') as stream:
        process=owned.launch_owned(list(map(str,command)),cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:code=process.wait(timeout=timeout)
        finally:
            job=getattr(process,'_lyria_job',None)
            if job is not None:job.stop()
            elif process.poll() is None:process.kill();process.wait(timeout=10)
    if code:raise RuntimeError('Command failed; see '+str(log))


def pytest_command(python,evidence):
    evidence=Path(evidence)
    return [python,'-B','-m','pytest','tests','-q','--capture=sys','-p','no:cacheprovider','-p','tools.pytest_journal',
            '--lyria-journal='+str(evidence/'tests-journal.jsonl'),
            '--basetemp='+str(evidence/'pytest-temp'),'--junitxml='+str(evidence/'tests.xml')]


def smoke_report_paths(evidence):
    evidence=Path(evidence)
    # The app creates these parent directories exclusively and owns their storage.
    # Keep them separate from private_desktop's already-created output directories.
    return dict(startup=evidence/'startup.json',
                foundation=evidence/'foundation-artifacts'/'report.json',
                editing=evidence/'editing-artifacts'/'report.json')


def smoke_arguments(target,evidence):
    target=Path(target);evidence=Path(evidence);reports=smoke_report_paths(evidence)
    return dict(startup=['--video',str(target/'examples/synthetic_mv.mp4'),'--exit-after','5000',
                        '--screenshot',str(evidence/'startup.png'),'--self-check',str(reports['startup']),
                        '--storage-dir',str(evidence/'storage')],
                foundation=['--foundation-self-check',str(reports['foundation'])],
                editing=['--editing-self-check',str(reports['editing'])])


def validate_smoke_report(name,path):
    data=json.loads(Path(path).read_text('utf8'))
    kinds=dict(startup='qt-runtime-smoke',foundation='foundation-synthetic-end-to-end',
               editing='phase-three-synthetic-editing-and-candidates')
    if data.get('kind')!=kinds[name] or data.get('ok') is not True:
        raise RuntimeError(name+' selfcheck failed or wrong report kind')
    flags=('qml_loaded','media_requested','sink_frame_valid','visible_frame_samples_match') if name=='startup' else ('qml_window_loaded','screenshot_saved')
    if any(data.get(k) is not True for k in flags):raise RuntimeError(name+' selfcheck evidence missing')
    if name!='startup' and (data.get('status')!='passed' or not data.get('checks')):
        raise RuntimeError(name+' selfcheck has no completed checks')
    return data


def move_package(package,target,*,evidence,delivery):
    """One bounded retry for a transient Windows directory sharing denial only."""
    package=Path(package);target=Path(target)
    expected=Path(evidence).resolve()/'dist/Lyria';destination=Path(delivery).resolve()
    def validate():
        if (os.path.lexists(target) or not package.is_dir() or package.is_symlink()
                or package.resolve()!=expected or target.parent.resolve()!=destination
                or target.parent!=Path(delivery)):
            raise RuntimeError('Unsafe final package move')
    for attempt in range(2):
        validate()
        try:
            package.rename(target)
            return
        except PermissionError:
            if attempt:raise
            # A failed move must not be treated as evidence that source/target
            # are unchanged. Recheck now and after the short backoff.
            validate()
            time.sleep(.25)


def execute(plan):
    if sys.platform!='win32':raise RuntimeError('Windows only, no foreground fallback')
    sources,delivery,workspace,runtime=validate_layout([x['source'] for x in plan['editions']],plan['delivery'],plan['workspace'],plan['runtime'])
    if workspace.exists():raise ValueError('Refusing reused build workspace')
    if sha(runtime/'lyria-shared-runtime.json')!=plan['runtime_manifest_sha256']:raise ValueError('Runtime identity changed')
    if sha(Path(plan['reading_bundle'])/'manifest.json')!=plan['reading_manifest_sha256']:raise ValueError('Reading bundle identity changed')
    if shutil.disk_usage(delivery).free<plan['incremental_budget_bytes']:raise RuntimeError('Insufficient free space for declared peak/reserve')
    for source,edition in zip(sources,plan['editions']):
        current=fingerprint(source)
        if not current['clean'] or any(current[k]!=edition[k] for k in ('commit','tracked_sha256')):raise RuntimeError('Source changed or uncommitted; regenerate frozen plan')
        if identity(source)!=edition['label']:raise RuntimeError('Edition identity changed')
    workspace.mkdir(parents=True);write(workspace/'input-plan.json',plan)
    python=plan['python'];env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',
        LYRIA_READING_BUNDLE=plan['reading_bundle'],PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf8')
    env.pop('LYRIA_ALIGNMENT_PYTHON',None);env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    run([python,'-B','-m','pip','check'],sources[0],workspace/'pip-check.log',env)
    run([python,'-B',sources[0]/'tools/shared_runtime_bundle.py','verify','--root',runtime,'--report',workspace/'runtime-verified.json'],sources[0],workspace/'runtime-verify.log',env)
    report=json.loads((workspace/'runtime-verified.json').read_text('utf8'))
    if report.get('returncode')!=0 or report.get('manifest_sha256')!=plan['runtime_manifest_sha256']:raise RuntimeError('Shared runtime audit failed')
    # Pure stdlib validator; keeps one verified-tree cache for both editions.
    spec=importlib.util.spec_from_file_location('dual_shared_runtime',sources[0]/'lyricsync/shared_runtime.py')
    shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)
    results=[]
    for index,(source,edition) in enumerate(zip(sources,plan['editions'])):
        evidence=workspace/('edition-'+str(index));evidence.mkdir()
        env['PYINSTALLER_CONFIG_DIR']=str(evidence/'pyinstaller-cache')
        temporary=evidence/'temp';temporary.mkdir();env.update(TEMP=str(temporary),TMP=str(temporary))
        private=source/'tools/private_desktop.py';audit=source/'tools/audit_portable_runtime.py'
        def isolated(name,command,timeout=300,override=None):
            out=evidence/name
            run([python,'-B',private,'--out',out,'--timeout',str(timeout),'--',*command],source,evidence/(name+'.launcher.log'),override or env,timeout+60)
            state=json.loads((out/'result.json').read_text('utf8'))
            if state.get('returncode')!=0 or state.get('desktop_handle_closed') is not True:raise RuntimeError('Private desktop did not finish safely')
        for mode in ('source','prebuild'):
            isolated('audit-'+mode,[python,'-B',str(audit),mode,'--root',str(source),'--report',str(evidence/(mode+'.json'))],1200)
        junit=evidence/'tests.xml'
        isolated('tests',pytest_command(python,evidence),7200)
        tests=require_junit(junit)
        if fingerprint(source)!=dict(commit=edition['commit'],tracked_sha256=edition['tracked_sha256'],clean=True):raise RuntimeError('Source changed during tests')
        dist=evidence/'dist'
        run([python,'-B','-m','PyInstaller','--noconfirm','--distpath',dist,'--workpath',evidence/'work',source/'LyriaQt.spec'],source,evidence/'pyinstaller.log',env,1800)
        package=dist/'Lyria';contract=json.loads((source/'tools/portable_runtime_manifest.json').read_text('utf8'))
        copy_public(source,package,contract);shutil.copyfile(source/'tools/portable_runtime_manifest.json',package/'portable_runtime_manifest.json')
        isolated('audit-postbuild',[python,'-B',str(audit),'postbuild','--root',str(package),'--prebuild-report',str(evidence/'prebuild.json'),'--report',str(evidence/'postbuild.json'),'--run-binaries'],1200)
        if json.loads((evidence/'postbuild.json').read_text('utf8')).get('technical_ok') is not True:raise RuntimeError('Packaged audit not passed')
        import pefile
        pe=pefile.PE(str(package/'Lyria.exe'));strings={}
        for group in pe.FileInfo:
            for item in group:
                for table in getattr(item,'StringTable',[]):strings.update({k.decode():v.decode() for k,v in table.entries.items()})
        pe.close()
        if any(strings.get(k)!=edition['label'] for k in ('FileVersion','ProductVersion')):raise RuntimeError('Real PE version/edition mismatch')
        for rel in contract['internal_files']:
            if sha(package/'_internal'/rel)!=sha(source/rel):raise RuntimeError('Loose worker differs from tested source')
        target=delivery/edition['label']
        if target.exists() or package.resolve()!=evidence/'dist/Lyria' or target.parent!=delivery:raise RuntimeError('Unsafe final package move')
        move_package(package,target,evidence=evidence,delivery=delivery)
        (target/'BUILD_INCOMPLETE').write_text('Do not use until validation succeeds.\n',encoding='utf8')
        write(target/'_internal/shared-runtime-reference.json',dict(schema=1,runtime_relative='../shared-runtime',manifest_sha256=plan['runtime_manifest_sha256']))
        app=target/'Lyria.exe'
        smoke_reports=smoke_report_paths(evidence)
        cases=smoke_arguments(target,evidence)
        for name,args in cases.items():
            isolated(name,[str(app),*args],120)
            validate_smoke_report(name,smoke_reports[name])
            if name=='startup' and (not (evidence/'startup.png').is_file() or (evidence/'startup.png').stat().st_size==0):raise RuntimeError('Startup screenshot missing')
        for name,request in [('sudachi-probe',dict(operation='reading_probe',reading_tool='sudachi')),
                             ('sudachi-convert',dict(operation='readings',reading_tool='sudachi',rows=[dict(id='test',text='一緒に歌おう')]))]:
            incoming=evidence/(name+'-request.json');outgoing=evidence/(name+'-result.json');write(incoming,request)
            isolated(name,[str(app),'--reading-worker','--request',str(incoming),'--result',str(outgoing)],120)
            result=json.loads(outgoing.read_text('utf8'))
            if name.endswith('probe') and not result.get('reading_tool',{}).get('ready'):raise RuntimeError('Bundled dictionary probe failed')
            if name.endswith('convert') and ''.join(result.get('rows',[{}])[0].get('reading','').split())!='isshoniutaou':raise RuntimeError('Bundled dictionary conversion failed')
        for name,request in [('pykakasi',dict(operation='readings',reading_tool='pykakasi',rows=[dict(id='test',text='桜')])),
                             ('model-probe',dict(operation='probe',device='cpu',language='Japanese'))]:
            request['_shared_runtime_attestation']=shared.verify_shared_runtime(runtime/'python.exe',app=target)
            incoming=evidence/(name+'-request.json');outgoing=evidence/(name+'-result.json');write(incoming,request)
            local_cache=evidence/'runtime-cache';local_cache.mkdir(exist_ok=True)
            worker_env=dict(env,PATH=os.pathsep.join([str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'),str(runtime),str(runtime/'DLLs')]),
                NUMBA_CACHE_DIR=str(local_cache/'numba'),HF_HOME=str(local_cache/'hf'),TORCH_HOME=str(local_cache/'torch'),
                XDG_CACHE_HOME=str(local_cache),MPLCONFIGDIR=str(local_cache/'matplotlib'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
            isolated(name,[str(runtime/'python.exe'),'-I','-B',str(target/'_internal/lyricsync/local_alignment_worker.py'),'--request',str(incoming),'--result',str(outgoing)],180,worker_env)
            value=json.loads(outgoing.read_text('utf8'))
            if name=='pykakasi' and ''.join(value.get('rows',[{}])[0].get('reading','').split())!='sakura':raise RuntimeError('Shared pykakasi failed')
            if name=='model-probe' and value.get('runtime',{}).get('device')!='cpu':raise RuntimeError('Shared model probe failed')
        if fingerprint(source)!=dict(commit=edition['commit'],tracked_sha256=edition['tracked_sha256'],clean=True):raise RuntimeError('Source changed during build')
        (target/'BUILD_INCOMPLETE').unlink()
        result=dict(edition=edition['label'],source_commit=edition['commit'],source_fingerprint=edition['tracked_sha256'],exe_sha256=sha(app),tests=tests,technical_checks_passed=True,legal_clear=False,foreground_acceptance=False)
        write(target/'build-metadata.json',result);results.append(result)
    write(delivery/'dual-build-result.json',dict(editions=results,shared_runtime_manifest_sha256=plan['runtime_manifest_sha256'],legal_clear=False,publication=False,heavy_model_inference_in_this_build=False))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('plan')
    for name in ('source-a','source-b','delivery','workspace','runtime','python','reading-bundle','out'):a.add_argument('--'+name,required=True)
    a=sub.add_parser('build');a.add_argument('--plan',required=True);a.add_argument('--execute',action='store_true')
    args=p.parse_args()
    if args.command=='plan':
        result=make_plan([args.source_a,args.source_b],args.delivery,args.workspace,args.runtime,args.python,args.reading_bundle)
        if Path(args.out).exists():raise ValueError('Plan already exists; use a fresh name')
        write(args.out,result);print(json.dumps(result,ensure_ascii=True))
    else:
        if not args.execute:p.error('Explicit --execute is required; preparation does not authorize running build')
        execute(json.loads(Path(args.plan).read_text('utf8')))

if __name__=='__main__':main()
