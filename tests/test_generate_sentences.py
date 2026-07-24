import csv
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from dataset_common import DIFFICULTIES, REGIONS, normalize_text


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "catalog.json"
TEMPLATES_PATH = ROOT / "data" / "templates.json"
EXPECTED_SHAPES = {
    "easy": {"atomic"},
    "normal": {"compound"},
    "hard": {"ambiguous", "conversation"},
}


class TemplateContractTest(unittest.TestCase):
    def test_templates_are_owned_by_one_semantic_level(self):
        from generate_sentences import _expand_template

        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        payload = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
        templates_by_difficulty = payload["templates"]
        if isinstance(templates_by_difficulty, list):
            templates_by_difficulty = {
                difficulty: [
                    template
                    for template in templates_by_difficulty
                    if template["difficulty"] == difficulty
                ]
                for difficulty in DIFFICULTIES
            }

        seen_ids = {}
        seen_patterns = {}
        seen_rendered_texts = {}
        for difficulty, templates in templates_by_difficulty.items():
            for template in templates:
                self.assertIn(template["task_shape"], EXPECTED_SHAPES[difficulty])
                self.assertNotIn(template["id"], seen_ids)
                seen_ids[template["id"]] = difficulty

                pattern = normalize_text(template["text"]).casefold()
                previous_pattern_owner = seen_patterns.get(pattern)
                self.assertIn(previous_pattern_owner, {None, difficulty})
                seen_patterns.setdefault(pattern, difficulty)
                for rendered_text in _expand_template(template, catalog):
                    normalized = normalize_text(rendered_text).casefold()
                    previous_owner = seen_rendered_texts.get(normalized)
                    self.assertIn(previous_owner, {None, difficulty})
                    seen_rendered_texts.setdefault(normalized, difficulty)

                if difficulty == "easy":
                    self.assertEqual(1, template["action_count"])
                    self.assertEqual(1, template["target_count"])
                elif difficulty == "normal":
                    self.assertGreaterEqual(
                        max(template["action_count"], template["target_count"]),
                        2,
                    )
                elif difficulty == "hard":
                    self.assertEqual("ambiguous", template["task_shape"])
                    self.assertEqual(1, template["action_count"])
                    self.assertEqual(0, template["target_count"])

    def test_catalog_and_templates_follow_contract(self):
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        payload = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
        templates = [
            template
            for difficulty_templates in payload["templates"].values()
            for template in difficulty_templates
        ]
        allowed_capabilities = set(catalog["devices"]) | {"none", "nonexistent"}

        self.assertGreater(len(templates), 0)
        self.assertEqual(set(catalog["regional_words"]), set(REGIONS))
        for template in templates:
            self.assertIn(template["language_style"], {"standard", "regional"})
            self.assertIn(template["required_capability"], allowed_capabilities)
            self.assertNotIn("[CONTEXT]", template["text"])
            if template["language_style"] == "regional":
                self.assertIn(template["region"], REGIONS)

    def test_catalog_has_values_for_every_supported_capability(self):
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            set(catalog["devices"]),
            {"toggle", "open_close", "brightness", "temperature", "fan_level"},
        )
        self.assertGreaterEqual(len(catalog["rooms"]), 7)
        self.assertGreaterEqual(len(catalog["nonexistent"]["rooms"]), 5)
        self.assertGreaterEqual(len(catalog["nonexistent"]["devices"]), 4)


class SentenceGenerationTest(unittest.TestCase):
    def test_hard_singles_do_not_invent_area_room_relationships(self):
        from generate_sentences import generate_rows

        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        hard_singles = [
            row
            for row in generate_rows(seed=20260723)
            if row["difficulty"] == "hard"
            and row["recording_mode"] == "single"
        ]
        invalid_relationships = [
            row["text"]
            for row in hard_singles
            if any(area in row["text"] for area in catalog["areas"])
            and any(room in row["text"] for room in catalog["rooms"])
        ]

        self.assertEqual(
            0,
            len(invalid_relationships),
            f"Hard singles invented area-room relationships: {invalid_relationships[:5]}",
        )

    def test_generator_emits_900_singles_and_12_shared_l3_turns(self):
        from generate_sentences import generate_rows

        rows = generate_rows(seed=20260723)
        self.assertEqual(912, len(rows))
        conversations = [
            row for row in rows if row["recording_mode"] == "conversation"
        ]
        self.assertEqual(12, len(conversations))
        expected_conversations = {
            "L3-01": [
                "Bật đèn.",
                "Giảm nó xuống 50%.",
                "Tắt cái đèn vừa bật.",
            ],
            "L3-02": [
                "Đặt điều hòa ở 26 độ.",
                "Giảm thêm 2 độ.",
                "Tắt nó đi.",
            ],
            "L3-03": [
                "Mở rèm.",
                "Đóng bớt đi 70%.",
                "Dừng lại.",
            ],
            "L3-04": [
                "Bật đèn trần.",
                "Bật cả đèn bàn ăn nữa.",
                "Giảm sáng cả hai xuống 50%.",
            ],
        }
        self.assertEqual(
            set(expected_conversations),
            {row["conversation_id"] for row in conversations},
        )
        for conversation_id, expected_texts in expected_conversations.items():
            turns = [
                row
                for row in conversations
                if row["conversation_id"] == conversation_id
            ]
            self.assertEqual(expected_texts, [row["text"] for row in turns])
            self.assertEqual([1, 2, 3], [int(row["turn_index"]) for row in turns])
            self.assertTrue(all(int(row["turn_count"]) == 3 for row in turns))
            self.assertTrue(all(row["task_shape"] == "conversation" for row in turns))
            self.assertTrue(all(row["difficulty"] == "hard" for row in turns))
            self.assertTrue(
                all(row["language_style"] == "standard" for row in turns)
            )
            self.assertTrue(all(row["region"] == "all" for row in turns))
            self.assertTrue(all(row["region_scope"] == "all" for row in turns))

    def test_generator_creates_912_unique_resolved_sentences(self):
        from generate_sentences import generate_sentences

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "source.csv"
            generate_sentences(
                catalog_path=CATALOG_PATH,
                templates_path=TEMPLATES_PATH,
                output_path=output,
                dataset_id="lumi-voice-vi",
            )

            with output.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))

        self.assertEqual(len(rows), 912)
        self.assertEqual(len({row["id"] for row in rows}), 912)
        normalized_texts = {normalize_text(row["text"]).casefold() for row in rows}
        self.assertEqual(len(normalized_texts), 912)

        singles = [row for row in rows if row["recording_mode"] == "single"]
        self.assertEqual(len(singles), 900)

        counts = Counter(
            (row["region"], row["difficulty"], row["language_style"])
            for row in singles
        )
        for region in REGIONS:
            for difficulty in DIFFICULTIES:
                self.assertEqual(counts[(region, difficulty, "standard")], 70)
                self.assertEqual(counts[(region, difficulty, "regional")], 30)

        helper_markers = {
            "north": "hộ",
            "central": "giúp mình",
            "south": "giùm",
        }
        for region in REGIONS:
            for difficulty in DIFFICULTIES:
                helper_count = sum(
                    helper_markers[region] in row["text"].casefold()
                    for row in singles
                    if row["region"] == region
                    and row["difficulty"] == difficulty
                    and row["language_style"] == "regional"
                )
                self.assertLessEqual(
                    helper_count,
                    10,
                    f"{region}/{difficulty}: {helper_count}/30 regional sentences use helpers",
                )

        for row in singles:
            self.assertNotIn("[CONTEXT]", row["text"])
            self.assertNotIn("{", row["text"])
            self.assertNotIn("}", row["text"])
            self.assertEqual(row["enabled"], "true")
            self.assertTrue(row["text"][0].isupper(), row["text"])

        max_words = {"easy": 10, "normal": 12, "hard": 10}
        for row in singles:
            word_count = len(normalize_text(row["text"]).rstrip(".?!").split())
            self.assertLessEqual(word_count, max_words[row["difficulty"]], row["text"])

        unnatural_fragments = (
            "Dừng đèn",
            "Dừng điều hòa",
            "Ngắt đèn",
            "Ngắt điều hòa",
            "Ngắt quạt",
            "Khởi động đèn",
            "Cho đèn trần ở",
            "Cho đèn ngủ ở",
            "Cho đèn hồ cá hoạt động",
        )
        mechanical_fragments = (
            "tại mức sáng",
            "về mức",
            "giúp tôi với",
            "ở phòng đọc ở phòng",
            "phòng kho ở phòng",
        )
        for row in singles:
            self.assertFalse(
                row["text"].startswith(unnatural_fragments),
                row["text"],
            )
            self.assertFalse(
                any(
                    fragment in row["text"].casefold()
                    for fragment in mechanical_fragments
                ),
                row["text"],
            )
            self.assertFalse(
                "100 phần trăm" in row["text"]
                and row["text"].startswith(("Hạ ", "Giảm ")),
                row["text"],
            )

        regional_vocabulary = {
            "north": ("điều hòa", "hộ"),
            "central": ("máy lạnh", "giúp mình"),
            "south": ("máy lạnh", "màn cửa", "giùm"),
        }
        for region, phrases in regional_vocabulary.items():
            regional_text = "\n".join(
                row["text"].casefold()
                for row in singles
                if row["region"] == region and row["language_style"] == "regional"
            )
            for phrase in phrases:
                self.assertIn(phrase, regional_text, f"{region}: {phrase}")


if __name__ == "__main__":
    unittest.main()
