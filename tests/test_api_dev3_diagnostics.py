import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from lyricsync import api_client
from lyricsync.storage import Cache

BODY = {'model': 'local-test', 'messages': [{'role': 'user', 'content': 'synthetic'}]}


def test_disconnect_after_post_never_repeats(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        calls = 0
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            type(self).calls += 1
            self.close_connection = True
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cache = Cache(tmp_path / 'calls.sqlite')
    try:
        with pytest.raises(api_client.ApiRequestError) as caught:
            api_client.request_json(cache, 'synthetic-key', BODY, api_client.strict_json,
                namespace='disconnect', retries=3,
                settings={'api_provider': 'custom', 'api_base_url': f'http://127.0.0.1:{server.server_port}'})
        assert Handler.calls == 1, 'peer consumed POST; repeating it may duplicate charges'
        assert caught.value.code == 'response_incomplete'
        assert caught.value.diagnostics['phase'] == 'connect_send_headers'
        with pytest.raises(api_client.ApiRequestError) as resumed:
            api_client.request_json(cache, 'synthetic-key', BODY, api_client.strict_json,
                namespace='disconnect', retries=3,
                settings={'api_provider': 'custom', 'api_base_url': f'http://127.0.0.1:{server.server_port}'})
        assert resumed.value.code == 'unknown_outcome' and Handler.calls == 1
    finally:
        cache.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_body_timeout_reports_received_bytes_without_body():
    class Response:
        headers = {'Content-Type': 'application/json', 'Content-Length': '100'}
        calls = 0
        def read1(self, maximum):
            self.calls += 1
            if self.calls == 1:
                return b'private-body'
            raise TimeoutError('private exception')
        def read(self, maximum):
            raise TimeoutError('private exception')
    with pytest.raises(api_client.TransportFailure) as caught:
        api_client._read_json(Response())
    assert caught.value.code == 'read_timeout'
    diagnostic = caught.value.diagnostics
    assert diagnostic['phase'] == 'read_body'
    assert diagnostic['received_bytes'] == 12
    assert diagnostic['expected_bytes'] == 100
    assert diagnostic['socket_timeout_seconds'] == 40
    assert diagnostic['elapsed_seconds'] >= 0
    assert 'private' not in json.dumps(diagnostic)


def test_real_http_partial_body_timeout_is_single_and_diagnostic(tmp_path, monkeypatch):
    import time
    import urllib.request
    class Handler(BaseHTTPRequestHandler):
        calls = 0
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            type(self).calls += 1
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'private-body')
            self.wfile.flush()
            time.sleep(.2)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(api_client, 'SOCKET_TIMEOUT_SECONDS', .05)
    cache = Cache(tmp_path / 'timeout.sqlite')
    try:
        with pytest.raises(api_client.ApiRequestError) as caught:
            api_client.request_json(cache, 'synthetic-key', BODY, api_client.strict_json,
                namespace='partial-timeout', retries=3,
                settings={'api_provider': 'custom', 'api_base_url': f'http://127.0.0.1:{server.server_port}'})
        assert Handler.calls == 1
        assert caught.value.code == 'read_timeout'
        assert caught.value.diagnostics['received_bytes'] == 12
        assert caught.value.diagnostics['expected_bytes'] == 100
        assert caught.value.diagnostics['socket_timeout_seconds'] == .05
        assert cache.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0] == 0
        assert 'private' not in str(caught.value) + json.dumps(caught.value.diagnostics)
    finally:
        cache.close()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('body,expected_phase', [(b'not-json', 'parse_json'), (b'x' * 500001, 'read_body')])
def test_invalid_body_has_safe_phase(body, expected_phase):
    import io
    response = io.BytesIO(body)
    response.headers = {}
    with pytest.raises(api_client.TransportFailure) as caught:
        api_client._read_json(response)
    assert caught.value.code == 'invalid_response'
    assert caught.value.diagnostics['phase'] == expected_phase


def test_diagnostic_boundary_discards_sensitive_and_invalid_fields():
    failure = api_client.ApiRequestError('read_timeout', {
        'phase': 'private-data', 'endpoint': 'private-url', 'body': 'private-lyrics',
        'elapsed_seconds': float('nan'), 'received_bytes': -1,
        'expected_bytes': True, 'socket_timeout_seconds': 40,
    })
    assert failure.diagnostics == {'socket_timeout_seconds': 40}


def test_cancelled_return_is_not_cached_or_retried(tmp_path):
    cache = Cache(tmp_path / 'cancel.sqlite')
    cancelled = False
    calls = 0
    def transport(body, key):
        nonlocal cancelled, calls
        calls += 1
        cancelled = True
        return '{}', {}
    try:
        with pytest.raises(InterruptedError):
            api_client.request_json(cache, 'synthetic-key', BODY, api_client.strict_json,
                namespace='cancel', retries=3, transport=transport, cancel=lambda: cancelled)
        assert calls == 1
        assert cache.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0] == 0
        assert cache.db.execute('SELECT status FROM calls').fetchone()[0] == 'cancelled'
    finally:
        cache.close()


def test_fragmented_valid_json_still_requires_complete_envelope():
    import io
    payload = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}).encode()
    class Response(io.BytesIO):
        headers = {'Content-Type': 'application/json', 'Content-Length': str(len(payload))}
        def read1(self, maximum):
            return super().read1(min(maximum, 3))
    assert api_client._read_json(Response(payload))['choices'][0]['message']['content'] == '{}'


def test_review_coverage_preserves_safe_diagnostics(tmp_path):
    from lyricsync.api import suggest
    from lyricsync.model import Project, Lyric, Translation, Mapping
    project = Project(lyrics=[Lyric('l1', 'original', 0, 1)],
                      translations=[Translation('t1', 'translation')],
                      mappings=[Mapping('m1', ['t1'], ['l1'], start=0, end=1)])
    project.settings.update(api_enabled=True, api_retries=3)
    cache = Cache(tmp_path / 'coverage.sqlite')
    diagnostic = dict(phase='read_body', elapsed_seconds=.05, received_bytes=12,
                      expected_bytes=100, socket_timeout_seconds=40)
    def transport(body, key):
        raise api_client.TransportFailure('read_timeout', diagnostic)
    try:
        suggest(project, cache, 'synthetic-key', transport=transport)
        coverage = project.settings['api_coverage']
        assert coverage['error'] == 'read_timeout'
        assert coverage['diagnostics'] == diagnostic
        assert coverage['failed'] == 1 and coverage['processed'] == 0
        assert len(project.mappings) == 1
        message = api_client.diagnostic_message(coverage['diagnostics'])
        assert '12' in message and '100' in message and '40' in message
        assert api_client.diagnostic_message({'phase': 'private-body'}) == ''
    finally:
        cache.close()


def test_cross_line_alignment_retains_safe_diagnostics_without_retry_or_candidate(tmp_path):
    from copy import deepcopy
    from lyricsync.api_alignment import request_plan
    from tests.test_api_migration_alignment import fixture
    project,index=fixture();project.settings["api_retries"]=3
    before=deepcopy(project.to_dict());calls=[]
    diagnostic=dict(phase="read_body",elapsed_seconds=40,received_bytes=12,
                    expected_bytes=100,socket_timeout_seconds=40,body="private-lyrics",key="private-secret")
    def transport(body,key):
        calls.append(1)
        raise api_client.TransportFailure("read_timeout",diagnostic)
    cache=Cache(tmp_path/"alignment.sqlite")
    try:
        result=request_plan(project,index,cache,"synthetic-key",transport=transport)
        assert calls==[1]
        assert result["status"]=="failed" and result["code"]=="read_timeout"
        assert result["diagnostics"]["phase"]=="read_body"
        assert result["diagnostics"]["received_bytes"]==12
        assert "诊断：" in result["message"] and "40" in result["message"]
        assert "private-" not in json.dumps(result)
        assert "plan" not in result
        after=project.to_dict();after["api_records"]=before["api_records"]
        assert after==before
        assert len(project.api_records)==1
    finally:cache.close()
