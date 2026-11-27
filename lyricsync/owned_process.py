"""Own only newly launched Windows Python task trees, including orphaned children.

The child is gated before executing the requested command. No global process
scan, named job, security setting, existing environment or user process is used.
"""
import ctypes
from ctypes import wintypes as w
import json
import os
import sys
from pathlib import Path
import subprocess


class _WindowsJob:
    def __init__(self):
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),
                      ('minimum_working_set',ctypes.c_size_t),('maximum_working_set',ctypes.c_size_t),
                      ('active_process_limit',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                      ('peak_process_memory',ctypes.c_size_t),('peak_job_memory',ctypes.c_size_t)]
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        for name,argtypes,restype in (
            ('CreateJobObjectW',[ctypes.c_void_p,w.LPCWSTR],w.HANDLE),
            ('SetInformationJobObject',[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD],w.BOOL),
            ('AssignProcessToJobObject',[w.HANDLE,w.HANDLE],w.BOOL),
            ('TerminateJobObject',[w.HANDLE,w.UINT],w.BOOL),
            ('CloseHandle',[w.HANDLE],w.BOOL)):
            fn=getattr(self.kernel,name);fn.argtypes=argtypes;fn.restype=restype
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=Extended();limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            error=ctypes.WinError(ctypes.get_last_error());self.close();raise error
    def assign(self,process):
        if not self.kernel.AssignProcessToJobObject(self.handle,w.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
    def close(self):
        if self.handle:
            handle,self.handle=self.handle,None
            if not self.kernel.CloseHandle(handle):raise ctypes.WinError(ctypes.get_last_error())
    def stop(self):
        if self.handle:
            # Closing also kills, but explicitly request termination so failure is visible.
            ok=self.kernel.TerminateJobObject(self.handle,1)
            error=None if ok else ctypes.WinError(ctypes.get_last_error())
            self.close()
            if error:raise error


def _resume_initial_threads(pid):
    """Resume only threads of our still-suspended just-created process.

    Popen closes CreateProcess's initial thread handle, so reacquire it by the
    exact owned PID. The process cannot spawn a venv redirector child before
    this step. No existing process is suspended, resumed, or terminated.
    """
    class Entry(ctypes.Structure):
        _fields_=[('size',w.DWORD),('usage',w.DWORD),('tid',w.DWORD),('pid',w.DWORD),
                  ('base_priority',w.LONG),('delta_priority',w.LONG),('flags',w.DWORD)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    for name,argtypes,restype in (
        ('CreateToolhelp32Snapshot',[w.DWORD,w.DWORD],w.HANDLE),
        ('Thread32First',[w.HANDLE,ctypes.POINTER(Entry)],w.BOOL),
        ('Thread32Next',[w.HANDLE,ctypes.POINTER(Entry)],w.BOOL),
        ('OpenThread',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
        ('ResumeThread',[w.HANDLE],w.DWORD),('CloseHandle',[w.HANDLE],w.BOOL)):
        fn=getattr(kernel,name);fn.argtypes=argtypes;fn.restype=restype
    snapshot=kernel.CreateToolhelp32Snapshot(4,0)  # TH32CS_SNAPTHREAD
    if snapshot==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    ids=[]
    try:
        entry=Entry();entry.size=ctypes.sizeof(entry)
        ok=kernel.Thread32First(snapshot,ctypes.byref(entry))
        while ok:
            if entry.pid==pid:ids.append(entry.tid)
            entry.size=ctypes.sizeof(entry)
            ok=kernel.Thread32Next(snapshot,ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if not ids:raise RuntimeError('启动后台子进程失败（未找到挂起的主线程）')
    for tid in ids:
        handle=kernel.OpenThread(2,False,tid)  # THREAD_SUSPEND_RESUME
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        try:
            previous=kernel.ResumeThread(handle)
            if previous==0xFFFFFFFF:raise ctypes.WinError(ctypes.get_last_error())
            if previous==0:raise RuntimeError('启动后台子进程失败（初始线程状态异常）')
        finally:
            kernel.CloseHandle(handle)


def _is_bundled_reading_command(command):
    """Only our frozen EXE's fixed reading-worker protocol avoids Python gate."""
    return (bool(getattr(sys, 'frozen', False))
            and isinstance(command, (list, tuple)) and len(command) == 6
            and Path(command[0]).resolve() == Path(sys.executable).resolve()
            and command[1] == '--reading-worker'
            and command[2] == '--request' and command[4] == '--result'
            and all(isinstance(command[i], str) and Path(command[i]).is_absolute()
                    for i in (3, 5)))


def launch_owned(command, **kwargs):
    """Popen-compatible owner. Input commands here are explicit Python tasks."""
    if kwargs.get('shell'):
        raise ValueError('Owned tasks require an explicit executable, never a shell')
    if os.name!='nt':return subprocess.Popen(command,**kwargs)
    job=_WindowsJob()
    gate=("import sys,json,subprocess; "
          "allowed=sys.stdin.buffer.read(1); "
          "sys.exit(subprocess.call(json.loads(sys.argv[1]),stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) if allowed==b'1' else 125)")
    native_reading = _is_bundled_reading_command(command)
    options=dict(kwargs)
    options['stdin']=subprocess.DEVNULL if native_reading else subprocess.PIPE
    options['creationflags']=options.get('creationflags',0)|0x4  # CREATE_SUSPENDED
    process=None
    try:
        launch = list(command) if native_reading else [command[0],'-I','-B','-u','-c',gate,json.dumps(list(command))]
        process=subprocess.Popen(launch,**options)
        job.assign(process)
        process._lyria_job=job
        _resume_initial_threads(process.pid)
        if not native_reading:
            process.stdin.write(b'1' if not options.get('encoding') else '1')
            process.stdin.flush();process.stdin.close()
        return process
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate();process.wait(timeout=3)
        job.close()
        raise
