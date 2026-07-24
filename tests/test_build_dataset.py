import csv
import json
import tempfile
import unittest
from pathlib import Path

from dataset_common import sha256_file
from generate_sentences import generate_sentences


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "catalog.json"
TEMPLATES_PATH = ROOT / "data" / "templates.json"
PUBLISHED_AT = "2026-07-23T10:00:00Z"
VERSION = "2026.07.3"


class BuildDatasetTest(unittest.TestCase):
    def _source_csv(self, root: Path) -> Path:
        source = root / "source.csv"
        generate_sentences(
            catalog_path=CATALOG_PATH,
            templates_path=TEMPLATES_PATH,
            output_path=source,
            dataset_id="lumi-voice-vi",
        )
        return source

    def _build(
        self,
        source: Path,
        output: Path,
        force: bool = False,
        shard_size=5000,
        dataset_id="lumi-voice-vi",
        published_at=PUBLISHED_AT,
    ):
        from build_dataset import build_dataset

        return build_dataset(
            input_path=source,
            output_root=output,
            dataset_id=dataset_id,
            version=VERSION,
            shard_size=shard_size,
            published_at=published_at,
            force=force,
        )

    def _rows(self, source: Path):
        with source.open(encoding="utf-8", newline="") as input_file:
            return list(csv.DictReader(input_file))

    def _write_rows(self, source: Path, rows):
        with source.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)

    def test_build_creates_schema_v2_regional_and_shared_shards(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._build(self._source_csv(root), root)

            manifest_path = root / "versions" / VERSION / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))

            self.assertEqual(2, manifest["schema_version"])
            self.assertEqual(912, manifest["counts"]["total"])
            self.assertEqual(10, len(manifest["files"]))
            self.assertEqual(
                {"easy": 10, "normal": 10, "hard": 10},
                manifest["collection_policy"]["counts_per_difficulty"],
            )
            self.assertEqual(
                {"easy": 3, "normal": 3, "hard": 3},
                manifest["collection_policy"]["regional_style_per_difficulty"],
            )
            self.assertEqual(
                {"easy": 0, "normal": 0, "hard": 2},
                manifest["collection_policy"]["conversation_groups_per_difficulty"],
            )
            self.assertEqual(
                [
                    {"id": "ONE_METER", "distance_meters": 1},
                    {
                        "id": "THREE_METERS",
                        "distance_meters": 3,
                        "unlock_after": "ONE_METER",
                    },
                ],
                manifest["collection_policy"]["turns"],
            )
            self.assertEqual(2, latest["schema_version"])
            self.assertEqual(VERSION, latest["latest_version"])
            self.assertEqual(f"versions/{VERSION}/manifest.json", latest["manifest_path"])

            regional_files = [entry for entry in manifest["files"] if entry["region"] != "shared"]
            shared_files = [entry for entry in manifest["files"] if entry["region"] == "shared"]
            self.assertEqual(9, len(regional_files))
            self.assertEqual(1, len(shared_files))
            self.assertEqual(
                "sentences/shared/hard-conversation-0001.json",
                shared_files[0]["path"],
            )
            self.assertEqual(12, shared_files[0]["count"])

            shared_path = manifest_path.parent / shared_files[0]["path"]
            shared = json.loads(shared_path.read_text(encoding="utf-8"))
            self.assertEqual(4, len({item["conversation_id"] for item in shared["sentences"]}))
            self.assertEqual(
                [(f"L3-{group:02d}", turn) for group in range(1, 5) for turn in range(1, 4)],
                [(item["conversation_id"], item["turn_index"]) for item in shared["sentences"]],
            )
            for conversation_id in sorted({item["conversation_id"] for item in shared["sentences"]}):
                turns = [item for item in shared["sentences"] if item["conversation_id"] == conversation_id]
                self.assertEqual([1, 2, 3], [item["turn_index"] for item in turns])
                self.assertTrue(all(item["turn_count"] == 3 for item in turns))

            for entry in manifest["files"]:
                shard_path = manifest_path.parent / entry["path"]
                self.assertEqual(entry["size_bytes"], shard_path.stat().st_size)
                self.assertEqual(entry["sha256"], sha256_file(shard_path))

    def test_conversation_group_is_not_split_by_small_shard_size(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._build(self._source_csv(root), root, shard_size=4)
            manifest_path = root / "versions" / VERSION / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            conversation_locations = {}
            for entry in manifest["files"]:
                if entry["region"] != "shared":
                    continue
                shard = json.loads((manifest_path.parent / entry["path"]).read_text(encoding="utf-8"))
                for sentence in shard["sentences"]:
                    conversation_locations.setdefault(sentence["conversation_id"], set()).add(entry["path"])
            self.assertEqual(4, len(conversation_locations))
            self.assertTrue(all(len(paths) == 1 for paths in conversation_locations.values()))

    def test_same_input_and_timestamp_produce_identical_json(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = self._source_csv(root)
            first = root / "first"
            second = root / "second"
            self._build(source, first)
            self._build(source, second)
            self.assertEqual(
                {path.relative_to(first): path.read_bytes() for path in first.rglob("*.json")},
                {path.relative_to(second): path.read_bytes() for path in second.rglob("*.json")},
            )

    def test_existing_version_requires_force(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = self._source_csv(root)
            self._build(source, root)
            manifest_path = root / "versions" / VERSION / "manifest.json"
            original_manifest = manifest_path.read_bytes()
            with self.assertRaisesRegex(FileExistsError, VERSION.replace(".", r"\.")):
                self._build(source, root)
            self.assertEqual(original_manifest, manifest_path.read_bytes())
            self._build(source, root, force=True)
            self.assertEqual(original_manifest, manifest_path.read_bytes())

    def test_rejects_easy_compound_source_row(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = self._source_csv(root)
            rows = self._rows(source)
            rows[0]["task_shape"] = "compound"
            self._write_rows(source, rows)
            with self.assertRaisesRegex(ValueError, "easy.*atomic"):
                self._build(source, root)

    def test_rejects_rows_with_extra_or_missing_column_values(self):
        for malformed_kind in ("extra", "missing"):
            with self.subTest(malformed_kind=malformed_kind), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                source = self._source_csv(root)
                lines = source.read_text(encoding="utf-8").splitlines()
                if malformed_kind == "extra":
                    lines[1] += ",unexpected"
                else:
                    lines[1] = lines[1].rsplit(",", 1)[0]
                source.write_text("\n".join(lines) + "\n", encoding="utf-8")

                with self.assertRaisesRegex(ValueError, r"Malformed CSV row 2$"):
                    self._build(source, root)

    def test_rejects_conversation_pool_that_is_not_four_enabled_three_turn_groups(self):
        for malformed_kind in ("uneven", "two_groups", "disabled"):
            with self.subTest(malformed_kind=malformed_kind), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                source = self._source_csv(root)
                rows = self._rows(source)
                conversations = [row for row in rows if row["recording_mode"] == "conversation"]
                if malformed_kind == "uneven":
                    sizes = (2, 3, 3, 4)
                elif malformed_kind == "two_groups":
                    sizes = (6, 6)
                else:
                    conversations[0]["enabled"] = "false"
                    sizes = None
                if sizes:
                    offset = 0
                    for group_number, size in enumerate(sizes, start=1):
                        for turn_index, row in enumerate(conversations[offset:offset + size], start=1):
                            row["conversation_id"] = f"MALFORMED-{group_number}"
                            row["turn_index"] = str(turn_index)
                            row["turn_count"] = str(size)
                        offset += size
                self._write_rows(source, rows)
                with self.assertRaisesRegex(ValueError, "conversation pool"):
                    self._build(source, root)

    def test_rejects_template_id_reused_across_difficulties(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = self._source_csv(root)
            rows = self._rows(source)
            easy = next(row for row in rows if row["difficulty"] == "easy")
            normal = next(row for row in rows if row["difficulty"] == "normal")
            normal["template_id"] = easy["template_id"]
            self._write_rows(source, rows)
            expected_line = rows.index(normal) + 2
            with self.assertRaisesRegex(
                ValueError,
                rf"template_id {easy['template_id']} assigned to multiple difficulties at line {expected_line}$",
            ):
                self._build(source, root)

    def test_rejects_invalid_publication_metadata_before_touching_output(self):
        invalid_values = ("", "   ", " leading", None)
        for field in ("dataset_id", "published_at"):
            for invalid_value in invalid_values:
                with self.subTest(field=field, invalid_value=invalid_value), tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    source = self._source_csv(root)
                    output = root / "output"
                    sentinel = root / "sentinel"
                    sentinel.write_text("unchanged", encoding="utf-8")
                    arguments = {field: invalid_value}

                    with self.assertRaisesRegex(ValueError, rf"{field} must be a non-empty trimmed string$"):
                        self._build(source, output, **arguments)
                    self.assertFalse(output.exists())
                    self.assertEqual("unchanged", sentinel.read_text(encoding="utf-8"))

    def test_refuses_latest_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = self._source_csv(root)
            output = root / "output"
            output.mkdir()
            outside = root / "outside.json"
            outside.write_text("sentinel", encoding="utf-8")
            (output / "latest.json").symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "latest.json.*symlink"):
                self._build(source, output)
            self.assertEqual("sentinel", outside.read_text(encoding="utf-8"))
            self.assertTrue((output / "latest.json").is_symlink())
            self.assertEqual([], list(output.glob(".latest.*.tmp")))


if __name__ == "__main__":
    unittest.main()
