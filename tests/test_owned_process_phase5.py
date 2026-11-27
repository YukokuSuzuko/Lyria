"""Actual owned Windows processes; never touches an existing user process."""
import ctypes
from ctypes import wintypes
import os
import sys
import threading
import time
import pytest
from lyricsync.local_runtime_setup import _run
from lyricsync import owned_process

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows Job Object contract')


@pytest.mark.parametrize('cancel_after_child',[True,False])
@pytest.mark.parametrize('assign_delay',[0.,.3])
def test_owned_orphan_exits_even_after_parent_already_exited(cancel_after_child,assign_delay,monkeypatch):
    original_assign=owned_process._WindowsJob.assign
    def delayed(self,process):
        time.sleep(assign_delay)
        return original_assign(self,process)
    monkeypatch.setattr(owned_process._WindowsJob,'assign',delayed)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];kernel.TerminateProcess.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    cancelled=threading.Event();handles=[]
    child="import os,time;print('OWNED_CHILD:'+str(os.getpid()),flush=True);time.sleep(60)"
    parent="import subprocess,sys;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);print('PARENT_DONE',flush=True)"
    def progress(p,line):
        if line.startswith('OWNED_CHILD:'):
            pid=int(line.split(':')[1]);handle=kernel.OpenProcess(0x100001,False,pid)
            assert handle;handles.append(handle)
            if cancel_after_child:cancelled.set()
    began=time.monotonic()
    try:
        with pytest.raises(InterruptedError if cancel_after_child else RuntimeError):
            _run([sys.executable,'-c',parent],cancelled.is_set,progress,0,timeout=5)
        assert handles
        assert all(kernel.WaitForSingleObject(h,1000)==0 for h in handles)
        assert time.monotonic()-began<4
        assert not any(t.name=='Lyria-installer-output' for t in threading.enumerate())
    finally:
        # Failure cleanup uses held handles to only this fixture's own children;
        # no PID scan and no risk of killing a reused PID/user Python process.
        for h in handles:
            if kernel.WaitForSingleObject(h,0)!=0:kernel.TerminateProcess(h,2)
            kernel.CloseHandle(h)


def test_job_assignment_failure_never_releases_command_gate(tmp_path,monkeypatch):
    marker=tmp_path/'must-not-exist'
    def reject(self,process):raise OSError('synthetic assign denied')
    monkeypatch.setattr(owned_process._WindowsJob,'assign',reject)
    command=[sys.executable,'-c',f"from pathlib import Path;Path({str(marker)!r}).write_text('unsafe')"]
    with pytest.raises(OSError,match='assign denied'):
        owned_process.launch_owned(command,stdout=-1,stderr=-1,encoding='utf-8',creationflags=0x08000000)
    time.sleep(.1)
    assert not marker.exists()
