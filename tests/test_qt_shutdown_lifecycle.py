"""Each lifecycle case owns one small offscreen process. No user media/API/audio.

Failure artifacts retain child PID, timestamps, exit status, stdout and stderr.
A timeout is reported without terminating another process or a worker thread.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

ROOT=Path(__file__).resolve().parents[1]
CHILD=r"""
import os,sys,time,json,threading,faulthandler
if os.name=='nt':
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(0x0001|0x0002|0x8000)
faulthandler.enable()
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QTimer,Qt,QCoreApplication
from PySide6.QtWidgets import QApplication
from desktop_qt.controller import Bridge
from desktop_qt.diagnostics import DiagnosticStore
case=sys.argv[1];base=Path(sys.argv[2]);started=time.monotonic()
print(json.dumps({'pid':os.getpid(),'case':case,'started':time.time()}),flush=True)

def pump(app,predicate,seconds=3):
    end=time.monotonic()+seconds
    while not predicate() and time.monotonic()<end:
        app.processEvents();time.sleep(.002)
    assert predicate(), 'controlled condition did not complete'

if case in ('direct_app_exit','direct_exit_write_error','qml_load_failure'):
    import desktop_qt.controller as controller
    import app_qt
    captured=[]
    class StartupBridge(Bridge):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);captured.append(self)
            self.audio_started=threading.Event()
            def work(cancel,progress):
                self.audio_started.set();time.sleep(.18)
                (base/'background-finished').write_text('done')
            self._start_job('controlled-worker',work,lambda *args:None,foreground=False)
            if case=='direct_app_exit':QTimer.singleShot(0,lambda:QCoreApplication.exit(7))
            if case=='direct_exit_write_error':
                self.closing_release=threading.Event();self.writer_entered=threading.Event()
                self.writer_saw_shutdown=False
                def fail(*args):
                    self.writer_entered.set()
                    assert self.closing_release.wait(2), 'shutdown never released controlled writer'
                    assert self._shutdown, 'writer failed before shutdown began'
                    self.writer_saw_shutdown=True
                    raise OSError('controlled closing write error')
                controller.write_document=fail
                self._save(True,str(base/'failed.lyria'))
                def exit_after_writer_enters():
                    if self.writer_entered.is_set():QCoreApplication.exit(0)
                    else:QTimer.singleShot(1,exit_after_writer_enters)
                QTimer.singleShot(0,exit_after_writer_enters)
        def shutdown(self):
            super().shutdown()
            event=getattr(self,'closing_release',None)
            if event is not None:event.set()
    controller.Bridge=StartupBridge
    if case=='qml_load_failure':
        import PySide6.QtQml
        class Signal:
            def connect(self,*args):pass
        class FailedEngine:
            warnings=Signal()
            def rootContext(self):return self
            def setContextProperty(self,*args):pass
            def load(self,*args):pass
            def rootObjects(self):return []
        PySide6.QtQml.QQmlApplicationEngine=FailedEngine
    args=SimpleNamespace(storage_dir=base/'storage',native_window_frame=False,
        foundation_self_check=None,editing_self_check=None,project=None,video=None,
        screenshot=None,self_check=None,exit_after=None)
    diagnostics=DiagnosticStore(base/'diagnostics')
    try:
        code=app_qt._run_application(args,diagnostics,SimpleNamespace(install_qt=lambda:None))
        assert (case=='direct_app_exit' and code==7) or (case=='direct_exit_write_error' and code==1)
    except Exception as exc:
        from desktop_qt.diagnostics import StartupFailure
        assert case=='qml_load_failure' and isinstance(exc,StartupFailure),repr(exc)
    if case=='direct_exit_write_error':assert captured[0].writer_saw_shutdown
    assert captured[0].shutdown_complete
    assert not captured[0].jobs.live_threads()
    assert not captured[0].media_controller.worker.thread.is_alive()
    assert (base/'background-finished').exists()
else:
    app=QApplication([]);bridge=Bridge(storage_dir=base/'storage')
    bridge.media_controller.audio.setMuted(True)
    release=threading.Event();fallback=threading.Timer(2.,release.set);fallback.start()
    ticks=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(1));timer.start()
    quits=[];bridge.quitRequested.connect(lambda:quits.append(1))
    try:
        if case=='partial_bridge_failure':
            import desktop_qt.controller as controller
            real_media=controller.MediaController;media=[]
            def capture(*args,**kwargs):
                value=real_media(*args,**kwargs);media.append(value);return value
            controller.MediaController=capture
            invalid=base/'not-directory';invalid.write_text('fixture')
            try:Bridge(storage_dir=invalid,diagnostics=DiagnosticStore(base/'diagnostics'))
            except FileExistsError:pass
            else:raise AssertionError('Expected constructor failure')
            assert media and not media[0].worker.thread.is_alive()
            bridge.wait_for_shutdown(app)
        elif case in ('shutdown_save_retry','superseded_save_failure'):
            import desktop_qt.controller as controller
            real=controller.write_document;calls=[]
            def first_fails(snapshot,path):
                calls.append((str(path),snapshot['revision']))
                if len(calls)==1:
                    release.wait();raise OSError('controlled first write failure')
                return real(snapshot,path)
            controller.write_document=first_fails
            target=base/'retry.lyria'
            bridge._save(True,str(target))
            if case=='superseded_save_failure':
                bridge.document.project.settings['latest']='preserve';bridge.document.touch()
                bridge._save(True,str(target))
            bridge._begin_quit();QTimer.singleShot(80,release.set)
            if case=='shutdown_save_retry':
                pump(app,lambda:bridge._shutdown_failure_waiting)
                assert not bridge.shutdown_complete and not quits
                assert bridge.state['closeDialog']['shutdownFailure']
                assert not bridge.command('close.resolve',{'choice':'cancel'})
                assert bridge.state['closeDialog']['visible']
                assert bridge.command('close.resolve',{'choice':'save'})
            pump(app,lambda:bridge.shutdown_complete)
            assert quits==[1] and len(calls)==2
            saved=json.loads(target.read_text('utf8'))
            if case=='superseded_save_failure':assert saved['project']['settings']['latest']=='preserve'
            assert not bridge._shutdown_failed_saves
        elif case=='pending_save':
            import desktop_qt.controller as controller
            real=controller.write_document;calls=[]
            def slow(snapshot,path):
                calls.append(str(path))
                if len(calls)==1:release.wait()
                return real(snapshot,path)
            controller.write_document=slow
            bridge.document.project.settings['test_value']='first'
            bridge._save(True,str(base/'first.lyria'))
            bridge.document.project.settings['test_value']='second';bridge.document.touch()
            bridge._save(True,str(base/'second.lyria'))
            bridge.shutdown();QTimer.singleShot(100,release.set)
            bridge.wait_for_shutdown(app)
            assert len(calls)==2
            assert json.loads((base/'first.lyria').read_text('utf8'))['project']['settings']['test_value']=='first'
            assert json.loads((base/'second.lyria').read_text('utf8'))['project']['settings']['test_value']=='second'
        elif case=='save_close':
            bridge.document.touch();bridge.path=str(base/'saved.lyria')
            assert not bridge.requestClose()
            assert bridge.command('close.resolve',{'choice':'save'})
            pump(app,lambda:bridge.shutdown_complete)
            assert (base/'saved.lyria').exists() and quits==[1]
            assert bridge.document.saved_revision==bridge.document.revision
        elif case=='failed_save_close':
            import desktop_qt.controller as controller
            def fail(*args):raise OSError('controlled save error')
            controller.write_document=fail
            bridge.document.touch();bridge.path=str(base/'failed.lyria')
            assert not bridge.requestClose()
            assert bridge.command('close.resolve',{'choice':'save'})
            pump(app,lambda:bridge._save_running is None)
            assert not bridge._shutdown and not quits and bridge.state['error']
            bridge.wait_for_shutdown(app)
        elif case=='finished_before_exit':
            observed=[]
            bridge.jobs.finished.connect(lambda *args:release.wait(),Qt.ConnectionType.DirectConnection)
            bridge._start_job('controlled-worker',lambda cancel,progress:None,lambda *args:None,foreground=False)
            pump(app,lambda:not bridge.jobs.active)
            assert bridge.jobs.live_threads()
            assert not bridge.requestClose()
            QTimer.singleShot(50,lambda:observed.append(not bridge.shutdown_complete and bool(bridge.jobs.live_threads())))
            QTimer.singleShot(100,release.set)
            pump(app,lambda:bridge.shutdown_complete)
            assert observed==[True] and quits==[1]
        elif case=='async_close':
            began=threading.Event()
            def work(cancel,progress):began.set();release.wait()
            bridge._start_job('controlled-worker',work,lambda *args:None,foreground=False)
            pump(app,began.is_set)
            before=time.monotonic();assert not bridge.requestClose()
            assert time.monotonic()-before<.1
            assert not bridge.command('project.new',{})
            assert not bridge.requestClose()
            bridge._shutdown_warning=time.monotonic()-11
            bridge._poll_shutdown()
            assert not bridge.shutdown_complete
            QTimer.singleShot(100,release.set)
            pump(app,lambda:bridge.shutdown_complete)
            assert len(ticks)>5 and quits==[1]
            bridge.shutdown();bridge._poll_shutdown();assert quits==[1]
        elif case=='decoder_exit_error':
            # A synthetic worker cleanup error must be recorded, never hidden
            # behind a timeout or attributed to unrelated heap corruption.
            bridge.media_controller.worker.exit_error=RuntimeError('controlled decoder cleanup error')
            bridge.wait_for_shutdown(app)
            assert bridge._shutdown_errors==['RuntimeError']
        else:raise AssertionError(case)
        assert bridge.shutdown_complete
        assert not bridge.jobs.live_threads()
        assert not bridge.media_controller.worker.thread.is_alive()
    finally:
        release.set();fallback.cancel();fallback.join()
        bridge.wait_for_shutdown(app)
        timer.stop()
print(json.dumps({'pid':os.getpid(),'case':case,'elapsed':time.monotonic()-started,'ok':True}),flush=True)
"""

@pytest.mark.parametrize('case',['async_close','finished_before_exit','pending_save','save_close',
    'failed_save_close','decoder_exit_error','direct_app_exit','direct_exit_write_error','qml_load_failure',
    'partial_bridge_failure','shutdown_save_retry','superseded_save_failure'])
def test_shutdown_in_isolated_process(case,tmp_path):
    stdout=tmp_path/'stdout.log';stderr=tmp_path/'stderr.log'
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONIOENCODING='utf-8')
    start=time.time()
    with stdout.open('w',encoding='utf8') as out,stderr.open('w',encoding='utf8') as err:
        child=subprocess.Popen([sys.executable,'-c',CHILD,case,str(tmp_path)],cwd=ROOT,env=env,
            stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        deadline_exceeded=False
        try:code=child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            deadline_exceeded=True
            # Do not orphan a native worker process or start another Qt case.
            # Record the live owner first, then keep waiting without force-kill.
            (tmp_path/'process.json').write_text(json.dumps(dict(case=case,
                launcher_pid=child.pid,start=start,timeout_at=time.time(),
                status='deadline exceeded; retaining owners and waiting for safe process exit'),indent=2),encoding='utf8')
            print('Shutdown test deadline exceeded; waiting for owned child PID '+str(child.pid),flush=True)
            code=child.wait()
    first_line=stdout.read_text('utf8').splitlines()
    actual_pid=json.loads(first_line[0]).get('pid') if first_line and first_line[0].startswith('{') else None
    record=dict(case=case,pid=actual_pid,launcher_pid=child.pid,start=start,end=time.time(),exit_code=code,
                stdout=str(stdout),stderr=str(stderr),deadline_exceeded=deadline_exceeded)
    (tmp_path/'process.json').write_text(json.dumps(record,indent=2),encoding='utf8')
    assert code==0 and not deadline_exceeded, json.dumps(record)+'\n'+stdout.read_text('utf8')+'\n'+stderr.read_text('utf8')
