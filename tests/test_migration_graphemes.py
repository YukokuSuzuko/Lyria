import unittest
from lyricsync.graphemes import character_gaps, validate_character_gap, codepoint_to_utf16, utf16_to_codepoint, safe_utf16_gap


class GraphemeTests(unittest.TestCase):
    def test_chinese_each_adjacent_character_gap(self):
        self.assertEqual(character_gaps("每一句话"), [0, 1, 2, 3, 4])

    def test_no_inside_combining_emoji_flag_indic_or_crlf(self):
        for cluster in ("e\u0301", "👩🏽‍💻", "🇨🇳", "\r\n", "क्‍ष", "한"):
            with self.subTest(cluster=cluster):
                self.assertEqual(character_gaps(cluster), [0, len(cluster)])
                for offset in range(1, len(cluster)):
                    with self.assertRaises(ValueError): validate_character_gap(cluster, offset)

    def test_qt_utf16_and_python_offsets_differ(self):
        text = "我😀们"
        self.assertEqual(codepoint_to_utf16(text, 2), 3)
        self.assertEqual(utf16_to_codepoint(text, 3), 2)
        with self.assertRaises(ValueError): utf16_to_codepoint(text, 2)
        self.assertEqual(safe_utf16_gap(text, 2), {"codepoint": 2, "utf16": 3})

    def test_empty_endpoints_and_invalid_inputs(self):
        self.assertEqual(character_gaps(""), [0])
        self.assertEqual(safe_utf16_gap("", 0), {"codepoint": 0, "utf16": 0})
        for offset in (-1, 99, True):
            with self.assertRaises(ValueError): utf16_to_codepoint("abc", offset)
        with self.assertRaises(ValueError): character_gaps("\ud800")

    def test_all_valid_gaps_roundtrip(self):
        text = "中文 é 👩🏽‍💻\n🇨🇳ひらがな"
        for offset in character_gaps(text):
            self.assertEqual(utf16_to_codepoint(text, codepoint_to_utf16(text, offset)), offset)


if __name__ == "__main__": unittest.main()
