"""Opt-in synthetic audio lifecycle diagnosis; never opens user inputs.

Run one scope at a time. The nonfatal traceback watchdog observes stalls; it
never terminates the process. Each invocation requires a new evidence directory.
"""
from __future__ import annotations
import argparse
import faulthandler
import functools
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--scope',choices=('single','sequence','module'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    os.environ.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONIOENCODING='utf-8')
    if os.name=='nt':
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001|0x0002|0x8000)
        os.environ['QT_QPA_FONTDIR']=str(Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts')
    status=dict(pid=os.getpid(),scope=args.scope,started=time.time(),status='running')
    def publish():
        (output/'process.json').write_text(json.dumps(status,indent=2),encoding='utf8')
    publish();print(json.dumps(status),flush=True)
    trace=(output/'trace.jsonl').open('w',encoding='utf8')
    dump=(output/'traceback.log').open('w',encoding='utf8')
    lock=threading.Lock()
    def event(name,**fields):
        row=dict(time=time.time(),thread=threading.current_thread().name,event=name,**fields)
        with lock:trace.write(json.dumps(row,ensure_ascii=False)+'\n');trace.flush()
    faulthandler.enable(file=dump,all_threads=True)
    faulthandler.dump_traceback_later(20,repeat=True,file=dump,exit=False)
    # Trace plain Python entry points without replacing Qt's decorated signal
    # handlers or changing signal connection/dispatch semantics.
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid
    from desktop_qt.controller import Bridge
    from desktop_qt.media import MediaController
    retained=[];applications=[]
    def wrap(cls,name):
        original=getattr(cls,name)
        @functools.wraps(original)
        def observed(self,*values,**kwargs):
            label=cls.__name__+'.'+name
            extra={'command':values[0]} if name=='command' and values else {}
            event(label+'.enter',**extra)
            try:
                result=original(self,*values,**kwargs)
                if cls is Bridge and name=='__init__':
                    retained.append(self);applications.append(QApplication.instance())
                event(label+'.exit',**extra)
                return result
            except BaseException as exc:
                event(label+'.error',error=type(exc).__name__,**extra);raise
        setattr(cls,name,observed)
    for name in ('__init__','command','_new','_save','_start_job','shutdown','wait_for_shutdown','deleteLater'):
        wrap(Bridge,name)
    for name in ('__init__','open','clear','seek','pause','play','close'):
        wrap(MediaController,name)
    import pytest
    class Phases:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_setup(self,item):
            event('pytest.setup.enter',node=item.nodeid)
            yield
            event('pytest.setup.exit',node=item.nodeid)
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self,item):
            event('pytest.call.enter',node=item.nodeid)
            yield
            event('pytest.call.exit',node=item.nodeid)
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_teardown(self,item):
            event('pytest.teardown.enter',node=item.nodeid)
            yield
            event('pytest.teardown.exit',node=item.nodeid)
    module='tests/test_phase2_audio_editing.py'
    audio=module+'::test_audio_load_seek_step_analysis_save_reopen'
    selected=[audio] if args.scope=='single' else [module]
    if args.scope=='sequence':
        selected=[module+'::'+name for name in (
            'test_srt_exact_boundaries_ids_undo_save_reopen',
            'test_empty_pure_editing_and_real_transport',
            'test_undo_virtual_creation_restores_transport')]+[audio]
    code=1
    try:
        code=int(pytest.main([*selected,'-x','-q','--basetemp='+str(output/'tmp'),
                              '--junitxml='+str(output/'pytest.xml')],plugins=[Phases()]))
    finally:
        # If a fixture fails before deletion, still retain and drain its owner.
        for bridge in retained:
            if isValid(bridge):bridge.wait_for_shutdown(QApplication.instance())
        event('driver.finished',exit_code=code,threads=[t.name for t in threading.enumerate()])
        status.update(status='completed',finished=time.time(),exit_code=code)
        publish();faulthandler.cancel_dump_traceback_later();faulthandler.disable()
        trace.close();dump.close()
    return code

if __name__=='__main__':raise SystemExit(main())
