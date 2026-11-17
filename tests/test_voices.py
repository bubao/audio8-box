import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import voices


class VoiceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "voices"
        patcher = mock.patch.dict(os.environ, {"AUDIO8_VOICES_DIR": str(self.dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_list_get(self):
        item = voices.save_voice("新闻播音", "这是参考文本。", b"RIFF....", ".wav")
        self.assertTrue(item["id"])
        self.assertEqual(item["name"], "新闻播音")
        self.assertEqual(item["ref_text"], "这是参考文本。")
        self.assertTrue(item["file"].endswith(".wav"))
        self.assertTrue((self.dir / item["file"]).exists())

        listed = voices.list_voices()
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["exists"])
        self.assertEqual(voices.get_voice(item["id"])["id"], item["id"])

    def test_delete(self):
        item = voices.save_voice("A", "文本", b"x", ".wav")
        self.assertTrue(voices.delete_voice(item["id"]))
        self.assertEqual(voices.list_voices(), [])
        self.assertFalse((self.dir / item["file"]).exists())
        self.assertFalse(voices.delete_voice(item["id"]))

    def test_blank_name_and_text(self):
        with self.assertRaises(ValueError):
            voices.save_voice("   ", "文本", b"x", ".wav")
        with self.assertRaises(ValueError):
            voices.save_voice("名称", "  ", b"x", ".wav")
        with self.assertRaises(ValueError):
            voices.save_voice("名称", "文本", b"", ".wav")

    def test_bad_suffix(self):
        with self.assertRaises(ValueError):
            voices.save_voice("名称", "文本", b"x", ".exe")

    def test_duplicate_names_allowed(self):
        first = voices.save_voice("同名", "文本一", b"a", ".wav")
        second = voices.save_voice("同名", "文本二", b"b", ".mp3")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(voices.list_voices()), 2)

    def test_missing_file_marked(self):
        item = voices.save_voice("A", "文本", b"x", ".wav")
        (self.dir / item["file"]).unlink()
        listed = voices.list_voices()
        self.assertFalse(listed[0]["exists"])
        self.assertIsNone(voices.get_voice("missing"))

    def test_corrupt_index(self):
        self.dir.mkdir(parents=True)
        (self.dir / "voices.json").write_text("{bad json", encoding="utf-8")
        self.assertEqual(voices.list_voices(), [])
        voices.save_voice("A", "文本", b"x", ".wav")
        self.assertEqual(len(voices.list_voices()), 1)

    def test_name_whitespace_and_truncation(self):
        item = voices.save_voice("  我的  音色  ", "文本", b"x", ".wav")
        self.assertEqual(item["name"], "我的 音色")
        long_item = voices.save_voice("字" * 100, "文本", b"x", ".wav")
        self.assertEqual(len(long_item["name"]), voices.MAX_NAME_CHARS)


if __name__ == "__main__":
    unittest.main()
