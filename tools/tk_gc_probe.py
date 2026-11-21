"""Experimental test-only teardown probe; no test assertion or application patch."""
import gc
counts=[]
def pytest_collection_modifyitems(items):
    classes={item.cls for item in items if getattr(item,'cls',None) is not None and item.cls.__name__=='PlaybackTests'}
    for cls in classes:
        original=cls.tearDown
        def teardown(self,_original=original):
            try:return _original(self)
            finally:
                self.app=None;self.root=None
                counts.append(gc.collect())
        cls.tearDown=teardown
def pytest_sessionfinish(session,exitstatus):
    print('TK_TEARDOWN_GC_COLLECTIONS',counts,flush=True)
