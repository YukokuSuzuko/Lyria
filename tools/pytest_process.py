"""Recorded subprocess entry for isolated pytest modules; no UI or process control."""
from datetime import datetime, timezone
import faulthandler
import os
from pathlib import Path
import sys
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform == 'win32':
    import ctypes
    kernel=ctypes.windll.kernel32
    kernel.SetErrorMode(kernel.GetErrorMode() | 0x8003)
if os.environ.get('LYRIA_EXPECTED_TEST_DESKTOP'):
    from tools.private_desktop import desktop_name
    actual=desktop_name()
    if actual!=os.environ['LYRIA_EXPECTED_TEST_DESKTOP']:
        raise RuntimeError('Test desktop mismatch: refusing toolkit initialization')
    print('PYTEST_VERIFIED_PRIVATE_DESKTOP',actual,flush=True)
faulthandler.enable()
print('PYTEST_PYTHON_PID',os.getpid(),'PARENT_PID',os.getppid(),
      'START_UTC',datetime.now(timezone.utc).isoformat(),flush=True)
import pytest
code=pytest.main(sys.argv[1:])
print('PYTEST_END_UTC',datetime.now(timezone.utc).isoformat(),'EXIT',int(code),
      'PYTHON_THREADS',[thread.name for thread in threading.enumerate()],flush=True)
raise SystemExit(code)