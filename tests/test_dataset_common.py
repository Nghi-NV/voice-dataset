import json
import tempfile
import unittest
from pathlib import Path

from dataset_common import (
    deterministic_random_key,
    normalize_text,
    sentence_id,
    write_canonical_json,
)


class DatasetCommonTest(unittest.TestCase):
    def test_normalize_text_collapses_whitespace(self):
        self.assertEqual(
            normalize_text("  Bật   đèn\nphòng khách. "),
            "Bật đèn phòng khách.",
        )

    def test_random_key_is_stable_and_sqlite_safe(self):
        first = deterministic_random_key("lumi-voice-vi", "VI_NORTH_E_0001")
        second = deterministic_random_key("lumi-voice-vi", "VI_NORTH_E_0001")
        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 0)
        self.assertLessEqual(first, 2_147_483_647)

    def test_sentence_id_uses_region_and_difficulty(self):
        self.assertEqual(sentence_id("north", "easy", 1), "VI_NORTH_E_0001")
        self.assertEqual(sentence_id("central", "normal", 12), "VI_CENTRAL_N_0012")
        self.assertEqual(sentence_id("south", "hard", 100), "VI_SOUTH_H_0100")

    def test_canonical_json_preserves_vietnamese_and_one_trailing_newline(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "sample.json"
            write_canonical_json(path, {"text": "Bật đèn", "enabled": True})

            content = path.read_text(encoding="utf-8")
            self.assertIn("Bật đèn", content)
            self.assertTrue(content.endswith("\n"))
            self.assertFalse(content.endswith("\n\n"))
            self.assertEqual(json.loads(content)["enabled"], True)


if __name__ == "__main__":
    unittest.main()
