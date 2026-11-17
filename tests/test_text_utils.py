from __future__ import annotations

import unittest

from text_utils import split_sentences, split_text


class SplitSentencesTests(unittest.TestCase):
    def test_chinese_punctuation(self):
        text = "你好。今天天气不错！我们出去走走？"
        self.assertEqual(split_sentences(text), ["你好。", "今天天气不错！", "我们出去走走？"])

    def test_english_punctuation_and_newlines(self):
        text = "Hello world. How are you?\nSecond line!"
        self.assertEqual(
            split_sentences(text),
            ["Hello world.", "How are you?", "Second line!"],
        )

    def test_semicolon_boundary(self):
        self.assertEqual(split_sentences("第一；第二；第三"), ["第一；", "第二；", "第三"])

    def test_blank_and_whitespace_only(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("   \n  "), [])

    def test_long_sentence_hard_split(self):
        text = "啊" * 10
        self.assertEqual(split_sentences(text, max_chars=4), ["啊啊啊啊", "啊啊啊啊", "啊啊"])

    def test_short_text_single_sentence(self):
        self.assertEqual(split_sentences("一句话"), ["一句话"])


class SplitTextTests(unittest.TestCase):
    def test_merges_short_pieces_up_to_max_chars(self):
        text = "一。二。三。"
        self.assertEqual(split_text(text, max_chars=10), ["一。二。三。"])

    def test_newline_boundary_merges_short_lines(self):
        # 换行是边界，但短句仍会按 max_chars 合并（split_text 的既有行为）
        self.assertEqual(split_text("甲\n乙", max_chars=100), ["甲\n乙"])


if __name__ == "__main__":
    unittest.main()
