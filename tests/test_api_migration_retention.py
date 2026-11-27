"""A stopped re-review retains old evidence without claiming current completion."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from lyricsync.api import suggest, format_review_suggestions, review_signature
from lyricsync.model import Project, Lyric, Translation, Mapping
from lyricsync.storage import Cache


class ReviewRetentionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = Cache(Path(self.directory.name) / 'review.sqlite')
        self.project = Project(lyrics=[Lyric('l', 'source', 0, 1)],
                               translations=[Translation('t', '可靠中文')],
                               mappings=[Mapping('m', ['t'], ['l'], start=0, end=1)])
        self.project.settings.update(api_enabled=True, api_max_calls=50, api_retries=0)
        self.old = {'mapping_id': 'm', 'status': 'processed',
                    'context_signature': review_signature(self.project, self.project.mappings[0]),
                    'suggestion': {'mappings': [{'lyric_ids': ['l'], 'mapping_ids': ['m'],
                        'translation_ids': ['t'], 'current_chinese': ['可靠中文'],
                        'reason': '旧的可靠依据', 'reference_meaning': '旧参考译意'}], 'corrections': []}}
        self.project.settings['api_suggestions'] = [deepcopy(self.old)]

    def tearDown(self):
        self.cache.close()
        self.directory.cleanup()

    def cancel_review(self):
        with self.assertRaises(InterruptedError):
            suggest(self.project, self.cache, 'unit-test-only', cancel=lambda: True,
                    transport=lambda *_: self.fail('cancelled call must not reach provider'))
        return self.project.settings['api_suggestions'][0]

    def test_immediate_cancel_preserves_evidence_without_current_completion(self):
        old_reference = self.project.settings['api_suggestions'][0]
        result = self.cancel_review()
        self.assertEqual(result['status'], 'pending')
        self.assertNotIn('suggestion', result)
        previous = result['previous_suggestion']
        self.assertTrue(previous['historical'])
        self.assertEqual(previous['suggestion'], self.old['suggestion'])
        self.assertEqual(previous['context_signature'], self.old['context_signature'])
        self.assertEqual(old_reference, self.old)
        self.assertIsNot(previous['suggestion'], old_reference['suggestion'])
        self.assertEqual(self.project.settings['api_coverage']['processed'], 0)
        body = format_review_suggestions(self.project, [result])
        for value in ('尚未核对', '不确定'):
            self.assertIn(value, body)
        self.assertEqual(self.cache.records(), [])

    def test_failed_provider_keeps_history_but_counts_failed_not_processed(self):
        result = suggest(self.project, self.cache, 'unit-test-only', transport=lambda *_: ('bad json', {}))[0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['previous_suggestion']['suggestion'], self.old['suggestion'])
        self.assertEqual(self.project.settings['api_coverage']['failed'], 1)
        self.assertEqual(self.project.settings['api_coverage']['processed'], 0)
        self.assertEqual(len(self.cache.records()), 1)

    def test_budget_exhaustion_preserves_history_and_resumable_status(self):
        self.project.settings['api_max_calls'] = 0
        result = suggest(self.project, self.cache, 'unit-test-only',
                         transport=lambda *_: self.fail('no remaining budget'))[0]
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['previous_suggestion']['suggestion'], self.old['suggestion'])
        self.assertEqual(self.project.settings['api_resume']['status'], 'budget_exhausted')
        self.assertEqual(self.cache.records(), [])

    def test_multiple_cancelled_reruns_keep_one_bounded_historical_snapshot(self):
        for _ in range(8):
            previous = self.cancel_review()['previous_suggestion']
            self.assertNotIn('previous_suggestion', previous)
            self.assertEqual(previous['suggestion'], self.old['suggestion'])
        self.assertEqual(str(self.project.settings['api_suggestions']).count('旧参考译意'), 1)

    def test_changed_text_marks_history_stale_without_modifying_new_text(self):
        self.project.mappings[0].text_override = '最新人工中文'
        result = self.cancel_review()
        body = format_review_suggestions(self.project, [result])
        self.assertIn('尚未核对', body)
        self.assertNotIn('旧参考译意', body)
        self.assertIn('不确定', body)
        self.assertEqual(self.project.mappings[0].text_override, '最新人工中文')
        self.assertFalse(self.project.mappings[0].confirmed)

    def test_success_replaces_history_only_after_validated_new_result(self):
        self.cancel_review()
        result = suggest(self.project, self.cache, 'unit-test-only',
                         transport=lambda *_: ('{"reviews":[{"mapping_id":"m","verdict":"uncertain","reason":"Insufficient evidence."}]}', {}))[0]
        self.assertEqual(result['status'], 'processed')
        self.assertNotIn('previous_suggestion', result)
        self.assertEqual(result['suggestion']['verdict'], 'uncertain')
        self.assertEqual(self.project.settings['api_coverage']['processed'], 1)


if __name__ == '__main__':
    unittest.main()
