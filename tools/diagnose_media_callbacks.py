"""Opt-in narrow media callback tracing. No source/media/user-state modification."""
import faulthandler, json, os, sys, threading, time, traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QThread
from desktop_qt.media import MediaController
out=Path(sys.argv[1]);out.mkdir(exist_ok=False,parents=True)
log=(out/'callbacks.jsonl').open('w',encoding='utf8')
for name in ('_audio_status','_audio_error'):
    original=getattr(MediaController,name)
    def traced(self,*args,_original=original,_name=name):
        log.write(json.dumps(dict(t=time.time(),event=_name,thread=threading.get_ident(),gui=QThread.currentThread()==self.thread(),args=[str(a) for a in args],stack=[f'{f.name}:{f.lineno}' for f in traceback.extract_stack(limit=14)]))+'\n');log.flush()
        return _original(self,*args)
    setattr(MediaController,name,traced)
print('PID',os.getpid(),'PARENT',os.getppid(),flush=True)
import pytest
raise SystemExit(pytest.main(['tests/test_phase2_audio_editing.py','-v','-x','-o','faulthandler_timeout=10','--basetemp='+str(out/'tmp'),'--junitxml='+str(out/'pytest.xml')]))
