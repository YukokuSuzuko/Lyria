"""Adversarial independent-review regressions, synthetic keys and local SQLite only."""
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from lyricsync import api_client, ai_config, api_alignment
from lyricsync.storage import Cache
from tests.test_api_phase5_stream import BODY
from tests.test_api_phase5_windows import fixture, split_first


@pytest.mark.parametrize('key', ['abc', 'k', 'synthetic-long-key', 'synthetic\\escaped"key'])
@pytest.mark.parametrize('shape', ['value', 'nested', 'dict_key'])
def test_decoded_credential_echo_is_never_cached_or_retried(tmp_path, key, shape):
    value = {'reason': key} if shape == 'value' else ({'nested': [{'reason': key}]} if shape == 'nested' else {key: 'ordinary'})
    cache = Cache(tmp_path/'echo.sqlite'); calls = []
    try:
        for expected in ('credential_echo', 'unknown_outcome'):
            with pytest.raises(api_client.ApiRequestError) as caught:
                api_client.request_json(cache, key, BODY, api_client.strict_json, namespace='echo',
                    transport=lambda *_: (calls.append(1), (json.dumps(value), {}))[1])
            assert caught.value.code == expected
        assert calls == [1] and len(cache.records()) == 1
        assert cache.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0] == 0
        assert key not in json.dumps(cache.records())
    finally: cache.close()


@pytest.mark.parametrize('key', ['k', 'abc'])
def test_short_key_substrings_do_not_reject_ordinary_text(tmp_path, key):
    cache = Cache(tmp_path/'ordinary.sqlite')
    try:
        value = {'reason': 'Keep normal remarks and abcdef text.'}
        result = api_client.request_json(cache, key, BODY, api_client.strict_json,
            namespace='ordinary', transport=lambda *_: (value, {}))
        assert result['value'] == value
    finally: cache.close()


@pytest.mark.parametrize('key', ['abc', 'synthetic\\escaped"key'])
def test_unsafe_old_cache_is_quarantined_without_post(tmp_path, key):
    cache = Cache(tmp_path/'old-cache.sqlite')
    hashed = Cache.key('old', {'endpoint': ai_config.base_url({}), 'body': ai_config.adapt_chat_body(BODY)})
    cache.put(hashed, {'reason': key})
    try:
        for _ in range(2):
            with pytest.raises(api_client.ApiRequestError) as caught:
                api_client.request_json(cache, key, BODY, api_client.strict_json, namespace='old',
                    transport=lambda *_: pytest.fail('Unsafe cache cannot authorize a POST'))
            assert caught.value.code == 'credential_echo'
        assert cache.get(hashed) is None and cache.records() == []
        assert api_client.response_quarantined(cache, hashed)
    finally: cache.close()


@pytest.mark.parametrize('mode', ['fresh', 'completed', 'requesting'])
@pytest.mark.parametrize('key', ['abc', 'synthetic\\escaped"key'])
def test_alignment_fresh_and_recovered_journal_cannot_expose_credential(tmp_path, mode, key):
    project, index = fixture(2); cache = Cache(tmp_path/'alignment-echo.sqlite'); calls = []
    def provider(body, _):
        value = split_first(body)
        value['operations'][0]['reason'] = key if mode == 'fresh' else 'Ordinary explanation.'
        calls.append(1); return json.dumps(value, ensure_ascii=False), {}
    try:
        result = api_alignment.request_plan(project, index, cache, key, transport=provider)
        if mode != 'fresh':
            assert result['status'] == 'proposed'
            for cache_key, body in cache.db.execute('SELECT key,body FROM cache').fetchall():
                value = json.loads(body)
                if cache_key.startswith('pairing-alignment-run-'):
                    entry = value['windows'][0]
                    if mode == 'requesting': entry.clear(); entry.update(status='requesting')
                    else: entry['value']['operations'][0]['reason'] = key
                else: value['operations'][0]['reason'] = key
                cache.put(cache_key, value)
            result = api_alignment.request_plan(project, index, cache, key,
                transport=lambda *_: pytest.fail('Recovery must never POST'))
        assert result['status'] == 'failed' and result['code'] == 'credential_echo'
        assert 'plan' not in result and calls == [1]
        for _, body in cache.db.execute('SELECT key,body FROM cache'):
            assert not api_client.contains_credential(json.loads(body), key)
        assert len(cache.records()) == 1
    finally: cache.close()


@pytest.mark.parametrize('phase', ['claim', 'reserve', 'renew', 'publish'])
def test_sqlite_busy_wait_cancel_does_not_acquire_or_publish_after_cancel(tmp_path, monkeypatch, phase):
    path = tmp_path/'busy.sqlite'; locker = Cache(path)
    with api_client._claim(locker, 'init', lambda: False): pass
    ready = threading.Event(); proceed = threading.Event(); stop = threading.Event(); reached = []
    if phase == 'publish':
        original = api_client.RequestLease.renew; counts = []
        def renew(lease):
            original(lease); counts.append(1)
            if len(counts) == 4: ready.set(); assert proceed.wait(3)
        monkeypatch.setattr(api_client.RequestLease, 'renew', renew)
    def worker():
        cache = Cache(path)
        try:
            if phase == 'publish':
                return api_client.request_json(cache, 'synthetic-key', BODY, api_client.strict_json,
                    namespace='busy', cancel=stop.is_set, transport=lambda *_: ('{}', {}))
            if phase == 'renew':
                with api_client._claim(cache, 'lease', stop.is_set) as lease:
                    ready.set(); assert proceed.wait(3); lease.renew(); reached.append('renewed')
            else:
                ready.set(); assert proceed.wait(3)
                if phase == 'claim':
                    with api_client._claim(cache, 'lease', stop.is_set): reached.append('acquired')
                else: api_client._reserve_attempt(cache, 'test', 50, 0, 4, stop.is_set); reached.append('reserved')
        finally:
            assert cache.db.execute('PRAGMA busy_timeout').fetchone()[0] == 30000
            cache.close()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker); assert ready.wait(3)
            locker.db.execute('BEGIN IMMEDIATE'); proceed.set(); time.sleep(.08); stop.set()
            start = time.monotonic()
            try:
                with pytest.raises(InterruptedError): future.result(timeout=1.5)
                assert time.monotonic()-start < .8 and reached == []
            finally: locker.db.rollback()
        assert locker.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0] == 0
        records = locker.records()
        if phase == 'publish': assert len(records) == 1 and records[0]['status'] == 'pending'
        else: assert records == []
    finally: proceed.set(); stop.set(); locker.close()


def test_db_busy_wait_is_bounded_and_nonbusy_errors_propagate(tmp_path):
    first = Cache(tmp_path/'bounded.sqlite'); second = Cache(tmp_path/'bounded.sqlite')
    try:
        first.db.execute('BEGIN IMMEDIATE'); started = time.monotonic()
        with pytest.raises(api_client.TransportFailure) as caught:
            api_client._db_execute(second, 'BEGIN IMMEDIATE', wait_seconds=.06)
        assert caught.value.code == 'local_storage_busy' and time.monotonic()-started < .3
        assert second.db.execute('PRAGMA busy_timeout').fetchone()[0] == 30000
        first.db.rollback()
        with pytest.raises(sqlite3.OperationalError): api_client._db_execute(second, 'SELECT * FROM nonexistent_table')
    finally: first.close(); second.close()


def test_generic_timeout_restart_is_unknown_and_never_replayed(tmp_path):
    cache = Cache(tmp_path/'generic-timeout.sqlite'); calls = []
    def fail(*_): calls.append(1); raise TimeoutError('synthetic only')
    try:
        for code in ('timeout', 'unknown_outcome'):
            with pytest.raises(api_client.ApiRequestError) as caught:
                api_client.request_json(cache, 'synthetic-key', BODY, api_client.strict_json,
                    namespace='generic-timeout', transport=fail)
            assert caught.value.code == code
        assert calls == [1] and len(cache.records()) == 1
    finally: cache.close()
