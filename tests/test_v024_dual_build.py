import json
from pathlib import Path
import pytest
from tools import build_v024_dual as b


def fixtures(tmp_path,monkeypatch):
    a=tmp_path/'source-a';c=tmp_path/'source-b'
    for source,edition in ((a,''),(c,'OCR修改')):
        (source/'lyricsync').mkdir(parents=True)
        (source/'lyricsync/__init__.py').write_text("__version__='v0.24'\n__edition__="+repr(edition),encoding='utf8')
    delivery=tmp_path/'交付 space';runtime=delivery/'shared-runtime';runtime.mkdir(parents=True)
    (runtime/'lyria-shared-runtime.json').write_text('{}')
    reading=tmp_path/'reading';reading.mkdir();(reading/'manifest.json').write_text('{}')
    python=tmp_path/'python.exe';python.write_bytes(b'fixture-not-executable')
    monkeypatch.setattr(b,'fingerprint',lambda r:dict(commit='a'*40,tracked_sha256='b'*64,clean=True))
    return [a,c],delivery,tmp_path/'private-build',runtime,python,reading


def test_plan_has_no_build_install_or_copy(tmp_path,monkeypatch):
    args=fixtures(tmp_path,monkeypatch)
    monkeypatch.setattr(b,'run',lambda *a,**k:pytest.fail('plan may not execute'))
    value=b.make_plan(*args)
    assert [x['label'] for x in value['editions']]==list(b.LABELS)
    assert value['legal_clear'] is False and not value['source_and_runtime_copied']
    assert not args[2].exists() and not (args[1]/'v0.24').exists()


def test_wrong_edition_rejected(tmp_path,monkeypatch):
    args=fixtures(tmp_path,monkeypatch)
    (args[0][1]/'lyricsync/__init__.py').write_text("__version__='v0.24'\n__edition__=''",encoding='utf8')
    with pytest.raises(ValueError,match='edition'):b.make_plan(*args)


def test_no_existing_package_overwrite(tmp_path,monkeypatch):
    args=fixtures(tmp_path,monkeypatch);(args[1]/'v0.24').mkdir()
    with pytest.raises(ValueError,match='overwrite'):b.make_plan(*args)


def test_no_private_workspace_inside_delivery(tmp_path,monkeypatch):
    args=list(fixtures(tmp_path,monkeypatch));args[2]=args[1]/'private'
    with pytest.raises(ValueError,match='outside'):b.make_plan(*args)


@pytest.mark.parametrize('name',['../secret','C:/secret','docs/private-results.json','advice/notes.md','licenses/../x','/root','a\\b'])
def test_public_material_path_allowlist(name):
    with pytest.raises(ValueError):b.safe_relative(name)


def test_public_copy_rejects_untracked_material(tmp_path,monkeypatch):
    source=tmp_path/'source';(source/'licenses').mkdir(parents=True);(source/'licenses/LICENSE').write_text('license')
    package=tmp_path/'package';package.mkdir()
    monkeypatch.setattr(b,'git',lambda *a:b'')
    with pytest.raises(ValueError,match='Untracked'):b.copy_public(source,package,dict(external_trees=['licenses'],external_files=[]))
    assert not (package/'licenses/LICENSE').exists()


@pytest.mark.parametrize('xml',['<testsuites/>','<testsuite tests="0"/>','<testsuite tests="5" failures="1"/>','<testsuite tests="5" errors="1"/>'])
def test_failed_or_empty_tests_never_waived(tmp_path,xml):
    p=tmp_path/'tests.xml';p.write_text(xml)
    with pytest.raises(RuntimeError):b.require_junit(p)


def test_skips_reported_not_passes(tmp_path):
    p=tmp_path/'tests.xml';p.write_text('<testsuites><testsuite tests="4" failures="0" errors="0" skipped="1"/></testsuites>')
    assert b.require_junit(p)==dict(tests=4,skipped=1)


def test_dirty_source_blocks_before_any_execution(tmp_path,monkeypatch):
    args=fixtures(tmp_path,monkeypatch);plan=b.make_plan(*args)
    monkeypatch.setattr(b.sys,'platform','win32')
    monkeypatch.setattr(b,'fingerprint',lambda r:dict(commit='a'*40,tracked_sha256='b'*64,clean=False))
    monkeypatch.setattr(b,'run',lambda *a,**k:pytest.fail('must not execute dirty source'))
    with pytest.raises(RuntimeError,match='uncommitted'):b.execute(plan)
    assert not args[2].exists()


def test_runtime_pin_change_blocks_before_execution(tmp_path,monkeypatch):
    args=fixtures(tmp_path,monkeypatch);plan=b.make_plan(*args)
    (args[3]/'lyria-shared-runtime.json').write_text('{"changed":true}')
    monkeypatch.setattr(b.sys,'platform','win32')
    monkeypatch.setattr(b,'run',lambda *a,**k:pytest.fail('must not execute changed runtime'))
    with pytest.raises(ValueError,match='identity changed'):b.execute(plan)



def test_full_suite_has_sys_capture_and_unique_flushed_journal(tmp_path):
    command=b.pytest_command('python',tmp_path/'edition-0')
    assert '--capture=sys' in command and 'tools.pytest_journal' in command and 'no:cacheprovider' in command
    assert '--lyria-journal='+str(tmp_path/'edition-0/tests-journal.jsonl') in command
    assert '--basetemp='+str(tmp_path/'edition-0/pytest-temp') in command
    assert command!=b.pytest_command('python',tmp_path/'edition-1')



@pytest.mark.parametrize('name',['foundation','editing'])
def test_probe_storage_uses_fresh_artifacts_not_appdata(tmp_path,monkeypatch,name):
    import app_qt
    import desktop_qt.diagnostics as diagnostics
    import lyricsync.ocr_font_setup as fonts
    from types import SimpleNamespace
    evidence=tmp_path/'edition';evidence.mkdir()
    (evidence/name).mkdir()  # private_desktop has already created its own log root
    report=b.smoke_report_paths(evidence)[name]
    assert not report.parent.exists()
    observed=[]
    class Store:
        def __init__(self,path):observed.append(Path(path))
        def record(self,*a,**k):pass
    monkeypatch.setattr(diagnostics,'DiagnosticStore',Store)
    monkeypatch.setattr(diagnostics,'RuntimeHooks',lambda *_:SimpleNamespace(install=lambda:None,restore=lambda:None))
    monkeypatch.setattr(fonts,'configure_ocr_font',lambda:None)
    def fake_run(args,*_):
        assert Path(args.storage_dir)==report.parent/'storage'
        assert args.project is None and args.video is None
        return 0
    monkeypatch.setattr(app_qt,'_run_application',fake_run)
    assert app_qt.main(b.smoke_arguments(tmp_path/'package',evidence)[name])==0
    assert observed==[report.parent/'storage']
    assert report.parent.is_dir() and report.parent!=evidence/name



@pytest.mark.parametrize('name',['foundation','editing'])
def test_actual_probe_finish_json_contract(tmp_path,name):
    from types import SimpleNamespace as N
    from desktop_qt.foundation_probe import FoundationProbe
    from desktop_qt.editing_probe import EditingProbe
    report=b.smoke_report_paths(tmp_path)[name];report.parent.mkdir()
    image=N(isNull=lambda:False,save=lambda p:(bool(Path(p).write_bytes(b'fake-image-contract'))))
    exits=[];probe=N(root=report.parent,report=report,timer=N(stop=lambda:None),
        window=N(grabWindow=lambda:image),results=[dict(check='fixture',ok=True)],app=N(exit=exits.append))
    (FoundationProbe if name=='foundation' else EditingProbe).finish(probe,True)
    assert b.validate_smoke_report(name,report)['ok'] is True and exits==[0]


@pytest.mark.parametrize('name',['foundation','editing'])
def test_probe_rejects_user_storage_argument_before_gui(tmp_path,monkeypatch,name):
    import app_qt,lyricsync.ocr_font_setup as fonts
    monkeypatch.setattr(fonts,'configure_ocr_font',lambda:None)
    monkeypatch.setattr(app_qt,'_run_application',lambda *_:pytest.fail('must not start'))
    with pytest.raises(SystemExit):app_qt.main(['--'+name+'-self-check',str(tmp_path/'report.json'),'--storage-dir',str(tmp_path/'user-storage')])
    assert not (tmp_path/'user-storage').exists()


def test_actual_startup_writer_json_contract_without_gui(tmp_path):
    import ast,app_qt
    from types import SimpleNamespace as N
    tree=ast.parse(Path(app_qt.__file__).read_text('utf8'))
    function=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='write_self_check_report')
    image=N(isNull=lambda:False,width=lambda:10,height=lambda:10,pixelColor=lambda *_:N(red=lambda:0,green=lambda:0,blue=lambda:0))
    video=N(property=lambda _:N(x=lambda:0,y=lambda:0,width=lambda:10,height=lambda:10),mapToScene=lambda p:p)
    report=b.smoke_report_paths(tmp_path)['startup'];exits=[]
    scope=dict(Path=Path,json=json,platform=N(python_version=lambda:'fixture',platform=lambda:'fixture'),
        pyside_version='fixture',qVersion=lambda:'fixture',__version__='v0.24',sys=N(modules={}),
        args=N(video='synthetic.mp4',self_check=str(report)),engine=N(rootObjects=lambda:[object()]),
        bridge=N(media=dict(ready=True,displayedFrame=1),softwareRendering=False,_surface=None,
                 media_controller=N(sink=N(videoFrame=lambda:N(isValid=lambda:True)),_last_image=image)),
        window_chrome=N(enabled=False,platformName='offscreen',fallbackReason='fixture'),
        window=N(findChild=lambda *_:video,grabWindow=lambda:image,width=lambda:10,height=lambda:10),app=N(quit=lambda:exits.append(0)))
    exec(compile(ast.Module(body=[function],type_ignores=[]),'<actual-startup-writer>','exec'),scope)
    scope['write_self_check_report']()
    assert b.validate_smoke_report('startup',report)['ok'] is True and exits==[0]
    invalid=json.loads(report.read_text('utf8'));invalid['visible_frame_samples_match']=False;report.write_text(json.dumps(invalid))
    with pytest.raises(RuntimeError):b.validate_smoke_report('startup',report)



def package_move_fixture(tmp_path):
    evidence=tmp_path/'edition';package=evidence/'dist/Lyria';package.mkdir(parents=True)
    (package/'payload.txt').write_text('unchanged')
    delivery=tmp_path/'delivery';delivery.mkdir();target=delivery/'v0.24'
    return evidence,package,delivery,target


def test_package_move_retries_permission_once_then_succeeds(tmp_path,monkeypatch):
    evidence,package,delivery,target=package_move_fixture(tmp_path)
    original=Path.rename;attempts=[];sleeps=[]
    def rename(path,destination):
        attempts.append((path,destination))
        if len(attempts)==1:raise PermissionError('transient sharing denial')
        return original(path,destination)
    monkeypatch.setattr(Path,'rename',rename);monkeypatch.setattr(b.time,'sleep',sleeps.append)
    b.move_package(package,target,evidence=evidence,delivery=delivery)
    assert len(attempts)==2 and sleeps==[.25]
    assert not package.exists() and (target/'payload.txt').read_text()=='unchanged'


def test_package_move_persistent_permission_denial_stops_after_two_attempts(tmp_path,monkeypatch):
    evidence,package,delivery,target=package_move_fixture(tmp_path);attempts=[];sleeps=[]
    def deny(path,destination):
        attempts.append(1);raise PermissionError('persistent sharing denial')
    monkeypatch.setattr(Path,'rename',deny);monkeypatch.setattr(b.time,'sleep',sleeps.append)
    with pytest.raises(PermissionError):b.move_package(package,target,evidence=evidence,delivery=delivery)
    assert len(attempts)==2 and sleeps==[.25]
    assert package.is_dir() and not target.exists()


@pytest.mark.parametrize('timing',['denial','backoff'])
def test_package_move_never_overwrites_target_created_during_retry(tmp_path,monkeypatch,timing):
    evidence,package,delivery,target=package_move_fixture(tmp_path);attempts=[]
    def occupy():
        target.mkdir();(target/'other.txt').write_text('must survive')
    def deny(path,destination):
        attempts.append(1)
        if timing=='denial':occupy()
        raise PermissionError('transient')
    def backoff(seconds):
        assert seconds==.25
        if timing=='backoff':occupy()
    monkeypatch.setattr(Path,'rename',deny);monkeypatch.setattr(b.time,'sleep',backoff)
    with pytest.raises(RuntimeError,match='Unsafe'):b.move_package(package,target,evidence=evidence,delivery=delivery)
    assert len(attempts)==1 and package.is_dir()
    assert (target/'other.txt').read_text()=='must survive'


def test_package_move_other_error_is_not_retried(tmp_path,monkeypatch):
    evidence,package,delivery,target=package_move_fixture(tmp_path);attempts=[]
    def fail(path,destination):
        attempts.append(1);raise OSError('not a permission/sharing denial')
    monkeypatch.setattr(Path,'rename',fail)
    monkeypatch.setattr(b.time,'sleep',lambda _:pytest.fail('must not back off'))
    with pytest.raises(OSError):b.move_package(package,target,evidence=evidence,delivery=delivery)
    assert len(attempts)==1 and package.is_dir() and not target.exists()


def test_package_move_rechecks_source_path_after_denial(tmp_path,monkeypatch):
    evidence,package,delivery,target=package_move_fixture(tmp_path);attempts=[]
    original=Path.rename
    def deny(path,destination):
        attempts.append(1);original(path,path.with_name('preserved-displaced-source'))
        raise PermissionError('source moved concurrently')
    monkeypatch.setattr(Path,'rename',deny)
    monkeypatch.setattr(b.time,'sleep',lambda _:pytest.fail('source disappeared; no retry'))
    with pytest.raises(RuntimeError,match='Unsafe'):b.move_package(package,target,evidence=evidence,delivery=delivery)
    assert len(attempts)==1 and not target.exists()
    assert (package.with_name('preserved-displaced-source')/'payload.txt').read_text()=='unchanged'


def test_real_source_has_exact_current_build_identity():
    from lyricsync import __display_version__
    source=Path(__file__).resolve().parents[1]
    assert b.identity(source)==__display_version__
    assert b.LABELS==("v0.24","v0.24 OCR修改")
    assert __display_version__ == 'v0.29'


def test_source_audit_covers_all_qml_and_loose_worker_inputs():
    source=Path(__file__).resolve().parents[1]
    contract=json.loads((source/"tools/portable_runtime_manifest.json").read_text("utf-8"))
    assert "desktop_qt/qml" in contract["copied_trees"]
    assert "lyricsync/japanese_readings.py" in contract["internal_files"]
    assert "lyricsync/local_alignment_worker.py" in contract["internal_files"]
    assert "docs/v0.24/PORTABILITY.md" in contract["external_files"]
