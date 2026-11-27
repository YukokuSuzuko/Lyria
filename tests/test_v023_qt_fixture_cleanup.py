"""Setup failures must release actual native owners, not just successful yields."""
import pytest
import test_qt_timeline_fold_motion as fold
from test_qt_timeline_fold_motion import application


def test_fold_setup_exception_drains_bridge_before_engine_release(application,tmp_path,monkeypatch):
    owners=[]
    original=fold.Bridge
    def capture(*args,**kwargs):
        bridge=original(*args,**kwargs);owners.append(bridge);return bridge
    class BrokenLoad(fold.QQmlApplicationEngine):
        def load(self,*args):raise RuntimeError('injected setup failure before yield')
    monkeypatch.setattr(fold,'Bridge',capture)
    monkeypatch.setattr(fold,'QQmlApplicationEngine',BrokenLoad)
    scene=fold.ui.__wrapped__(application,tmp_path,monkeypatch)
    with pytest.raises(RuntimeError,match='injected setup failure'):
        next(scene)
    assert len(owners)==1
    assert owners[0].shutdown_complete
    assert not owners[0].media_controller.worker.thread.is_alive()
    assert not owners[0].jobs.live_threads()


@pytest.mark.parametrize('module_name', ['test_qt_preview_visibility', 'test_qt_review_selection', 'test_qt_window_chrome'])
def test_shared_fixture_setup_exception_drains_owners(application, tmp_path, monkeypatch, module_name):
    import importlib
    from types import SimpleNamespace
    module=importlib.import_module(module_name)
    bridges=[];engines=[];destroyed=[]
    original=module.Bridge
    def capture(*args, **kwargs):
        bridge=original(*args, **kwargs);bridges.append(bridge);return bridge
    class BrokenLoad(module.QQmlApplicationEngine):
        def __init__(self):
            super().__init__();engines.append(self)
            self.destroyed.connect(lambda: destroyed.append(True))
        def load(self, *args):raise RuntimeError('injected shared setup failure')
        def loadData(self, *args):raise RuntimeError('injected shared setup failure')
    monkeypatch.setattr(module, 'Bridge', capture)
    monkeypatch.setattr(module, 'QQmlApplicationEngine', BrokenLoad)
    args=[application, tmp_path]
    if module_name=='test_qt_review_selection':args.append(monkeypatch)
    elif module_name=='test_qt_window_chrome':args.append(SimpleNamespace())
    scene=module.ui.__wrapped__(*args)
    with pytest.raises(RuntimeError, match='injected shared setup failure'):next(scene)
    assert len(bridges)==len(engines)==len(destroyed)==1
    assert bridges[0].shutdown_complete
    assert not bridges[0].media_controller.worker.thread.is_alive()
    assert not bridges[0].jobs.live_threads()
