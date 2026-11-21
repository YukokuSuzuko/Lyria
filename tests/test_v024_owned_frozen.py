"""Fixed bundled-worker selection and fail-closed Windows ownership tests."""
import os
import sys
import subprocess
from pathlib import Path
from unittest.mock import Mock
import pytest
from lyricsync import owned_process as own


def command(tmp_path):
    return [str(tmp_path/'Lyria.exe'),'--reading-worker','--request',str(tmp_path/'in.json'),'--result',str(tmp_path/'out.json')]


def test_only_exact_frozen_protocol_is_native(tmp_path,monkeypatch):
    cmd=command(tmp_path)
    monkeypatch.setattr(sys,'executable',cmd[0])
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    assert own._is_bundled_reading_command(cmd)
    for invalid in [cmd+['--extra'],[cmd[0],'-c','pass'],[str(tmp_path/'Other.exe'),*cmd[1:]],
                    [cmd[0],'--reading-worker','--request','relative.json','--result',cmd[5]],
                    [cmd[0],'--reading-worker','--result',cmd[3],'--request',cmd[5]]]:
        assert not own._is_bundled_reading_command(invalid)
    monkeypatch.setattr(sys,'frozen',False)
    assert not own._is_bundled_reading_command(cmd)


def test_shell_is_never_allowed(tmp_path):
    with pytest.raises(ValueError):own.launch_owned(command(tmp_path),shell=True)


@pytest.mark.skipif(os.name!='nt',reason='Windows job ordering')
def test_native_assign_precedes_resume_and_command_is_unchanged(tmp_path,monkeypatch):
    cmd=command(tmp_path);events=[]
    monkeypatch.setattr(sys,'executable',cmd[0]);monkeypatch.setattr(sys,'frozen',True,raising=False)
    job=Mock();job.assign.side_effect=lambda p:events.append('assign')
    proc=Mock();proc.pid=123
    def popen(args,**options):
        assert args==cmd and options['creationflags'] & 4
        assert options['stdin']==subprocess.DEVNULL
        events.append('create-suspended');return proc
    monkeypatch.setattr(own,'_WindowsJob',lambda:job)
    monkeypatch.setattr(own.subprocess,'Popen',popen)
    monkeypatch.setattr(own,'_resume_initial_threads',lambda pid:events.append('resume'))
    assert own.launch_owned(cmd) is proc
    assert events==['create-suspended','assign','resume']
    assert proc._lyria_job is job


@pytest.mark.skipif(os.name!='nt',reason='Windows job fail closed')
@pytest.mark.parametrize('stage',['assign','resume'])
def test_native_failure_terminates_owned_process_and_closes_job(tmp_path,monkeypatch,stage):
    cmd=command(tmp_path)
    monkeypatch.setattr(sys,'executable',cmd[0]);monkeypatch.setattr(sys,'frozen',True,raising=False)
    job=Mock();proc=Mock();proc.poll.return_value=None
    monkeypatch.setattr(own,'_WindowsJob',lambda:job)
    monkeypatch.setattr(own.subprocess,'Popen',lambda *a,**kw:proc)
    resume=Mock();monkeypatch.setattr(own,'_resume_initial_threads',resume)
    (job.assign if stage=='assign' else resume).side_effect=OSError('fail-closed fixture')
    with pytest.raises(OSError,match='fail-closed'):own.launch_owned(cmd)
    proc.terminate.assert_called_once();proc.wait.assert_called_once_with(timeout=3)
    job.close.assert_called_once()
    if stage=='assign':resume.assert_not_called()
