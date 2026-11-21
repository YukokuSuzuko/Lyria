"""Process bookkeeping and safe overdue behavior without running Qt or children."""
import subprocess
from tools import validate_windows_offscreen as runner


class FakeProcess:
    pid = 43210
    def __init__(self, answers):
        self.answers = list(answers)
        self.waits = []
    def wait(self, timeout=None):
        self.waits.append(timeout)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def run_fake(monkeypatch, answers):
    process = FakeProcess(answers)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *args, **kwargs: process)
    snapshots = []
    record = {'name': 'fixture', 'status': 'running'}
    runner._run_recorded_process(['unused'], None, {}, record,
                                 lambda: snapshots.append(dict(record)), 9)
    return process, record, snapshots


def test_records_pid_before_wait_and_completed_exit(monkeypatch):
    process, record, snapshots = run_fake(monkeypatch, [0])
    assert snapshots[0]['pid'] == 43210 and snapshots[0]['status'] == 'running'
    assert record['status'] == 'passed' and record['exit_code'] == 0
    assert record['started_utc'] <= record['finished_utc']
    assert not record['abnormal_exit'] and process.waits == [9]


def test_overdue_waits_without_killing_process(monkeypatch):
    process, record, snapshots = run_fake(monkeypatch, [subprocess.TimeoutExpired('unused', 9), 0])
    assert process.waits == [9, None]
    assert snapshots[1]['status'] == 'overdue_waiting_for_exit'
    assert record['timeout_seconds'] == 9 and record['status'] == 'passed'


def test_native_fault_is_distinguished_from_assertion_failure(monkeypatch):
    for code, native in [(1, False), (0xC0000374, True), (-1073741819, True)]:
        _, record, _ = run_fake(monkeypatch, [code])
        assert record['status'] == 'failed'
        assert record['abnormal_exit'] is native