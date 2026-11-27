import unittest
from unittest.mock import patch
from lyricsync.vision import Track, refine_track


class OccurrenceBoundaryTests(unittest.TestCase):
    def test_refinement_cannot_jump_to_next_identical_occurrence(self):
        class Video:
            info = {'fps': 10, 'duration': 20}
            def frames(self, left, right, cancel):
                # Same glyphs before and after an observed blank at 6.1.
                return iter((i / 10, i / 10) for i in range(40, 81)
                            if left <= i / 10 < right)
        track = Track(5, 6, [0, 0, 10, 10], ['same'], [.9], None, 5, 6,
                      prior_absent=4.9, next_absent=6.1)
        with patch('lyricsync.vision.visibility_score',
                   side_effect=lambda template, stamp, box: 0 if stamp in (4.9, 6.1) else 1):
            first, last, brackets, notes = refine_track(Video(), track, 1, .62, lambda: False)
        self.assertEqual(first, 5)
        self.assertEqual(last, 6.1)
        self.assertEqual(brackets, [[4.9, 5], [6, 6.1]])

    def test_recorded_absence_limits_false_positive_shape_refinement(self):
        class Video:
            info = {'fps': 10, 'duration': 20}
            def frames(self, left, right, cancel):
                return iter((i / 10, None) for i in range(30, 81)
                            if left <= i / 10 < right)
        track = Track(5, 6, [0, 0, 10, 10], ['same'], [.9], None, 5, 6,
                      prior_absent=4.9, next_absent=6.1)
        # Coarse tracking already recorded absence, but refinement's glyph-shape
        # matcher now mistakes a background/repeated shape for continuous text.
        with patch('lyricsync.vision.visibility_score', return_value=1):
            first, last, brackets, notes = refine_track(Video(), track, 1, .62, lambda: False)
        with self.subTest(boundary='start'):
            self.assertGreaterEqual(first, track.prior_absent)
            self.assertEqual(brackets[0][0], track.prior_absent)
        with self.subTest(boundary='end'):
            self.assertLessEqual(last, track.next_absent + 1 / Video.info['fps'] + 1e-9)
            self.assertLessEqual(brackets[1][1], 6.2 + 1e-9)
        # No visual disappearance was verified; retain uncertainty warnings.
        self.assertEqual(len(notes), 2)

    def test_role_engine_overrides_are_explicit_and_legacy_is_preserved(self):
        from lyricsync.vision import role_ocr_settings
        old = {'engine': 'tesseract', 'ocr_device': 'directml'}
        self.assertEqual(role_ocr_settings(old, 'chinese', 'chi_sim')['engine'], 'tesseract')
        mixed = {**old, 'chinese_engine': 'rapidocr', 'source_engine': 'tesseract'}
        self.assertEqual(role_ocr_settings(mixed, 'chinese', 'chi_sim')['engine'], 'rapidocr')
        self.assertEqual(role_ocr_settings(mixed, 'source', 'jpn+eng')['engine'], 'tesseract')
        self.assertEqual(old, {'engine': 'tesseract', 'ocr_device': 'directml'})
        with self.assertRaises(ValueError):
            role_ocr_settings({'chinese_engine': 'unknown'}, 'chinese', 'chi_sim')

    def test_timed_repeated_lyrics_use_intervals_not_nearest_start(self):
        from lyricsync.model import Lyric, Visual
        from lyricsync.matching import associate_visuals
        lyrics = [Lyric('first', 'same lyric', 174, 193.35),
                  Lyric('second', 'same lyric', 198.33, 202.16)]
        visuals = [Visual('v1', 'same lyric', 189.75, 192.55, [], .99, 3),
                   Visual('v2', 'same lyric', 198.53, 201.30, [], .99, 3)]
        result, unknown = associate_visuals(lyrics, visuals)
        self.assertEqual([v.id for v, _, _ in result['first']], ['v1'])
        self.assertEqual([v.id for v, _, _ in result['second']], ['v2'])
        self.assertEqual(unknown, [])

    def test_legitimate_multiple_visuals_inside_one_lyric_survive(self):
        from lyricsync.model import Lyric, Visual
        from lyricsync.matching import associate_visuals
        lyrics = [Lyric('first', 'same lyric', 10, 20), Lyric('second', 'same lyric', 30, 35)]
        visuals = [Visual('a', 'same lyric', 11, 13, [], .99, 3),
                   Visual('b', 'same lyric', 14, 17, [], .99, 3)]
        result, unknown = associate_visuals(lyrics, visuals)
        self.assertEqual([v.id for v, _, _ in result['first']], ['a', 'b'])
        self.assertEqual(unknown, [])

    def test_repeat_outside_all_intervals_remains_unassigned(self):
        from lyricsync.model import Lyric, Visual
        from lyricsync.matching import associate_visuals
        lyrics = [Lyric('first', 'same lyric', 10, 20), Lyric('second', 'same lyric', 30, 35)]
        result, unknown = associate_visuals(lyrics, [Visual('gap', 'same lyric', 24, 26, [], .99, 3)])
        self.assertEqual(unknown, ['gap'])
        self.assertFalse(any(result.values()))

    def test_repeat_rematch_preserves_user_locked_first_occurrence(self):
        from lyricsync.model import Lyric, Visual, Translation, Mapping, Project
        from lyricsync.matching import match_project
        locked = Mapping('manual', ['t1'], ['first'], [], 189.25, 192.55,
                         locked=True, human_edited=True)
        project = Project(lyrics=[Lyric('first', 'same lyric', 174, 193.35),
                                  Lyric('second', 'same lyric', 198.33, 202.16)],
            translations=[Translation('t1', 'first translation'), Translation('t2', 'second translation')],
            visuals=[Visual('v1', 'same lyric', 189.75, 192.55, [], .99, 3),
                     Visual('v2', 'same lyric', 198.53, 201.30, [], .99, 3)], mappings=[locked])
        result = match_project(project)
        self.assertIs(result[0], locked)
        self.assertEqual((locked.start, locked.end, locked.visual_ids), (189.25, 192.55, []))
        second = next(mapping for mapping in result if mapping.translation_ids == ['t2'])
        self.assertEqual(second.visual_ids, ['v2'])
        self.assertEqual((second.start, second.end), (198.53, 201.30))

    def test_nested_repeat_intervals_use_start_distance_as_secondary_evidence(self):
        from lyricsync.model import Lyric, Visual
        from lyricsync.matching import associate_visuals
        lyrics = [Lyric('first', 'same lyric', 10, 40), Lyric('second', 'same lyric', 25, 35)]
        visuals = [Visual('a', 'same lyric', 11, 13, [], .99, 3),
                   Visual('b', 'same lyric', 30, 32, [], .99, 3)]
        for order in (lyrics, list(reversed(lyrics))):
            result, unknown = associate_visuals(order, visuals)
            self.assertEqual([v.id for v, _, _ in result['first']], ['a'])
            self.assertEqual([v.id for v, _, _ in result['second']], ['b'])
            self.assertEqual(unknown, [])

    def test_indistinguishable_repeat_intervals_do_not_pick_list_order(self):
        from lyricsync.model import Lyric, Visual
        from lyricsync.matching import associate_visuals
        result, unknown = associate_visuals(
            [Lyric('first', 'same lyric', 10, 40), Lyric('second', 'same lyric', 10, 40)],
            [Visual('a', 'same lyric', 11, 13, [], .99, 3)])
        self.assertFalse(any(result.values()))
        self.assertEqual(unknown, ['a'])

    def test_repeat_gap_cannot_fall_back_to_weaker_text_candidate(self):
        from lyricsync.model import Lyric, Visual
        from lyricsync.matching import associate_visuals
        lyrics = [Lyric('first', 'same lyric', 10, 20), Lyric('second', 'same lyric', 30, 35),
                  Lyric('distractor', 'same lyricsy', 24, 28)]
        for text in ('same lyric', 'same lyricx'):
            with self.subTest(text=text):
                result, unknown = associate_visuals(lyrics, [Visual('gap', text, 24, 26, [], .99, 3)])
                self.assertFalse(any(result.values()))
                self.assertEqual(unknown, ['gap'])


if __name__ == '__main__':
    unittest.main()
