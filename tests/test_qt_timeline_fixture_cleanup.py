"""Injected setup failures must still stop/join the fixture decoder."""
import pytest
import test_qt_timeline_input as timeline
from test_qt_timeline_input import application


def track_bridges(monkeypatch):
    created = []
    original = timeline.Bridge
    def make(*args, **kwargs):
        bridge = original(*args, **kwargs)
        created.append(bridge)
        return bridge
    monkeypatch.setattr(timeline, 'Bridge', make)
    return created


def test_qml_load_failure_stops_worker(application, tmp_path, monkeypatch):
    created = track_bridges(monkeypatch)
    monkeypatch.setattr(timeline.QQmlApplicationEngine, 'load', lambda *args: None)
    with pytest.raises(AssertionError, match='root window'):
        with timeline.timeline_session(application, tmp_path / 'recovery'):
            pytest.fail('No QML window should have been yielded')
    assert len(created) == 1
    assert created[0]._shutdown
    assert not created[0].media_controller.worker.thread.is_alive()


def test_pre_yield_setup_failure_stops_worker(application, tmp_path, monkeypatch):
    created = track_bridges(monkeypatch)
    with pytest.raises(RuntimeError, match='injected setup failure'):
        with timeline.timeline_session(application, tmp_path / 'recovery') as (bridge, window):
            assert bridge.media_controller.worker.thread.is_alive()
            raise RuntimeError('injected setup failure')
    assert len(created) == 1
    assert created[0]._shutdown
    assert not created[0].media_controller.worker.thread.is_alive()


def test_active_job_cancelled_before_engine_destroyed(application, tmp_path, monkeypatch):
    import threading
    import time
    created = track_bridges(monkeypatch)
    engines = []
    destroyed = []
    original = timeline.QQmlApplicationEngine
    def make_engine():
        engine = original()
        engine.destroyed.connect(lambda: destroyed.append(True))
        engines.append(engine)
        return engine
    monkeypatch.setattr(timeline, 'QQmlApplicationEngine', make_engine)
    started = threading.Event()
    cancelled = threading.Event()
    threads = []
    def work(cancel, progress):
        threads.append(threading.current_thread())
        started.set()
        while not cancel():
            time.sleep(.005)
        cancelled.set()
    with pytest.raises(RuntimeError, match='injected active job failure'):
        with timeline.timeline_session(application, tmp_path / 'recovery') as (bridge, window):
            bridge.jobs.run('fixture-cleanup-probe', work)
            assert started.wait(2)
            assert bridge.jobs.active
            raise RuntimeError('injected active job failure')
    assert cancelled.is_set()
    assert not created[0].jobs.active
    assert not created[0].media_controller.worker.thread.is_alive()
    assert destroyed == [True]
    for thread in threads:
        thread.join(1)
        assert not thread.is_alive()
