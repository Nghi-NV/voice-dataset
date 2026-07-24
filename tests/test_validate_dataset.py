import json
import tempfile
import unittest
from pathlib import Path

from build_dataset import build_dataset
from dataset_common import sha256_file, write_canonical_json
from generate_sentences import generate_sentences


ROOT = Path(__file__).resolve().parents[1]
VERSION = "2026.07.3"


class ValidateDatasetTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        source = self.root / "source.csv"
        generate_sentences(ROOT / "data/catalog.json", ROOT / "data/templates.json", source, "lumi-voice-vi")
        build_dataset(source, self.root, "lumi-voice-vi", VERSION, 5000, "2026-07-23T10:00:00Z")

    def tearDown(self):
        self.temporary_directory.cleanup()

    @property
    def manifest_path(self):
        return self.root / "versions" / VERSION / "manifest.json"

    def _manifest(self):
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _entry(self, *, region=None, difficulty=None):
        return next(
            entry for entry in self._manifest()["files"]
            if (region is None or entry["region"] == region)
            and (difficulty is None or entry["difficulty"] == difficulty)
        )

    def _shard_path(self, entry):
        return self.manifest_path.parent / entry["path"]

    def _refresh_entry(self, shard_path: Path):
        manifest = self._manifest()
        relative = shard_path.relative_to(self.manifest_path.parent).as_posix()
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            if entry["path"] == relative:
                entry["size_bytes"] = shard_path.stat().st_size
                entry["sha256"] = sha256_file(shard_path)
                entry["count"] = len(shard["sentences"])
                entry["enabled_count"] = sum(item["enabled"] for item in shard["sentences"])
                entry["language_style_counts"] = {
                    style: sum(item["language_style"] == style for item in shard["sentences"])
                    for style in ("standard", "regional")
                }
                break
        write_canonical_json(self.manifest_path, manifest)

    def _errors(self):
        from validate_dataset import validate_dataset
        return validate_dataset(self.root, VERSION)

    def _rewrite_sentence(self, region, difficulty, mutate):
        entry = self._entry(region=region, difficulty=difficulty)
        shard_path = self._shard_path(entry)
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        mutate(shard["sentences"][0])
        write_canonical_json(shard_path, shard)
        self._refresh_entry(shard_path)

    def _rewrite_shared(self, mutate):
        entry = self._entry(region="shared")
        path = self._shard_path(entry)
        shard = json.loads(path.read_text(encoding="utf-8"))
        mutate(shard["sentences"])
        write_canonical_json(path, shard)
        self._refresh_entry(path)

    def test_valid_dataset_has_no_errors(self):
        self.assertEqual([], self._errors())

    def test_detects_checksum_mismatch(self):
        shard_path = self._shard_path(self._entry())
        shard_path.write_bytes(shard_path.read_bytes() + b" ")
        self.assertTrue(any("SHA-256 mismatch" in error for error in self._errors()))

    def test_detects_split_conversation_group(self):
        shared_entry = self._entry(region="shared")
        shared_path = self._shard_path(shared_entry)
        shard = json.loads(shared_path.read_text(encoding="utf-8"))
        moved = shard["sentences"].pop(0)
        write_canonical_json(shared_path, shard)
        self._refresh_entry(shared_path)

        duplicate = dict(shared_entry)
        duplicate["path"] = "sentences/shared/hard-conversation-0002.json"
        duplicate["shard"] = 2
        second_path = self.manifest_path.parent / duplicate["path"]
        second_shard = dict(shard)
        second_shard["shard"] = 2
        second_shard["sentences"] = [moved]
        write_canonical_json(second_path, second_shard)
        duplicate["count"] = duplicate["enabled_count"] = 1
        duplicate["language_style_counts"] = {"standard": 1, "regional": 0}
        duplicate["size_bytes"] = second_path.stat().st_size
        duplicate["sha256"] = sha256_file(second_path)
        manifest = self._manifest()
        manifest["files"].append(duplicate)
        write_canonical_json(self.manifest_path, manifest)
        self.assertTrue(any("split across shards" in error for error in self._errors()))

    def test_detects_duplicate_turn_index(self):
        entry = self._entry(region="shared")
        path = self._shard_path(entry)
        shard = json.loads(path.read_text(encoding="utf-8"))
        shard["sentences"][1]["turn_index"] = 1
        write_canonical_json(path, shard)
        self._refresh_entry(path)
        self.assertTrue(any("turn indices" in error for error in self._errors()))

    def test_detects_missing_turn_index(self):
        entry = self._entry(region="shared")
        path = self._shard_path(entry)
        shard = json.loads(path.read_text(encoding="utf-8"))
        shard["sentences"][1].pop("turn_index")
        write_canonical_json(path, shard)
        self._refresh_entry(path)
        self.assertTrue(any("conversation fields" in error for error in self._errors()))

    def test_detects_normal_atomic_record(self):
        self._rewrite_sentence("north", "normal", lambda item: item.__setitem__("task_shape", "atomic"))
        self.assertTrue(any("normal" in error and "compound" in error for error in self._errors()))

    def test_detects_easy_compound_record(self):
        self._rewrite_sentence("north", "easy", lambda item: item.__setitem__("task_shape", "compound"))
        self.assertTrue(any("easy" in error and "atomic" in error for error in self._errors()))

    def test_detects_hard_atomic_record(self):
        self._rewrite_sentence("north", "hard", lambda item: item.__setitem__("task_shape", "atomic"))
        self.assertTrue(
            any("hard" in error and "ambiguous" in error and "conversation" in error for error in self._errors())
        )

    def test_detects_policy_requiring_too_many_conversation_groups(self):
        manifest = self._manifest()
        manifest["collection_policy"]["conversation_groups_per_difficulty"]["hard"] = 5
        write_canonical_json(self.manifest_path, manifest)
        self.assertTrue(any("conversation_groups_per_difficulty unavailable" in error for error in self._errors()))

    def test_detects_invalid_turn_order_and_unlock(self):
        manifest = self._manifest()
        manifest["collection_policy"]["turns"].reverse()
        write_canonical_json(self.manifest_path, manifest)
        self.assertTrue(any("Invalid turns" in error for error in self._errors()))

    def test_rejects_non_exact_conversation_group_shapes(self):
        for sizes in ((2, 3, 3, 4), (6, 6)):
            with self.subTest(sizes=sizes):
                def mutate(sentences):
                    offset = 0
                    for group_number, size in enumerate(sizes, start=1):
                        for turn_index, row in enumerate(sentences[offset:offset + size], start=1):
                            row["conversation_id"] = f"MALFORMED-{group_number}"
                            row["turn_index"] = turn_index
                            row["turn_count"] = size
                        offset += size
                self._rewrite_shared(mutate)
                try:
                    self.assertTrue(any("exactly 4 groups of 3 turns" in error for error in self._errors()))
                finally:
                    self.tearDown()
                    self.setUp()

    def test_rejects_reordered_or_interleaved_conversation_turns(self):
        for malformed_kind in ("reordered", "interleaved"):
            with self.subTest(malformed_kind=malformed_kind):
                def mutate(sentences):
                    if malformed_kind == "reordered":
                        sentences[0], sentences[1] = sentences[1], sentences[0]
                    else:
                        sentences[:] = [sentences[index] for index in (0, 3, 1, 4, 2, 5, 6, 7, 8, 9, 10, 11)]
                self._rewrite_shared(mutate)
                try:
                    self.assertTrue(any("contiguous physical turn order" in error for error in self._errors()))
                finally:
                    self.tearDown()
                    self.setUp()

    def test_rejects_language_and_latest_identity_mismatches(self):
        manifest = self._manifest()
        manifest["language"] = 7
        write_canonical_json(self.manifest_path, manifest)
        latest_path = self.root / "latest.json"
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        latest["dataset_id"] = []
        latest["published_at"] = {}
        write_canonical_json(latest_path, latest)
        errors = self._errors()
        self.assertTrue(any("language" in error for error in errors))
        self.assertTrue(any("dataset_id" in error for error in errors))
        self.assertTrue(any("published_at" in error for error in errors))

    def test_malformed_structures_return_controlled_errors(self):
        fixtures = (
            ("manifest-list", lambda: write_canonical_json(self.manifest_path, [])),
            ("latest-list", lambda: write_canonical_json(self.root / "latest.json", [])),
            ("path-list", lambda: self._set_manifest_file_value("path", [])),
            ("conversation-id-list", lambda: self._rewrite_shared(lambda rows: rows[0].__setitem__("conversation_id", []))),
        )
        for name, corrupt in fixtures:
            with self.subTest(name=name):
                corrupt()
                try:
                    errors = self._errors()
                    self.assertTrue(errors)
                finally:
                    self.tearDown()
                    self.setUp()

    def _set_manifest_file_value(self, key, value):
        manifest = self._manifest()
        manifest["files"][0][key] = value
        write_canonical_json(self.manifest_path, manifest)

    def test_rejects_symlink_in_shard_path_without_touching_target(self):
        entry = self._entry()
        original_path = self._shard_path(entry)
        outside_dir = self.root / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "shard.json"
        outside_file.write_bytes(original_path.read_bytes())
        sentinel = outside_file.read_bytes()
        (self.manifest_path.parent / "sentences" / "linked").symlink_to(
            outside_dir,
            target_is_directory=True,
        )
        manifest = self._manifest()
        manifest["files"][0]["path"] = "sentences/linked/shard.json"
        write_canonical_json(self.manifest_path, manifest)

        self.assertTrue(any("symlink" in error for error in self._errors()))
        self.assertEqual(sentinel, outside_file.read_bytes())


if __name__ == "__main__":
    unittest.main()
