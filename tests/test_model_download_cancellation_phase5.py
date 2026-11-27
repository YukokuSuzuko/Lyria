"""Real loopback sockets: read1 slow-drip, stalled body, cancellation and 206 resume.

No trust policy is widened. A test-only opener maps an official pinned URL to
loopback and restores geturl for the response check. Production timeout stays 30 s.
"""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import time
import urllib.request

import pytest
from lyricsync import local_models as lm


@contextmanager
def local_download(mode):
    state={'mode':mode,'requests':[],'entered':threading.Event(),'release':threading.Event()}
    payload=b'x'*4096
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*_):pass
        def do_GET(self):
            value=self.headers.get('Range','bytes=0-');offset=int(value[6:-1])
            state['requests'].append(value)
            self.send_response(206 if offset else 200)
            self.send_header('Content-Length',str(len(payload)-offset))
            if offset:self.send_header('Content-Range',f'bytes {offset}-{len(payload)-1}/{len(payload)}')
            self.end_headers();state['entered'].set()
            try:
                if state['mode']=='full':self.wfile.write(payload[offset:]);self.wfile.flush();return
                self.wfile.write(payload[offset:offset+1]);self.wfile.flush()
                if state['mode']=='stall':state['release'].wait(3);return
                for byte in payload[offset+1:]:
                    if state['release'].wait(.04):return
                    self.wfile.write(bytes([byte]));self.wfile.flush()
            except (OSError,ConnectionError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
    class Opener:
        def open(self,request,timeout):
            assert timeout==30
            local=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/weights',headers=dict(request.header_items()))
            response=urllib.request.urlopen(local,timeout=timeout)
            response.geturl=lambda:request.full_url
            return response
    try:yield state,payload,Opener()
    finally:state['release'].set();server.shutdown();server.server_close();server_thread.join(2)


def synthetic_model(monkeypatch,payload):
    spec=lm.ModelSpec('cancel-fixture','Official/Test','b'*40,'Test only',
        (lm.ModelFile('fixture.dat',len(payload),hashlib.sha256(payload).hexdigest()),))
    monkeypatch.setitem(lm.CATALOG,spec.id,spec);return spec


@pytest.mark.parametrize('mode',['drip','stall'])
def test_real_blocked_or_slow_drip_cancel_preserves_partial_and_leaks_no_thread(tmp_path,monkeypatch,mode):
    with local_download(mode) as (state,payload,opener):
        spec=synthetic_model(monkeypatch,payload);manager=lm.ModelManager(tmp_path/'cache')
        stop=threading.Event();outcomes=[];progress=[]
        def run():
            try:manager.download(spec.id,opener=opener,cancel=stop.is_set,progress=lambda p,m:progress.append((p,m)))
            except BaseException as exc:outcomes.append(exc)
        worker=threading.Thread(target=run,daemon=True);worker.start();assert state['entered'].wait(2)
        time.sleep(.15);started=time.monotonic();stop.set();worker.join(2)
        assert not worker.is_alive(), 'Cancellation still blocked despite 30-second read timeout'
        assert time.monotonic()-started<1
        assert len(outcomes)==1 and isinstance(outcomes[0],InterruptedError)
        assert not manager.path(spec.id).exists() and not list(manager.root.glob('*.lock'))
        part=manager.root/('.download-'+spec.id+'-'+spec.revision)/'fixture.dat.part'
        assert part.is_file() and 0<part.stat().st_size<len(payload)
        assert not any(t.name=='Lyria-model-read-cancel' for t in threading.enumerate())
        assert progress and all(p<100 for p,_ in progress)
        # A later explicit resume must request exactly the retained prefix and
        # cannot publish until the complete original pinned content is verified.
        offset=part.stat().st_size;state['mode']='full'
        result=manager.download(spec.id,opener=opener)
        assert state['requests']==['bytes=0-',f'bytes={offset}-']
        assert result==manager.verify(spec.id)
        assert (result/'fixture.dat').read_bytes()==payload


def test_read1_is_used_instead_of_waiting_for_full_megabyte(tmp_path,monkeypatch):
    payload=b'synthetic incremental body';spec=synthetic_model(monkeypatch,payload)
    class Response:
        headers={}
        def __init__(self):self.position=0
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def geturl(self):return 'https://huggingface.co/Official/Test'
        def getcode(self):return 200
        def read(self,*_):pytest.fail('Full read regression')
        def read1(self,_):
            value=payload[self.position:self.position+1];self.position+=1;return value
    class Opener:
        def open(self,*_,**__):return Response()
    manager=lm.ModelManager(tmp_path/'cache')
    assert manager.download(spec.id,opener=Opener())==manager.verify(spec.id)


def test_cancel_before_connect_does_not_create_cache_or_open_socket(tmp_path,monkeypatch):
    spec=synthetic_model(monkeypatch,b'x')
    class Opener:
        def open(self,*_,**__):pytest.fail('Already-cancelled download connected')
    manager=lm.ModelManager(tmp_path/'cache')
    with pytest.raises(InterruptedError):manager.download(spec.id,cancel=lambda:True,opener=Opener())
    assert not manager.root.exists()
