"""Diagnostic waits must allow Python workers and preserve strict failures."""
import os
import threading

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from tools.qt_event_loop_wait import delay, wait_until


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


def test_wait_services_qt_events_and_python_worker(application):
    start = threading.Event()
    completed = threading.Event()

    def work():
        if start.wait(2):
            completed.set()

    worker = threading.Thread(target=work)
    worker.start()
    QTimer.singleShot(10, start.set)
    try:
        wait_until(completed.is_set, timeout_ms=1000)
    finally:
        start.set()
        worker.join(2)
    assert completed.is_set()
    assert not worker.is_alive()


def test_wait_timeout_is_not_success(application):
    with pytest.raises(TimeoutError):
        wait_until(lambda: False, timeout_ms=20)


def test_wait_propagates_predicate_failure(application):
    calls = []

    def predicate():
        calls.append(1)
        if len(calls) > 1:
            raise ValueError('actual-state failure')
        return False

    with pytest.raises(ValueError, match='actual-state failure'):
        wait_until(predicate, timeout_ms=1000)


def test_delay_dispatches_scheduled_events(application):
    calls = []
    QTimer.singleShot(0, lambda: calls.append('event'))
    delay(10)
    assert calls == ['event']
