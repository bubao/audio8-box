from __future__ import annotations

import unittest

from models import MODEL_CATALOG, MODEL_IDS, human_size, resolve_model_ref


class ResolveModelRefTests(unittest.TestCase):
    def test_builtin_uses_default_source(self):
        self.assertEqual(
            resolve_model_ref("preview"),
            ("hf", "mlx-community/Audio8-TTS-Preview-0.6b-bf16"),
        )

    def test_builtin_explicit_source(self):
        self.assertEqual(
            resolve_model_ref("realistic", "modelscope"),
            ("modelscope", "mlx-community/Audio8-TTS-Realistic-0.6b-bf16"),
        )

    def test_builtin_rejects_unsupported_source(self):
        with self.assertRaises(SystemExit):
            resolve_model_ref("realistic-air", "hf")

    def test_arbitrary_repo_defaults_to_hf(self):
        self.assertEqual(resolve_model_ref("owner/name"), ("hf", "owner/name"))

    def test_arbitrary_repo_with_source(self):
        self.assertEqual(
            resolve_model_ref("owner/name", "modelscope"),
            ("modelscope", "owner/name"),
        )

    def test_unknown_name(self):
        with self.assertRaises(SystemExit):
            resolve_model_ref("nope")

    def test_unknown_source(self):
        with self.assertRaises(SystemExit):
            resolve_model_ref("preview", "unknown")


class CatalogTests(unittest.TestCase):
    def test_every_model_has_usable_default_source(self):
        for name, spec in MODEL_CATALOG.items():
            self.assertIn(spec["default_source"], spec["sources"], name)

    def test_model_ids_follow_default_source(self):
        for name, spec in MODEL_CATALOG.items():
            self.assertEqual(
                MODEL_IDS[name], spec["sources"][spec["default_source"]], name
            )

    def test_modelscope_only_model_declares_ignore_patterns(self):
        spec = MODEL_CATALOG["realistic-air"]
        self.assertIn("modelscope", spec["sources"])
        self.assertTrue(spec.get("ignore_patterns"))


class HumanSizeTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1023), "1023 B")
        self.assertEqual(human_size(1024), "1.0 KB")
        self.assertEqual(human_size(5 * 1024 * 1024), "5.0 MB")
