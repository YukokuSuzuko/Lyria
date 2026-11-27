from tests.ai_pairing_helpers import pairing_reply, pairing_plan_reply
"""Deterministic review scope, terminal status and resume regressions. No network."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError

from lyricsync.api import suggest, review_signature
from lyricsync.model import Project, Lyric, Translation, Mapping, Visual
from lyricsync.storage import Cache


class ReviewScopingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.cache = Cache(Path(self.folder.name) / 'review.sqlite')
        self.project = Project(
            lyrics=[Lyric(f'l{i}', f'original {i}', i, i+1) for i in range(60)],
            translations=[Translation(f't{i}', f'中文{i}') for i in range(60)],
            visuals=[Visual(f'v{i}', f'OCR {i}', i, i+1, [0,0,1,1], .9, 2) for i in range(60)],
            mappings=[Mapping(f'm{i}', [f't{i}'], [f'l{i}'], [f'v{i}'], start=i, end=i+1) for i in range(60)])
        self.project.settings.update(api_enabled=True, api_retries=0, api_max_calls=50)
        self.calls = []

    def tearDown(self):
        self.cache.close()
        self.folder.cleanup()

    def transport(self, body, key):
        self.calls.append(deepcopy(body))
        return pairing_reply(body), {}

    def run_review(self, **kwargs):
        return suggest(self.project, self.cache, 'fake-not-a-credential',
                       transport=kwargs.pop('transport', self.transport), **kwargs)

    def old_results(self):
        rows = [{'mapping_id': m.id, 'status': 'processed',
                 'context_signature': review_signature(self.project, m),
                 'suggestion': {'mappings': [], 'corrections': []}}
                for m in self.project.mappings]
        self.project.settings['api_suggestions'] = deepcopy(rows)
        return rows

    def test_single_row_uses_one_request_and_only_two_context_neighbors(self):
        old = self.old_results()
        snapshot = deepcopy(self.project.mappings)
        result = self.run_review(mapping_ids=['m30'])
        self.assertEqual(len(self.calls), 1)
        payload = json.loads(self.calls[0]['messages'][1]['content'])
        for name in ('lyrics', 'current_pairings'):
            self.assertEqual(len(payload[name]), 3)
        self.assertEqual([r['id'] for r in payload['lyrics']], ['l29', 'l30', 'l31'])
        self.assertEqual([r['context_only'] for r in payload['current_pairings']], [True,False,True])
        self.assertEqual(result[:30], old[:30])
        self.assertEqual(result[31:], old[31:])
        self.assertEqual(self.project.mappings, snapshot)
        coverage = self.project.settings['api_coverage']
        self.assertEqual(coverage['scope_mapping_ids'], ['m30'])
        self.assertEqual(coverage['processed'], 1)
        self.assertEqual(coverage['status'], 'completed')

    def test_import_switch_only_gates_legacy_not_explicit_manual(self):
        self.project.settings['api_enabled'] = False
        self.assertEqual(self.run_review(mapping_ids=['m1']), [])
        self.assertEqual(self.calls, [])
        self.run_review(mapping_ids=['m1'], manual=True)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.project.settings['api_enabled'])

    def test_full_progress_reaches_ten_of_ten_and_preserves_cache(self):
        progress = []
        self.run_review(progress=lambda fraction,message: progress.append((fraction,message)))
        self.assertEqual(len(self.calls), 10)
        self.assertEqual(progress[0][0], 0)
        self.assertEqual(progress[-1][0], 1)
        self.assertIn('10/10', progress[-1][1])
        self.assertEqual(self.project.settings['api_coverage']['status'], 'completed')
        self.run_review()
        self.assertEqual(len(self.calls), 10)

    def test_auth_and_configuration_errors_stop_after_first_batch(self):
        for status in (401,403,404):
            with self.subTest(status=status):
                calls = []
                def denied(body, key):
                    calls.append(body)
                    raise HTTPError('https://provider.invalid', status, 'private body', {}, None)
                self.project.settings['api_retries'] = 3
                self.run_review(transport=denied)
                self.assertEqual(len(calls), 1)
                coverage = self.project.settings['api_coverage']
                self.assertEqual(coverage['status'], 'failed')
                self.assertEqual(coverage['failed'], 6)
                self.assertEqual(coverage['pending'], 54)
                self.assertNotIn('private body', str(self.project.settings))

    def test_retry_exhaustion_stops_song_and_reports_attempts(self):
        self.project.settings['api_retries'] = 3
        calls = []
        updates = []
        def unavailable(body,key):
            calls.append(body)
            raise HTTPError('https://provider.invalid', 503, 'unavailable', {}, None)
        self.run_review(transport=unavailable,
                        progress=lambda fraction,message: updates.append((fraction,message)))
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.project.settings['api_coverage']['pending'], 54)
        self.assertEqual(self.project.settings['api_coverage']['status'], 'failed')
        self.assertTrue(any('尝试 4/4' in message for _,message in updates))
        self.assertEqual(len(self.project.api_records), 4)

    def test_context_limit_mixed_with_success_is_partial(self):
        self.project.mappings[0].text_override = '大' * 24001
        self.run_review()
        coverage = self.project.settings['api_coverage']
        self.assertEqual(coverage['status'], 'partial')
        self.assertTrue(coverage['partial'])
        self.assertEqual((coverage['processed'],coverage['failed'],coverage['pending']), (54,6,0))
        self.assertEqual(len(self.calls), 9)

    def test_error_after_success_keeps_completed_and_stops_remaining(self):
        calls = []
        def transport(body,key):
            calls.append(body)
            if len(calls) == 2:
                raise HTTPError('https://provider.invalid', 401, 'denied', {}, None)
            return pairing_reply(body), {}
        self.run_review(transport=transport)
        coverage = self.project.settings['api_coverage']
        self.assertEqual((coverage['processed'],coverage['failed'],coverage['pending']), (6,6,48))
        self.assertEqual(coverage['status'], 'failed')
        self.assertTrue(coverage['partial'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.project.settings['api_suggestions'][0]['status'], 'processed')

    def test_partial_cancel_keeps_completed_and_historical_pending(self):
        old = self.old_results()
        snapshots = []
        def checkpoint():
            snapshots.append(deepcopy(self.project.settings['api_coverage']))
        with self.assertRaises(InterruptedError):
            self.run_review(cancel=lambda: bool(snapshots), checkpoint=checkpoint)
        coverage = self.project.settings['api_coverage']
        self.assertEqual(coverage['status'], 'cancelled')
        self.assertTrue(coverage['partial'])
        self.assertEqual((coverage['processed'], coverage['pending']), (6,54))
        results = self.project.settings['api_suggestions']
        self.assertEqual(results[0]['status'], 'processed')
        self.assertEqual(results[6]['status'], 'pending')
        self.assertEqual(results[6]['previous_suggestion']['suggestion'], old[6]['suggestion'])
        self.assertEqual(snapshots[-1]['status'], 'cancelled')
        self.assertEqual(len(self.calls), 1)

    def test_budget_stops_pending_then_scoped_resume_keeps_completed(self):
        self.project.settings['api_max_calls'] = 1
        self.run_review()
        coverage = self.project.settings['api_coverage']
        self.assertEqual((coverage['status'], coverage['processed'], coverage['failed'], coverage['pending']),
                         ('budget_exhausted',6,0,54))
        old = deepcopy(self.project.settings['api_suggestions'][:6])
        pending = self.project.settings['api_resume']['pending_mapping_ids']
        self.run_review(mapping_ids=pending, temporary_maximum=100)
        self.assertEqual(self.project.settings['api_suggestions'][:6], old)
        self.assertEqual(self.project.settings['api_coverage']['status'], 'completed')
        self.assertEqual(len(self.calls), 10)
        self.assertNotIn('api_resume', self.project.settings)
        self.assertEqual(self.project.settings['api_max_calls'], 1)

    def test_cancel_during_request_checkpoints_spent_ledger_and_pending(self):
        def cancelled(body,key):
            raise InterruptedError('cancelled in transport')
        with self.assertRaises(InterruptedError):
            self.run_review(mapping_ids=['m3'], transport=cancelled)
        self.assertEqual(self.project.settings['api_coverage']['status'], 'cancelled')
        self.assertEqual(self.project.settings['api_suggestions'][0]['status'], 'pending')
        self.assertEqual(len(self.project.api_records), 1)

    def test_invalid_scope_fails_without_mutation_or_request(self):
        before = deepcopy(self.project.settings)
        with self.assertRaises(ValueError):
            self.run_review(mapping_ids=['not-a-row'])
        self.assertEqual(self.project.settings, before)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
