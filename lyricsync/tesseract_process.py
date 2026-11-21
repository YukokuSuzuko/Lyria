"""Bounded, windowless Tesseract probes without changing global DLL settings."""
import subprocess
import sys
import time
from pathlib import Path
import re
import os
import shutil


def tesseract_call(command, options):
    """Child-local path adapter; explicit data > env > adjacent portable data.

    Some Windows builds reject Unicode in an absolute tessdata argument even
    though executable/input/output Unicode paths work. Keep data relative to
    the child cwd; never chdir the application or alter its environment.
    """
    options=dict(options)
    options['creationflags']=options.get('creationflags',0)|getattr(subprocess,'CREATE_NO_WINDOW',0)
    if not isinstance(command,(list,tuple)) or not command or options.get('shell'):
        return command,options
    argv=[os.fspath(x) for x in command]
    original_cwd=Path(options.get('cwd') or os.getcwd()).resolve()
    env=options.get('env') if options.get('env') is not None else os.environ
    def absolute(value):
        value=value[1:-1] if len(value)>1 and value[0]==value[-1]=='"' else value
        path=Path(value)
        return path if path.is_absolute() else original_cwd/path
    selected=None;explicit=[]
    for i,arg in enumerate(argv):
        if arg=='--tessdata-dir' and i+1<len(argv):selected=argv[i+1];explicit.append((i+1,False))
        elif arg.startswith('--tessdata-dir='):selected=arg.split('=',1)[1];explicit.append((i,True))
    if selected is None:selected=env.get('TESSDATA_PREFIX')
    if selected is None:
        exe=absolute(argv[0])
        if exe.is_file() and (exe.parent/'tessdata').is_dir():selected=str(exe.parent/'tessdata')
    if not selected:return command,options  # System installation resolves its own default.
    data=absolute(selected).resolve()
    if not data.is_dir():return command,options  # Never substitute for an explicit missing model tree.
    executable=absolute(argv[0])
    if executable.is_file():argv[0]=str(executable)
    elif not Path(argv[0]).parent==Path('.'):
        argv[0]=str(executable)
    else:
        found=shutil.which(argv[0],path=env.get('PATH',os.defpath))
        if found:argv[0]=str(Path(found).resolve())
    # pytesseract's positional input/output precede options; stdin/stdout stay pipes.
    if len(argv)>2 and not argv[1].startswith('-'):
        for i in (1,2):
            if argv[i] not in ('stdin','stdout','-'):argv[i]=str(absolute(argv[i]))
    for i,arg in enumerate(argv[:-1]):
        if arg in ('--user-words','--user-patterns'):argv[i+1]=str(absolute(argv[i+1]))
    # Existing explicit config paths are relative to the caller, not the data cwd.
    option_values={'-l','-c','--psm','--oem','--dpi','--loglevel','--tessdata-dir','--user-words','--user-patterns'}
    skip=False
    for i in range(3,len(argv)):
        if skip:skip=False;continue
        if argv[i] in option_values:skip=True;continue
        if not argv[i].startswith('-') and absolute(argv[i]).is_file():argv[i]=str(absolute(argv[i]))
    for i,equal in explicit:argv[i]='--tessdata-dir=.' if equal else '.'
    child_env=dict(env);child_env['TESSDATA_PREFIX']='.'
    options.update(cwd=str(data),env=child_env)
    return argv,options


class _TesseractProcesses:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    @staticmethod
    def _options(options):
        options = dict(options)
        options['creationflags'] = options.get('creationflags', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return options

    @staticmethod
    def _call(args,options):
        if args:
            command,options=tesseract_call(args[0],options)
            return (command,*args[1:]),options
        if 'args' in options:
            original=dict(options);command=original.pop('args')
            command,options=tesseract_call(command,original);options['args']=command
            return args,options
        return args,_TesseractProcesses._options(options)

    def Popen(self, *args, **options):
        args,options=self._call(args,options)
        return self.wrapped.Popen(*args, **options)

    def run(self,*args,**options):
        args,options=self._call(args,options)
        options.setdefault('timeout',10)
        return self.wrapped.run(*args,**options)

    def check_output(self, *args, **options):
        args,options=self._call(args,options)
        options.setdefault('timeout', 10)
        for attempt in range(2):
            try:
                return self.wrapped.check_output(*args, **options)
            except subprocess.CalledProcessError as error:
                if attempt or error.returncode & 0xffffffff != 0xc0000142:
                    raise
                time.sleep(.05)


def prepare_tesseract_processes(pytesseract):
    """Adapt only pytesseract's subprocess reference, not the shared module."""
    if sys.platform != 'win32':
        return
    internal = getattr(pytesseract, 'pytesseract', None)
    wrapped = getattr(internal, 'subprocess', None)
    if wrapped is not None and not isinstance(wrapped, _TesseractProcesses):
        internal.subprocess = _TesseractProcesses(wrapped)


def installed_tessdata(executable):
    """Find the data directory actually selected by a system installation.

    Portable builds keep tessdata beside the executable, but distro packages
    commonly put it under a versioned share directory. Ask the same binary
    used for recognition instead of guessing a platform-specific location or
    hashing models that it would not load. This also respects TESSDATA_PREFIX.
    """
    output = _TesseractProcesses(subprocess).check_output(
        [str(executable), "--list-langs"], stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, encoding="utf-8")
    match = re.search(r'^List of available languages in "(.+)" \(\d+\):\s*$',
                      output, re.MULTILINE)
    if not match:
        raise RuntimeError('未找到 Tesseract 语言包目录（tessdata），请使用完整安装包。')
    result=Path(match.group(1))
    if result.is_absolute():return result
    _,options=tesseract_call([str(executable),'--list-langs'],{})
    return (Path(options.get('cwd') or os.getcwd())/result).resolve()
