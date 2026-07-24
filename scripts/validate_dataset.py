import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

from dataset_common import (
    DIFFICULTIES,
    LANGUAGE_STYLES,
    REGIONS,
    deterministic_random_key,
    normalize_text,
    sha256_file,
)


SCHEMA_VERSION = 2
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
BASE_SENTENCE_FIELDS = {
    "id", "text", "random_key", "language_style", "enabled",
    "task_shape", "recording_mode", "region_scope",
}
CONVERSATION_FIELDS = {"conversation_id", "turn_index", "turn_count"}
MAX_WORDS = {"easy": 10, "normal": 12, "hard": 10}
EXPECTED_SHAPE = {"easy": "atomic", "normal": "compound", "hard": "ambiguous"}
EXPECTED_TURNS = [
    {"id": "ONE_METER", "distance_meters": 1},
    {"id": "THREE_METERS", "distance_meters": 3, "unlock_after": "ONE_METER"},
]


def _read_json(path: Path, errors: List[str]):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"Invalid JSON {path}: {error}")
        return None


def _validate_policy(manifest, errors):
    policy = manifest.get("collection_policy")
    if not isinstance(policy, dict):
        errors.append("Invalid collection_policy")
        return {}, {}, {}
    counts = policy.get("counts_per_difficulty")
    regional = policy.get("regional_style_per_difficulty")
    conversations = policy.get("conversation_groups_per_difficulty")
    for name, values in (
        ("counts_per_difficulty", counts),
        ("regional_style_per_difficulty", regional),
        ("conversation_groups_per_difficulty", conversations),
    ):
        if not isinstance(values, dict) or set(values) != set(DIFFICULTIES):
            errors.append(f"Invalid {name}")
            continue
        for difficulty, value in values.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"Invalid {name}: {difficulty}")
    if isinstance(counts, dict) and isinstance(regional, dict):
        for difficulty in DIFFICULTIES:
            total = counts.get(difficulty)
            regional_count = regional.get(difficulty)
            if isinstance(total, int) and isinstance(regional_count, int) and not 0 <= regional_count <= total:
                errors.append(f"Invalid regional_style_per_difficulty: {difficulty}")
    if policy.get("turns") != EXPECTED_TURNS:
        errors.append("Invalid turns: expected ONE_METER then unlocked THREE_METERS")
    return (
        counts if isinstance(counts, dict) else {},
        regional if isinstance(regional, dict) else {},
        conversations if isinstance(conversations, dict) else {},
    )


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_shard_path(version_root: Path, relative_path, errors):
    if not isinstance(relative_path, str) or not relative_path:
        errors.append("Unsafe shard path")
        return None
    try:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            errors.append("Unsafe shard path")
            return None
        candidate = version_root / path
        current = version_root
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                errors.append(f"Unsafe shard path symlink: {relative_path}")
                return None
        candidate.resolve(strict=False).relative_to(version_root.resolve(strict=True))
    except (OSError, ValueError):
        errors.append("Unsafe shard path")
        return None
    return candidate


def validate_dataset(root: Path, version: str) -> List[str]:
    errors: List[str] = []
    latest_path = root / "latest.json"
    manifest_path = root / "versions" / version / "manifest.json"
    latest = _read_json(latest_path, errors)
    manifest = _read_json(manifest_path, errors)
    if latest is None or manifest is None:
        return errors
    if not isinstance(latest, dict):
        return [*errors, "Invalid latest.json object"]
    if not isinstance(manifest, dict):
        return [*errors, "Invalid manifest object"]

    expected_manifest_path = f"versions/{version}/manifest.json"
    if latest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"latest.json schema_version must be {SCHEMA_VERSION}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"Manifest schema_version must be {SCHEMA_VERSION}")
    if latest.get("manifest_path") != expected_manifest_path:
        errors.append(f"latest.json points to {latest.get('manifest_path')}, expected {expected_manifest_path}")
    for field in ("dataset_id", "latest_version", "manifest_path", "published_at"):
        if not isinstance(latest.get(field), str) or not latest.get(field):
            errors.append(f"Invalid latest.json {field}")
    for field in ("dataset_id", "dataset_version", "published_at"):
        if not isinstance(manifest.get(field), str) or not manifest.get(field):
            errors.append(f"Invalid manifest {field}")
    if manifest.get("language") != "vi-VN":
        errors.append("Invalid manifest language: expected vi-VN")
    if latest.get("dataset_id") != manifest.get("dataset_id"):
        errors.append("latest.json dataset_id mismatch")
    if latest.get("published_at") != manifest.get("published_at"):
        errors.append("latest.json published_at mismatch")
    if not manifest_path.is_file():
        errors.append("latest.json points to missing manifest")
    if latest.get("latest_version") != version:
        errors.append(f"latest.json version mismatch: {latest.get('latest_version')} != {version}")
    if manifest.get("dataset_version") != version:
        errors.append(f"Manifest version mismatch: {manifest.get('dataset_version')} != {version}")

    requested_counts, requested_regional, requested_conversations = _validate_policy(manifest, errors)
    files = manifest.get("files")
    if not isinstance(files, list):
        errors.append("Invalid files list")
        return errors
    if len(files) != 10:
        errors.append(f"Manifest must declare 10 shards, found {len(files)}")

    dataset_id = manifest.get("dataset_id")
    version_root = manifest_path.parent
    declared_paths = {
        entry.get("path") for entry in files
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    actual_paths = {
        path.relative_to(version_root).as_posix()
        for path in (version_root / "sentences").rglob("*.json")
    }
    for undeclared in sorted(actual_paths - declared_paths):
        errors.append(f"Undeclared shard: {undeclared}")
    for missing in sorted(declared_paths - actual_paths):
        errors.append(f"Missing shard: {missing}")

    seen_ids = set()
    seen_texts = set()
    group_counts = Counter()
    style_counts = Counter()
    conversation_rows = defaultdict(list)
    conversation_shards = defaultdict(set)
    conversation_positions = defaultdict(list)
    total_records = 0

    for entry in files:
        if not isinstance(entry, dict):
            errors.append("Invalid file entry")
            continue
        relative_path = entry.get("path")
        shard_path = _safe_shard_path(version_root, relative_path, errors)
        if shard_path is None:
            continue
        for field in ("shard", "count", "enabled_count", "size_bytes"):
            if not _is_int(entry.get(field)) or entry.get(field) < 0:
                errors.append(f"Invalid file entry {field}: {relative_path}")
        if not isinstance(entry.get("sha256"), str):
            errors.append(f"Invalid file entry sha256: {relative_path}")
        if not isinstance(entry.get("language_style_counts"), dict):
            errors.append(f"Invalid file entry language_style_counts: {relative_path}")
        if not shard_path.is_file():
            continue
        if shard_path.stat().st_size != entry.get("size_bytes"):
            errors.append(f"Size mismatch: {relative_path}")
        if sha256_file(shard_path) != entry.get("sha256"):
            errors.append(f"SHA-256 mismatch: {relative_path}")
        shard = _read_json(shard_path, errors)
        if not isinstance(shard, dict):
            continue
        region = shard.get("region")
        difficulty = shard.get("difficulty")
        if shard.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"Shard schema_version mismatch: {relative_path}")
        if region != entry.get("region") or difficulty != entry.get("difficulty"):
            errors.append(f"Shard group mismatch: {relative_path}")
        if shard.get("shard") != entry.get("shard"):
            errors.append(f"Shard number mismatch: {relative_path}")
        if shard.get("dataset_id") != dataset_id or shard.get("dataset_version") != version:
            errors.append(f"Dataset identity mismatch: {relative_path}")
        if region not in (*REGIONS, "shared") or difficulty not in DIFFICULTIES:
            errors.append(f"Invalid shard group: {relative_path}")
            continue
        if region == "shared" and difficulty != "hard":
            errors.append(f"Shared shard must be hard: {relative_path}")

        sentences = shard.get("sentences")
        if not isinstance(sentences, list):
            errors.append(f"Invalid sentences list: {relative_path}")
            continue
        if len(sentences) != entry.get("count"):
            errors.append(f"Shard count mismatch: {relative_path}")
        shard_style_counts = Counter()
        enabled_count = 0
        for sentence in sentences:
            total_records += 1
            if not isinstance(sentence, dict):
                errors.append(f"Invalid sentence fields: {relative_path}")
                continue
            recording_mode = sentence.get("recording_mode")
            expected_fields = BASE_SENTENCE_FIELDS | (CONVERSATION_FIELDS if recording_mode == "conversation" else set())
            if set(sentence) != expected_fields:
                label = "conversation fields" if recording_mode == "conversation" else "sentence fields"
                errors.append(f"Invalid {label}: {relative_path}")
                continue
            item_id = sentence["id"]
            text = sentence["text"]
            style = sentence["language_style"]
            enabled = sentence["enabled"]
            if not isinstance(item_id, str) or not ID_PATTERN.fullmatch(item_id):
                errors.append(f"Invalid id in {relative_path}: {item_id}")
                continue
            if item_id in seen_ids:
                errors.append(f"Duplicate id: {item_id}")
            seen_ids.add(item_id)
            normalized = normalize_text(text) if isinstance(text, str) else ""
            if not normalized:
                errors.append(f"Empty text: {item_id}")
            else:
                if text != normalized:
                    errors.append(f"Non-normalized text: {item_id}")
                if len(normalized.rstrip(".?!").split()) > MAX_WORDS[difficulty]:
                    errors.append(f"Sentence too long: {item_id}")
                text_key = normalized.casefold()
                if text_key in seen_texts:
                    errors.append(f"Duplicate text: {item_id}")
                seen_texts.add(text_key)
                if "[CONTEXT]" in text or "{" in text or "}" in text:
                    errors.append(f"Unresolved placeholder: {item_id}")
            if sentence["random_key"] != deterministic_random_key(dataset_id, item_id):
                errors.append(f"Random key mismatch: {item_id}")
            if style not in LANGUAGE_STYLES:
                errors.append(f"Invalid language style: {item_id}")
            else:
                shard_style_counts[style] += 1
                style_counts[(region, difficulty, style)] += 1
            if not isinstance(enabled, bool):
                errors.append(f"Invalid enabled value: {item_id}")
            elif enabled:
                enabled_count += 1
                group_counts[(region, difficulty)] += 1

            task_shape = sentence["task_shape"]
            region_scope = sentence["region_scope"]
            if recording_mode == "single":
                expected_shape = EXPECTED_SHAPE[difficulty]
                if task_shape != expected_shape:
                    allowed = "ambiguous or conversation" if difficulty == "hard" else expected_shape
                    errors.append(f"Invalid {difficulty} task shape: expected {allowed}: {item_id}")
                if region not in REGIONS or region_scope != region:
                    errors.append(f"Invalid single region scope: {item_id}")
            elif recording_mode == "conversation":
                if region != "shared" or difficulty != "hard" or task_shape != "conversation" or region_scope != "all":
                    errors.append(f"Invalid hard conversation taxonomy: {item_id}")
                conversation_id = sentence["conversation_id"]
                if not isinstance(conversation_id, str) or not conversation_id:
                    errors.append("Invalid conversation id")
                else:
                    conversation_rows[conversation_id].append(sentence)
                    conversation_shards[conversation_id].add(relative_path)
                    conversation_positions[relative_path].append(
                        (conversation_id, sentence["turn_index"])
                    )
            else:
                errors.append(f"Invalid recording mode: {item_id}")

        if enabled_count != entry.get("enabled_count"):
            errors.append(f"Enabled count mismatch: {relative_path}")
        expected_styles = {"standard": shard_style_counts["standard"], "regional": shard_style_counts["regional"]}
        if expected_styles != entry.get("language_style_counts"):
            errors.append(f"Shard style count mismatch: {relative_path}")

    exact_pool = len(conversation_rows) == 4 and sum(map(len, conversation_rows.values())) == 12
    for conversation_id, turns in conversation_rows.items():
        if len(conversation_shards[conversation_id]) != 1:
            errors.append(f"Conversation {conversation_id} split across shards")
        indices = [turn.get("turn_index") for turn in turns]
        counts = [turn.get("turn_count") for turn in turns]
        if not counts or not all(isinstance(value, int) and not isinstance(value, bool) for value in indices + counts):
            errors.append(f"Invalid conversation turn indices: {conversation_id}")
            continue
        if len(turns) != 3 or any(value != 3 for value in counts) or sorted(indices) != [1, 2, 3]:
            errors.append(f"Invalid conversation turn indices: {conversation_id}")
            exact_pool = False
        if any(turn["language_style"] != "standard" for turn in turns):
            errors.append(f"Invalid conversation style: {conversation_id}")
    if not exact_pool:
        errors.append("Conversation pool must contain exactly 4 groups of 3 turns")
    for relative_path, positions in conversation_positions.items():
        for conversation_id in {item[0] for item in positions}:
            group_positions = [index for index, item in enumerate(positions) if item[0] == conversation_id]
            physical_turns = [item[1] for item in positions if item[0] == conversation_id]
            if (
                group_positions != list(range(group_positions[0], group_positions[0] + 3))
                or physical_turns != [1, 2, 3]
            ):
                errors.append(
                    f"Conversation {conversation_id} must use contiguous physical turn order [1,2,3]"
                )

    if total_records != 912:
        errors.append(f"Total sentence count mismatch: {total_records} != 912")
    manifest_counts: Dict = manifest.get("counts", {})
    if not isinstance(manifest_counts, dict):
        errors.append("Invalid manifest counts")
        manifest_counts = {}
    if manifest_counts.get("total") != total_records:
        errors.append(f"Manifest count mismatch: total={manifest_counts.get('total')} actual={total_records}")
    for region in REGIONS:
        region_total = 0
        for difficulty in DIFFICULTIES:
            actual = group_counts[(region, difficulty)]
            region_total += actual
            region_manifest_counts = manifest_counts.get(region, {})
            if not isinstance(region_manifest_counts, dict):
                region_manifest_counts = {}
            if region_manifest_counts.get(difficulty) != actual:
                errors.append(f"Manifest count mismatch: {region}/{difficulty}")
            standard = style_counts[(region, difficulty, "standard")]
            regional = style_counts[(region, difficulty, "regional")]
            if (standard, regional) != (70, 30):
                errors.append(f"Invalid style split: {region}/{difficulty} standard={standard}, regional={regional}")
            requested_total = requested_counts.get(difficulty)
            requested_regional_count = requested_regional.get(difficulty)
            if isinstance(requested_total, int) and isinstance(requested_regional_count, int):
                if regional < requested_regional_count or standard < requested_total - requested_regional_count:
                    errors.append(f"regional_style_per_difficulty unavailable: {region}/{difficulty}")
        if region_manifest_counts.get("total") != region_total:
            errors.append(f"Manifest count mismatch: {region}/total")

    shared_count = group_counts[("shared", "hard")]
    shared_manifest_counts = manifest_counts.get("shared", {})
    if not isinstance(shared_manifest_counts, dict):
        shared_manifest_counts = {}
    if shared_manifest_counts.get("hard") != shared_count or shared_manifest_counts.get("total") != shared_count:
        errors.append("Manifest count mismatch: shared/hard")
    for difficulty in DIFFICULTIES:
        requested = requested_conversations.get(difficulty)
        available = len(conversation_rows) if difficulty == "hard" else 0
        total = requested_counts.get(difficulty)
        regional = requested_regional.get(difficulty)
        turns_per_group = 3
        if isinstance(requested, int) and (
            requested > available
            or (isinstance(total, int) and isinstance(regional, int) and requested * turns_per_group + regional > total)
        ):
            errors.append(f"conversation_groups_per_difficulty unavailable: {difficulty}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Lumi voice dataset artifacts")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    errors = validate_dataset(args.root, args.version)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("VALID: 912 sentences, 9 regional shards, 1 shared conversation shard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
