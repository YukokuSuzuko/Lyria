"""Wait helpers for Qt diagnostics with Python background workers.

QTest.qWait processes Qt events, but on the tested PySide build retains the GIL
for much of the wait. Repeating it starves Python decode/index loops while the
Qt heartbeat still looks healthy. Use a real Qt event loop between test inputs,
just as QApplication.exec does in the application. This is test tooling only.
"""
from PySide6.QtCore import QEventLoop, QTimer, Qt


def delay(milliseconds):
    loop = QEventLoop()
    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(milliseconds)
    loop.exec()


def wait_until(predicate, timeout_ms=15000, poll_ms=5):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setTimerType(Qt.TimerType.PreciseTimer)
    deadline = QTimer()
    deadline.setTimerType(Qt.TimerType.PreciseTimer)
    deadline.setSingleShot(True)
    failure = []

    def check():
        try:
            if predicate():
                loop.quit()
        except Exception as exc:
            failure.append(exc)
            loop.quit()

    poll.timeout.connect(check)
    deadline.timeout.connect(loop.quit)
    poll.start(poll_ms)
    deadline.start(timeout_ms)
    try:
        loop.exec()
    finally:
        poll.stop()
        deadline.stop()
    if failure:
        raise failure[0]
    if not predicate():
        raise TimeoutError('Timed out waiting for actual decoder/GUI state')
