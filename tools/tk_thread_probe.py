"""Read-only Tk object lifetime/thread tracing for test diagnosis."""
import json,threading,weakref
from pathlib import Path
path=None;lock=threading.Lock()
def emit(event,**data):
    with lock:
        with path.open('a',encoding='utf-8') as log:log.write(json.dumps(dict(event=event,thread=threading.current_thread().name,ident=threading.get_ident(),**data))+'\n')
def pytest_configure(config):
    global path
    path=Path(config.option.basetemp).parent/'tk-threads.jsonl'
def pytest_collection_modifyitems(items):
    classes={item.cls for item in items if getattr(item,'cls',None) is not None and item.cls.__name__=='PlaybackTests'}
    for cls in classes:
        setup,teardown=cls.setUp,cls.tearDown
        def start(self,_original=setup):
            emit('setup-start',test=self.id(),threads=[t.name for t in threading.enumerate()])
            result=_original(self)
            for name in ('root','app'):
                obj=getattr(self,name);weakref.finalize(obj,emit,'finalized',kind=name,identity=id(obj))
            emit('setup-complete',test=self.id());return result
        def end(self,_original=teardown):
            emit('teardown-start',test=self.id(),threads=[t.name for t in threading.enumerate()])
            try:return _original(self)
            finally:emit('teardown-end',test=self.id(),threads=[t.name for t in threading.enumerate()])
        cls.setUp=start;cls.tearDown=end
