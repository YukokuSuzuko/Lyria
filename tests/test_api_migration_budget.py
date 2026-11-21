"""Mocked provider transport, persistent budgets, retries and concurrent de-dupe."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from lyricsync import ai_config
from lyricsync.api import fetch_models, suggest, test_connection as probe, translate_reference, review_signature
from lyricsync.api_client import (ApiRequestError, BudgetLimit, NoRedirect, request,
                                 request_json, request_models, strict_json)
from lyricsync.storage import Cache
from lyricsync.model import Project, Lyric, Translation, Mapping


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'cache.sqlite'
        self.cache = Cache(self.path)
        self.body = {'model': 'mock-model', 'messages': [{'role': 'user', 'content': 'text only'}]}

    def tearDown(self):
        self.cache.close()
        self.directory.cleanup()

    def run_request(self, **kwargs):
        return request_json(self.cache, 'mock-secret-123', self.body, strict_json,
                            namespace='test-request', **kwargs)

    def test_defaults_three_failures_then_fourth_success_counts_all(self):
        attempts = []
        def transport(body, key):
            attempts.append(1)
            if len(attempts) <= 3:
                raise HTTPError('https://mock.invalid',503,'synthetic transient',{},None)
            return '{"ok":true}', {'total_tokens': 4, 'mock-secret-123': 7}
        result = self.run_request(transport=transport)
        self.assertTrue(result['value']['ok'])
        self.assertEqual(len(attempts), 4)
        self.assertEqual([r['status'] for r in self.cache.records()], ['server_error'] * 3 + ['success'])
        self.assertNotIn('mock-secret-123', str(self.cache.records()))

    def test_configured_five_retries_are_counted_and_bounded(self):
        attempts=[]
        def transport(body,key):
            attempts.append(1)
            if len(attempts)<=5:raise HTTPError('https://mock.invalid',503,'synthetic transient',{},None)
            return '{"ok":true}',{}
        result=self.run_request(retries=5,maximum=20,transport=transport)
        self.assertTrue(result['value']['ok'])
        self.assertEqual(len(attempts),6)
        self.assertEqual(len(self.cache.records()),6)

    def test_unknown_network_failure_stops_once_and_error_is_sanitized(self):
        with self.assertRaises(ApiRequestError) as ctx:
            self.run_request(transport=lambda *a: (_ for _ in ()).throw(OSError('mock-secret-123')))
        self.assertEqual(len(self.cache.records()), 1)
        self.assertNotIn('mock-secret-123', str(ctx.exception))
        self.assertEqual(ctx.exception.code, 'network_error')

    def test_invalid_output_stops_after_one_counted_attempt(self):
        with self.assertRaises(ApiRequestError) as ctx:
            self.run_request(transport=lambda *a: ('{bad JSON', {}))
        self.assertEqual(ctx.exception.code, 'invalid_response')
        self.assertEqual(len(self.cache.records()), 1)

    def test_auth_failure_does_not_retry_and_drops_response(self):
        def bad(*a):
            raise HTTPError('https://custom.invalid', 401, 'mock-secret-123', {}, io.BytesIO(b'mock-secret-123'))
        with self.assertRaises(ApiRequestError) as ctx:
            self.run_request(transport=bad)
        self.assertEqual(len(self.cache.records()), 1)
        self.assertEqual(ctx.exception.code, 'authentication_failed')
        self.assertNotIn('mock-secret-123', str(ctx.exception))

    def test_budget_mid_retry_has_structured_resume_and_preserves_attempts(self):
        with self.assertRaises(BudgetLimit) as ctx:
            self.run_request(maximum=3, transport=lambda *a: (_ for _ in ()).throw(HTTPError('https://mock.invalid',503,'synthetic transient',{},None)))
        state = ctx.exception.state
        self.assertEqual((state['used'], state['limit']), (3, 3))
        self.assertEqual(state['status'], 'budget_exhausted')
        self.assertIn({'action': 'continue_once', 'maximum': 100}, state['choices'])
        self.assertNotIn('mock-secret-123', str(state))
        self.assertEqual(len(self.cache.records()), 3)

    def test_temporary100_resume_does_not_reset_three_retry_allowance(self):
        attempts = []
        def failure(*a):
            attempts.append(1)
            raise HTTPError('https://mock.invalid',503,'synthetic transient',{},None)
        with self.assertRaises(BudgetLimit) as ctx:
            self.run_request(maximum=2, transport=failure)
        self.assertEqual(ctx.exception.state['attempts_spent'], 2)
        self.assertEqual(ctx.exception.state['remaining_attempts'], 2)
        self.cache.close()
        self.cache = Cache(self.path)
        with self.assertRaises(ApiRequestError):
            self.run_request(maximum=100, transport=failure)
        self.assertEqual(len(attempts), 4)
        self.assertEqual(len(self.cache.records()), 4)

    def test_crash_pending_attempt_is_counted_and_not_blindly_replayed(self):
        class SimulatedCrash(BaseException):
            pass
        with self.assertRaises(SimulatedCrash):
            self.run_request(transport=lambda *a: (_ for _ in ()).throw(SimulatedCrash()))
        self.assertEqual(self.cache.records()[0]['status'], 'pending')
        self.cache.close()
        self.cache = Cache(self.path)
        with self.assertRaises(ApiRequestError):
            self.run_request(transport=lambda *a: self.fail('pending result cannot be blindly resent'))
        self.assertEqual(len(self.cache.records()), 1)

    def test_success_cache_no_duplicate_even_when_budget_is_exhausted(self):
        calls = []
        def transport(*a):
            calls.append(1); return '{"ok":true}', {}
        self.run_request(maximum=1, transport=transport)
        second = self.run_request(maximum=0, transport=transport)
        self.assertTrue(second['cached'])
        self.assertEqual(calls, [1])

    def test_default_50_then_explicit_once100_does_not_reset_budget_or_defaults(self):
        for i in range(50):
            call = self.cache.reserve_call('existing-' + str(i), 50)
            self.cache.finish_call(call, 'success')
        with self.assertRaises(BudgetLimit) as ctx:
            self.run_request(transport=lambda *a: ('{}', {}))
        self.assertEqual(ctx.exception.state['limit'], 50)
        self.run_request(maximum=100, transport=lambda *a: ('{}', {}))
        self.assertEqual(len(self.cache.records()), 51)
        with self.assertRaises(BudgetLimit):
            request_json(self.cache, 'mock-secret-123', {**self.body, 'model': 'another'}, strict_json,
                         namespace='new', transport=lambda *a: ('{}', {}))
        self.assertEqual(ai_config.DEFAULTS['api_max_calls'], 50)

    def test_at100_only_later_no_unbounded_raise(self):
        for i in range(100):
            self.cache.reserve_call(str(i), 100)
        with self.assertRaises(BudgetLimit) as ctx:
            self.run_request(maximum=100, transport=lambda *a: self.fail('must not call'))
        self.assertEqual(ctx.exception.state['choices'], [{'action': 'later'}])
        with self.assertRaises(ValueError):
            self.run_request(maximum=101, transport=lambda *a: self.fail())

    def test_budget_and_cache_survive_reopen(self):
        self.run_request(maximum=1, transport=lambda *a: ('{"ok":true}', {}))
        self.cache.close()
        self.cache = Cache(self.path)
        self.assertEqual(len(self.cache.records()), 1)
        self.assertTrue(self.run_request(maximum=1, transport=lambda *a: self.fail())['cached'])
        with self.assertRaises(BudgetLimit):
            request_json(self.cache, 'mock', {}, strict_json, namespace='other', maximum=1,
                         transport=lambda *a: self.fail())

    def test_different_endpoints_never_share_cache(self):
        first = self.run_request(transport=lambda *a: ('{}', {}))
        config = dict(ai_config.DEFAULTS, api_provider='custom', api_base_url='https://custom.invalid/v1')
        second = self.run_request(settings=config, transport=lambda *a: ('{}', {}))
        self.assertNotEqual(first['request_hash'], second['request_hash'])
        self.assertFalse(second['cached'])
        self.assertEqual(len(self.cache.records()), 2)

    def test_same_request_concurrent_connections_only_one_provider_call(self):
        started = threading.Barrier(6)
        lock = threading.Lock()
        calls = []
        def transport(*a):
            with lock:
                calls.append(1)
            time.sleep(.08)
            return '{"ok":true}', {}
        def worker(_):
            cache = Cache(self.path)
            try:
                started.wait(timeout=5)
                return request_json(cache, 'mock', self.body, strict_json, namespace='same', transport=transport)
            finally:
                cache.close()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(worker, range(6)))
        self.assertEqual(calls, [1])
        self.assertEqual(sum(r['cached'] for r in results), 5)
        self.assertEqual(len(self.cache.records()), 1)

    def test_different_concurrent_requests_never_exceed_shared_budget(self):
        started = threading.Barrier(8)
        def worker(n):
            cache = Cache(self.path)
            try:
                started.wait(timeout=5)
                try:
                    request_json(cache, 'mock', {'number': n}, strict_json, namespace='distinct',
                                 maximum=3, transport=lambda *a: ('{}', {}))
                    return 'success'
                except BudgetLimit:
                    return 'budget'
            finally:
                cache.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(worker, range(8)))
        self.assertEqual(results.count('success'), 3)
        self.assertEqual(results.count('budget'), 5)
        self.assertEqual(len(self.cache.records()), 3)

    def test_cancel_before_network_spends_no_attempt(self):
        with self.assertRaises(InterruptedError):
            self.run_request(cancel=lambda: True, transport=lambda *a: self.fail())
        self.assertFalse(self.cache.records())

    def test_echoed_credential_never_cached_or_logged(self):
        with self.assertRaises(ApiRequestError):
            self.run_request(retries=0, transport=lambda *a: ('{"meaning":"mock-secret-123"}', {}))
        self.assertNotIn('mock-secret-123', str(self.cache.records()))
        bodies = self.cache.db.execute('SELECT body FROM cache').fetchall()
        self.assertEqual(bodies, [])

    def test_numeric_usage_is_allowlisted_finite_and_nonnegative(self):
        self.run_request(transport=lambda *a: ('{}', {'total_tokens': float('nan'), 'completion_tokens': -1,
                                                     'prompt_tokens': True, 'unknown': 1}))
        self.assertEqual(json.loads(self.cache.records()[0]['usage']), {})

    def test_reference_kept_separate_cached_and_id_checked(self):
        def good(*a):
            return '{"line_id":"line","translation":"仅供参考","note":""}', {}
        first = translate_reference('lyrics', 'line', self.cache, 'mock', transport=good)
        second = translate_reference('lyrics', 'line', self.cache, 'mock', transport=lambda *a: self.fail())
        self.assertEqual(first, second)
        with self.assertRaises(ApiRequestError):
            translate_reference('different', 'other', self.cache, 'mock', retries=0, transport=good)

    def test_models_are_real_ids_cached_manual_fallback_always_present(self):
        result = fetch_models(self.cache, 'mock', transport=lambda *a: (
            {'data': [{'id': 'my-model'}, {'id': 'reasoning-x'}, {'id': 'my-model'}]}, {}))
        self.assertEqual(result['models'], ['my-model', 'reasoning-x'])
        self.assertTrue(result['manual_entry'])
        again = fetch_models(self.cache, 'mock', transport=lambda *a: self.fail())
        self.assertTrue(again['cached'])

    def test_unsupported_model_list_offers_manual_id_not_fake_models(self):
        def unsupported(*a):
            raise HTTPError('https://custom.invalid/models', 404, 'SECRET', {}, None)
        result = fetch_models(self.cache, 'mock', transport=unsupported)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['models'], [])
        self.assertTrue(result['manual_entry'])
        self.assertNotIn('SECRET', str(result))

    def test_connection_test_counted_but_not_silently_cached(self):
        transport = Mock(return_value=('{"ok":true}', {}))
        self.assertTrue(probe(self.cache, 'mock', transport=transport)['ok'])
        self.assertTrue(probe(self.cache, 'mock', transport=transport)['ok'])
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(len(self.cache.records()), 2)

    def test_pairing_result_keeps_pre_request_signature_after_user_edit(self):
        p = Project(); p.settings.update(api_enabled=True, api_retries=0)
        p.translations = [Translation('t', '原中文')]
        p.lyrics = [Lyric('l', 'original', 0, 1)]
        p.mappings = [Mapping('m', ['t'], ['l'], start=0, end=1)]
        signature = review_signature(p, p.mappings[0])
        def transport(*a):
            p.mappings[0].text_override = '较新用户中文'
            return '{"reviews":[{"mapping_id":"m","verdict":"uncertain","reason":"Insufficient evidence."}]}', {}
        result = suggest(p, self.cache, 'mock', transport=transport)
        self.assertEqual(result[0]['context_signature'], signature)
        self.assertNotEqual(result[0]['context_signature'], review_signature(p, p.mappings[0]))
        self.assertEqual(result[0]['suggestion']['verdict'], 'uncertain')
        self.assertEqual(p.mappings[0].text_override, '较新用户中文')

    def test_pairing_signature_catches_time_change(self):
        p = Project(); p.translations = [Translation('t', '中文')]
        p.mappings = [Mapping('m', ['t'], start=0, end=1)]
        signature = review_signature(p, p.mappings[0])
        p.mappings[0].end = 2
        self.assertNotEqual(signature, review_signature(p, p.mappings[0]))


class SettingsAndTransportTests(unittest.TestCase):
    def test_defaults_and_only_official_or_custom_presets(self):
        self.assertEqual(ai_config.DEFAULTS['api_max_calls'], 50)
        self.assertEqual(ai_config.DEFAULTS['api_retries'], 3)
        self.assertEqual([p['id'] for p in ai_config.PROVIDERS], ['deepseek', 'custom'])
        with self.assertRaises(ValueError):
            ai_config.normalized({'api_provider': 'unrequested-built-in'})

    def test_settings_strip_credentials_and_retired_image_fields(self):
        with tempfile.TemporaryDirectory() as d:
            config = dict(ai_config.DEFAULTS, api_provider='custom', api_provider_name='My server',
                api_base_url='https://mine.invalid/v1/', api_model='user-owned-id', api_key='SECRET',
                image_api_enabled=True, image_api_key='SECRET-2', image_model='vision')
            saved = ai_config.save(d, config)
            text = (Path(d) / 'ai_settings.json').read_text()
            self.assertNotIn('SECRET', text)
            self.assertNotIn('image', text)
            self.assertEqual(saved['api_base_url'], 'https://mine.invalid/v1')
            self.assertEqual(ai_config.load(d), saved)
            with self.assertRaises(ValueError):
                ai_config.save(d, {**saved, 'api_max_calls': 101})

    def test_custom_url_credential_query_redirect_endpoint_rejected(self):
        for value in ['https://user:password@mine.invalid', 'https://mine.invalid?key=SECRET',
                      'https://mine.invalid#fragment', 'http://remote.invalid', 'file:///tmp/private',
                      'https://mine.invalid/chat/completions', 'https://mine.invalid/models',
                      'https://mine.invalid:INVALID', 'https://mine.invalid/\nheader']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ai_config.normalized({'api_provider': 'custom', 'api_base_url': value})

    def test_local_http_allowed_and_official_preset_cannot_be_redirected(self):
        custom = ai_config.normalized({'api_provider': 'custom', 'api_base_url': 'http://127.0.0.1:8080/v1'})
        self.assertEqual(ai_config.credential_scope(custom), 'http://127.0.0.1:8080/v1/chat/completions')
        official = ai_config.normalized({'api_base_url': 'https://attacker.invalid'})
        self.assertEqual(ai_config.credential_scope(official), 'https://api.deepseek.com/chat/completions')

    def test_delete_uses_keystore_only_and_never_reads_key(self):
        store = Mock()
        config = ai_config.normalized({'api_provider': 'custom', 'api_base_url': 'https://mine.invalid/v1'})
        with tempfile.TemporaryDirectory() as d:
            reset = ai_config.delete_provider(d, config, store)
        store.delete.assert_called_once_with('https://mine.invalid/v1/chat/completions')
        store.get.assert_not_called()
        self.assertEqual(reset, ai_config.DEFAULTS)

    def test_custom_chat_and_models_routes_with_no_media(self):
        config = {'api_provider': 'custom', 'api_base_url': 'https://mine.invalid/v1'}
        reply = Mock()
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        reply.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}).encode()
        opener = Mock(); opener.open.return_value = reply
        body = {'model': 'mine', 'messages': [{'role': 'user', 'content': 'text'}]}
        with patch('lyricsync.api_client.urllib.request.build_opener', return_value=opener):
            request(body, 'mock-secret-123', config)
            sent = opener.open.call_args.args[0]
            self.assertEqual(sent.full_url, 'https://mine.invalid/v1/chat/completions')
            self.assertEqual(json.loads(sent.data), {**body, 'stream': False})
            reply.read.return_value = b'{"data":[]}'
            request_models({}, 'mock-secret-123', config)
            sent = opener.open.call_args.args[0]
            self.assertEqual(sent.full_url, 'https://mine.invalid/v1/models')
            self.assertIsNone(sent.data)
            self.assertEqual(sent.method, 'GET')

    def test_builtin_transport_rejects_image_tool_or_upload_surface(self):
        cases = [
            {'model': 'm', 'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': 'data:media'}]}]},
            {'model': 'm', 'messages': [{'role': 'user', 'content': 'text'}], 'tools': []},
            {'model': 'm', 'messages': [{'role': 'user', 'content': 'text', 'image': 'x'}]},
        ]
        with patch('lyricsync.api_client.urllib.request.build_opener') as opener:
            for body in cases:
                with self.subTest(body=body), self.assertRaises(ValueError):
                    request(body, 'mock')
            opener.assert_not_called()
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None)


if __name__ == '__main__':
    unittest.main()
