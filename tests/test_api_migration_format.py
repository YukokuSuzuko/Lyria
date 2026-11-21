"""User-facing review text resolves internal references, never developer JSON."""
from copy import deepcopy
import unittest

from lyricsync.api import format_review_suggestions, review_signature
from lyricsync.frames import FrameIndex
from lyricsync.model import Lyric, Mapping, Project, Translation, Visual


class ReviewFormatTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(lyrics=[Lyric('internal_lyric', 'HELLO', 0, .2)],
            translations=[Translation('internal_translation', '用户可靠中文')],
            visuals=[Visual('internal_visual', 'HELLO OCR', 0, .2, [], .8, 2)],
            mappings=[Mapping('internal_mapping', ['internal_translation'], ['internal_lyric'], ['internal_visual'], 0, .2)])
        self.index = FrameIndex([0, .04, .09, .14], .2)
        self.result = [{'mapping_id': 'internal_mapping', 'status': 'processed',
            'context_signature': review_signature(self.project, self.project.mappings[0]),
            'suggestion': {'mappings': [{'lyric_ids': ['internal_lyric'], 'translation_ids': ['internal_translation'],
                'mapping_ids': ['internal_mapping'], 'current_chinese': ['用户可靠中文'], 'reason': '配对待核对',
                'reference_meaning': '你好', 'semantic_warning': '可能表达有别'}],
                'corrections': [{'lyric_id': 'internal_lyric', 'suggestion': 'HELLO!', 'reason': '标点待核对'}]}}]

    def test_evidence_is_actual_text_and_frames_not_internal_ids(self):
        body = format_review_suggestions(self.project, self.result, self.index)
        self.assertIn('旧版结果',body)
        for excluded in ('HELLO OCR','F1–F4','你好','HELLO!','internal_'):
            self.assertNotIn(excluded,body)
        self.result[0]['suggestion']={'verdict':'match','reason':'中日意思对应。'}
        body=format_review_suggestions(self.project,self.result,self.index)
        self.assertIn('第 1 句 · 对应：中日意思对应。',body)

    def test_formatter_does_not_mutate_project_or_result(self):
        before_project = deepcopy(self.project.to_dict()); before_result = deepcopy(self.result)
        format_review_suggestions(self.project, self.result, self.index)
        self.assertEqual(self.project.to_dict(), before_project)
        self.assertEqual(self.result, before_result)

    def test_changed_revision_explicitly_marks_old_evidence_stale(self):
        self.project.mappings[0].text_override = '最新人工修改'
        body = format_review_suggestions(self.project, self.result)
        self.assertIn('已过期', body)
        self.assertIn('不确定', body)
        self.assertNotIn('你好', body)
        self.assertNotIn('internal_', body)

    def test_unknown_references_are_not_printed_as_debug_ids(self):
        self.result[0]['suggestion']['mappings'][0]['lyric_ids'] = ['UNKNOWN_INTERNAL']
        body = format_review_suggestions(self.project, self.result)
        self.assertIn('旧版结果', body)
        self.assertNotIn('UNKNOWN_INTERNAL', body)
        self.project.mappings = []
        self.assertNotIn('第 1 句', format_review_suggestions(self.project, self.result))

    def test_empty_pending_failure_and_no_change_are_honest(self):
        self.assertIn('没有已保存', format_review_suggestions(self.project, []))
        self.result[0].update(status='pending', suggestion={})
        self.assertIn('尚未核对', format_review_suggestions(self.project, self.result))
        self.result[0].update(status='failed', error='budget_exhausted；未执行')
        self.assertIn('核对未完成', format_review_suggestions(self.project, self.result))
        self.result[0].update(status='processed');self.result[0].pop('error')
        self.assertIn('旧版结果', format_review_suggestions(self.project, self.result))


if __name__ == '__main__':
    unittest.main()
