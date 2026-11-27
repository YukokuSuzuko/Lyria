"""Fail-closed Windows test desktop. Never switches/opens the user's desktop.
Docs: learn.microsoft.com/windows/win32/api/winuser/nf-winuser-createdesktopw
      learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw
No handle inheritance at CreateProcess. Only the new owned job is terminated.
"""
import argparse, ctypes as C, json, os, subprocess, sys, time, uuid
from ctypes import wintypes as W
from pathlib import Path

class STARTUPINFO(C.Structure):
    _fields_=[('cb',W.DWORD),('lpReserved',W.LPWSTR),('lpDesktop',W.LPWSTR),('lpTitle',W.LPWSTR),
              *[(n,W.DWORD) for n in ('dwX','dwY','dwXSize','dwYSize','dwXCountChars','dwYCountChars','dwFillAttribute','dwFlags')],
              ('wShowWindow',W.WORD),('cbReserved2',W.WORD),('lpReserved2',C.c_void_p),
              ('hStdInput',W.HANDLE),('hStdOutput',W.HANDLE),('hStdError',W.HANDLE)]
class PROCESSINFO(C.Structure):
    _fields_=[('hProcess',W.HANDLE),('hThread',W.HANDLE),('dwProcessId',W.DWORD),('dwThreadId',W.DWORD)]
class BASICLIMIT(C.Structure):
    _fields_=[('PerProcessUserTimeLimit',C.c_longlong),('PerJobUserTimeLimit',C.c_longlong),('LimitFlags',W.DWORD),
              ('MinimumWorkingSetSize',C.c_size_t),('MaximumWorkingSetSize',C.c_size_t),('ActiveProcessLimit',W.DWORD),
              ('Affinity',C.c_size_t),('PriorityClass',W.DWORD),('SchedulingClass',W.DWORD)]
class IOCOUNTERS(C.Structure):
    _fields_=[(n,C.c_ulonglong) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
class EXTENDEDLIMIT(C.Structure):
    _fields_=[('BasicLimitInformation',BASICLIMIT),('IoInfo',IOCOUNTERS),
              *[(n,C.c_size_t) for n in ('ProcessMemoryLimit','JobMemoryLimit','PeakProcessMemoryUsed','PeakJobMemoryUsed')]]

def api():
    if sys.platform!='win32':raise RuntimeError('Windows only; no non-isolated fallback')
    k=C.WinDLL('kernel32',use_last_error=True);u=C.WinDLL('user32',use_last_error=True)
    signatures=[(u,'CreateDesktopW',[W.LPCWSTR,W.LPCWSTR,C.c_void_p,W.DWORD,W.DWORD,C.c_void_p],W.HANDLE),
      (u,'CloseDesktop',[W.HANDLE],W.BOOL),(u,'GetThreadDesktop',[W.DWORD],W.HANDLE),
      (u,'GetUserObjectInformationW',[W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),
      (k,'GetCurrentThreadId',[],W.DWORD),(k,'CreateJobObjectW',[C.c_void_p,W.LPCWSTR],W.HANDLE),
      (k,'SetInformationJobObject',[W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
      (k,'AssignProcessToJobObject',[W.HANDLE,W.HANDLE],W.BOOL),
      (k,'TerminateJobObject',[W.HANDLE,W.UINT],W.BOOL),(k,'TerminateProcess',[W.HANDLE,W.UINT],W.BOOL),
      (k,'CreateProcessW',[W.LPCWSTR,W.LPWSTR,C.c_void_p,C.c_void_p,W.BOOL,W.DWORD,C.c_void_p,W.LPCWSTR,C.POINTER(STARTUPINFO),C.POINTER(PROCESSINFO)],W.BOOL),
      (k,'ResumeThread',[W.HANDLE],W.DWORD),(k,'WaitForSingleObject',[W.HANDLE,W.DWORD],W.DWORD),
      (k,'GetExitCodeProcess',[W.HANDLE,C.POINTER(W.DWORD)],W.BOOL),(k,'CloseHandle',[W.HANDLE],W.BOOL)]
    for lib,name,args,result in signatures:
        f=getattr(lib,name);f.argtypes=args;f.restype=result
    return k,u

def checked(ok):
    if not ok:raise C.WinError(C.get_last_error())
    return ok

def desktop_name():
    k,u=api();handle=checked(u.GetThreadDesktop(k.GetCurrentThreadId()));needed=W.DWORD()
    u.GetUserObjectInformationW(handle,2,None,0,C.byref(needed))
    if not needed.value:raise C.WinError(C.get_last_error())
    buf=C.create_unicode_buffer(needed.value//C.sizeof(C.c_wchar)+1)
    checked(u.GetUserObjectInformationW(handle,2,buf,C.sizeof(buf),C.byref(needed)))
    return buf.value

def child(request_path):
    request=json.loads(Path(request_path).read_text('utf-8'))
    actual=desktop_name()
    # Verification happens before any toolkit/executable initialization.
    with open(request['log'],'w',encoding='utf-8') as log:
        log.write(json.dumps(dict(event='private-desktop-verified',pid=os.getpid(),actual=actual,expected=request['desktop']))+'\n');log.flush()
        if actual!=request['desktop']:raise RuntimeError('Desktop mismatch; test was NOT launched')
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',PYTHONUTF8='1',PYTHONIOENCODING='utf-8',LYRIA_EXPECTED_TEST_DESKTOP=actual)
        # Child target gets only its redirected log and NUL input, not parent handles.
        done=subprocess.run(request['command'],cwd=request['cwd'],env=env,stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW)
        log.write(json.dumps(dict(event='private-command-complete',returncode=done.returncode))+'\n')
        return done.returncode

def launch(command,out,timeout):
    k,u=api();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    desktop='LyriaTest_'+uuid.uuid4().hex
    request=dict(desktop=desktop,command=command,cwd=str(Path.cwd()),log=str(out/'output.log'))
    request_path=out/'request.json';request_path.write_text(json.dumps(request,ensure_ascii=False,indent=2),'utf-8')
    desk=job=None;info=PROCESSINFO();assigned=False;code=1;start=time.monotonic()
    try:
        # Minimum desktop window/menu/object access, no SWITCHDESKTOP or ACL edits.
        desk=checked(u.CreateDesktopW(desktop,None,None,0,0x0001|0x0002|0x0004|0x0040|0x0080,None))
        job=checked(k.CreateJobObjectW(None,None));limit=EXTENDEDLIMIT();limit.BasicLimitInformation.LimitFlags=0x2000
        checked(k.SetInformationJobObject(job,9,C.byref(limit),C.sizeof(limit)))
        si=STARTUPINFO();si.cb=C.sizeof(si);si.lpDesktop=desktop
        cmd=C.create_unicode_buffer(subprocess.list2cmdline([sys.executable,'-B',str(Path(__file__).resolve()),'--child',str(request_path)]))
        checked(k.CreateProcessW(sys.executable,cmd,None,None,False,0x08000000|0x00000004,None,str(Path.cwd()),C.byref(si),C.byref(info)))
        checked(k.AssignProcessToJobObject(job,info.hProcess));assigned=True
        if k.ResumeThread(info.hThread)==0xFFFFFFFF:raise C.WinError(C.get_last_error())
        (out/'owner.json').write_text(json.dumps(dict(pid=info.dwProcessId,desktop=desktop,handle_inheritance=False,job_kill_on_close=True)), 'utf-8')
        while True:
            result=k.WaitForSingleObject(info.hProcess,200)
            if result==0:break
            if result!=258:raise C.WinError(C.get_last_error())
            if time.monotonic()-start>timeout:raise TimeoutError('Private test desktop timeout')
        value=W.DWORD();checked(k.GetExitCodeProcess(info.hProcess,C.byref(value)));code=int(value.value)
        return code
    finally:
        if job and assigned:k.TerminateJobObject(job,125)
        elif info.hProcess:k.TerminateProcess(info.hProcess,125)
        if info.hProcess:k.WaitForSingleObject(info.hProcess,5000)
        if info.hThread:k.CloseHandle(info.hThread)
        if info.hProcess:k.CloseHandle(info.hProcess)
        if job:k.CloseHandle(job)
        closed=bool(u.CloseDesktop(desk)) if desk else True
        (out/'result.json').write_text(json.dumps(dict(returncode=code,desktop=desktop,desktop_handle_closed=closed,seconds=round(time.monotonic()-start,3))), 'utf-8')
        if not closed:raise RuntimeError('Could not close owned desktop handle; no foreground fallback')

def main():
    p=argparse.ArgumentParser();p.add_argument('--child');p.add_argument('--out');p.add_argument('--timeout',type=float,default=300);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    if a.child:return child(a.child)
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    if not a.out or not command:p.error('--out and command required')
    return launch(command,a.out,a.timeout)
if __name__=='__main__':raise SystemExit(main())
