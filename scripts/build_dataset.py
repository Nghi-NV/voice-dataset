import argparse
import csv
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

from dataset_common import (
    DIFFICULTIES,
    LANGUAGE_STYLES,
    REGIONS,
    canonical_json_bytes,
    deterministic_random_key,
    normalize_text,
    sha256_file,
    write_canonical_json,
)


SCHEMA_VERSION = 2
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
EXPECTED_FIELDS = (
    "id", "text", "region", "difficulty", "language_style", "enabled",
    "task_shape", "recording_mode", "region_scope", "conversation_id",
    "turn_index", "turn_count", "template_id",
)
EXPECTED_SHAPE = {"easy": "atomic", "normal": "compound", "hard": "ambiguous"}


def _load_rows(input_path: Path) -> List[Dict[str, object]]:
    with input_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != EXPECTED_FIELDS:
            raise ValueError(f"Unexpected CSV header: {reader.fieldnames}")
        raw_rows = list(reader)

    rows = []
    seen_ids = set()
    seen_texts = set()
    template_difficulties = {}
    for line_number, row in enumerate(raw_rows, start=2):
        if None in row or any(row.get(field) is None for field in EXPECTED_FIELDS):
            raise ValueError(f"Malformed CSV row {line_number}")
        item_id = row["id"].strip()
        text = normalize_text(row["text"])
        region = row["region"].strip()
        difficulty = row["difficulty"].strip()
        language_style = row["language_style"].strip()
        enabled_value = row["enabled"].strip().lower() or "true"
        task_shape = row["task_shape"].strip()
        recording_mode = row["recording_mode"].strip()
        region_scope = row["region_scope"].strip()
        conversation_id = row["conversation_id"].strip()
        turn_index_value = row["turn_index"].strip()
        turn_count_value = row["turn_count"].strip()
        template_id = row["template_id"].strip()

        if not ID_PATTERN.fullmatch(item_id):
            raise ValueError(f"Invalid id at line {line_number}: {item_id}")
        if item_id in seen_ids:
            raise ValueError(f"Duplicate id at line {line_number}: {item_id}")
        if not text:
            raise ValueError(f"Empty text at line {line_number}")
        text_key = text.casefold()
        if text_key in seen_texts:
            raise ValueError(f"Duplicate text at line {line_number}: {text}")
        if "[CONTEXT]" in text or "{" in text or "}" in text:
            raise ValueError(f"Unresolved placeholder at line {line_number}: {item_id}")
        if difficulty not in DIFFICULTIES or language_style not in LANGUAGE_STYLES:
            raise ValueError(f"Invalid taxonomy at line {line_number}: {item_id}")
        if enabled_value not in {"true", "false"}:
            raise ValueError(f"Invalid enabled at line {line_number}: {enabled_value}")
        if not template_id or not ID_PATTERN.fullmatch(template_id):
            raise ValueError(f"Invalid template_id at line {line_number}")
        previous_difficulty = template_difficulties.get(template_id)
        if previous_difficulty is not None and previous_difficulty != difficulty:
            raise ValueError(
                f"template_id {template_id} assigned to multiple difficulties "
                f"at line {line_number}"
            )
        template_difficulties[template_id] = difficulty

        if recording_mode == "single":
            if region not in REGIONS or region_scope != region:
                raise ValueError(f"Invalid single region at line {line_number}: {item_id}")
            if task_shape != EXPECTED_SHAPE[difficulty]:
                raise ValueError(
                    f"Invalid {difficulty} task_shape at line {line_number}: "
                    f"expected {EXPECTED_SHAPE[difficulty]}"
                )
            if conversation_id or turn_index_value or turn_count_value:
                raise ValueError(f"Single row has conversation metadata at line {line_number}")
            turn_index = turn_count = None
        elif recording_mode == "conversation":
            if (region, region_scope, difficulty, task_shape, language_style) != (
                "all", "all", "hard", "conversation", "standard"
            ):
                raise ValueError(f"Invalid conversation taxonomy at line {line_number}: {item_id}")
            try:
                turn_index = int(turn_index_value)
                turn_count = int(turn_count_value)
            except ValueError as error:
                raise ValueError(f"Invalid conversation turn at line {line_number}") from error
            if not conversation_id or turn_index <= 0 or turn_count <= 0:
                raise ValueError(f"Invalid conversation metadata at line {line_number}")
        else:
            raise ValueError(f"Invalid recording_mode at line {line_number}: {recording_mode}")

        seen_ids.add(item_id)
        seen_texts.add(text_key)
        rows.append({
            "id": item_id,
            "text": text,
            "region": region,
            "difficulty": difficulty,
            "language_style": language_style,
            "enabled": enabled_value == "true",
            "task_shape": task_shape,
            "recording_mode": recording_mode,
            "region_scope": region_scope,
            "conversation_id": conversation_id or None,
            "turn_index": turn_index,
            "turn_count": turn_count,
            "template_id": template_id,
        })

    _validate_counts(rows)
    return rows


def _validate_counts(rows: List[Dict[str, object]]) -> None:
    singles = [row for row in rows if row["recording_mode"] == "single"]
    conversations = [row for row in rows if row["recording_mode"] == "conversation"]
    if len(rows) != 912 or len(singles) != 900 or len(conversations) != 12:
        raise ValueError(
            f"Expected 912 rows (900 single, 12 conversation), found "
            f"{len(rows)} ({len(singles)} single, {len(conversations)} conversation)"
        )
    counts = Counter(
        (row["region"], row["difficulty"], row["language_style"])
        for row in singles if row["enabled"]
    )
    for region in REGIONS:
        for difficulty in DIFFICULTIES:
            split = (counts[(region, difficulty, "standard")], counts[(region, difficulty, "regional")])
            if split != (70, 30):
                raise ValueError(f"Invalid split for {region}/{difficulty}: {split}")

    groups = defaultdict(list)
    for row in conversations:
        groups[row["conversation_id"]].append(row)
    if len(groups) != 4 or any(not row["enabled"] for row in conversations):
        raise ValueError("Invalid conversation pool: expected 4 enabled groups of 3 turns")
    for conversation_id, turns in groups.items():
        ordered = sorted(turns, key=lambda item: item["turn_index"])
        if (
            len(ordered) != 3
            or [row["turn_index"] for row in ordered] != [1, 2, 3]
            or any(row["turn_count"] != 3 for row in ordered)
        ):
            raise ValueError("Invalid conversation pool: expected 4 enabled groups of 3 turns")


def _chunks(values: List[Dict[str, object]], size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _conversation_chunks(rows: List[Dict[str, object]], size: int):
    groups = defaultdict(list)
    for row in rows:
        groups[row["conversation_id"]].append(row)
    chunk = []
    for conversation_id in sorted(groups):
        group = sorted(groups[conversation_id], key=lambda item: item["turn_index"])
        if chunk and len(chunk) + len(group) > size:
            yield chunk
            chunk = []
        chunk.extend(group)
    if chunk:
        yield chunk


def _published_sentence(item, dataset_id):
    sentence = {
        "id": item["id"],
        "text": item["text"],
        "random_key": deterministic_random_key(dataset_id, item["id"]),
        "language_style": item["language_style"],
        "enabled": item["enabled"],
        "task_shape": item["task_shape"],
        "recording_mode": item["recording_mode"],
        "region_scope": item["region_scope"],
    }
    if item["recording_mode"] == "conversation":
        sentence.update({
            "conversation_id": item["conversation_id"],
            "turn_index": item["turn_index"],
            "turn_count": item["turn_count"],
        })
    return sentence


def _write_shard(temporary_version, relative_path, dataset_id, version, region, difficulty, shard_number, chunk):
    shard_path = temporary_version / relative_path
    sentences = [_published_sentence(item, dataset_id) for item in chunk]
    write_canonical_json(shard_path, {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_version": version,
        "region": region,
        "difficulty": difficulty,
        "shard": shard_number,
        "sentences": sentences,
    })
    style_counts = Counter(item["language_style"] for item in chunk)
    return {
        "path": relative_path.as_posix(),
        "region": region,
        "difficulty": difficulty,
        "shard": shard_number,
        "count": len(chunk),
        "enabled_count": sum(bool(item["enabled"]) for item in chunk),
        "language_style_counts": {"standard": style_counts["standard"], "regional": style_counts["regional"]},
        "size_bytes": shard_path.stat().st_size,
        "sha256": sha256_file(shard_path),
    }


def _write_latest_atomic(output_root: Path, value) -> None:
    latest_path = output_root / "latest.json"
    if latest_path.is_symlink():
        raise ValueError("latest.json destination must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".latest.", suffix=".tmp", dir=output_root
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(canonical_json_bytes(value))
            output.flush()
            os.fsync(output.fileno())
        if latest_path.is_symlink():
            raise ValueError("latest.json destination must not be a symlink")
        os.replace(temporary_path, latest_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def build_dataset(input_path: Path, output_root: Path, dataset_id: str, version: str,
                  shard_size: int, published_at: str, force: bool = False) -> Path:
    for field_name, value in (("dataset_id", dataset_id), ("published_at", published_at)):
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{field_name} must be a non-empty trimmed string")
    if shard_size <= 0:
        raise ValueError("shard_size must be positive")
    rows = _load_rows(input_path)
    singles = [row for row in rows if row["recording_mode"] == "single"]
    conversations = [row for row in rows if row["recording_mode"] == "conversation"]
    output_root.mkdir(parents=True, exist_ok=True)
    if (output_root / "latest.json").is_symlink():
        raise ValueError("latest.json destination must not be a symlink")
    versions_root = output_root / "versions"
    versions_root.mkdir(parents=True, exist_ok=True)
    target_version = versions_root / version
    if target_version.exists() and not force:
        raise FileExistsError(f"Dataset version already exists: {version}")

    temporary_version = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=versions_root))
    try:
        groups = defaultdict(list)
        for row in singles:
            groups[(row["region"], row["difficulty"])].append(row)
        counts = {"total": len(rows)}
        files = []
        for region in REGIONS:
            region_counts = {"total": 0}
            for difficulty in DIFFICULTIES:
                group = sorted(groups[(region, difficulty)], key=lambda item: item["id"])
                region_counts[difficulty] = len(group)
                region_counts["total"] += len(group)
                for shard_number, chunk in enumerate(_chunks(group, shard_size), start=1):
                    path = Path("sentences") / region / f"{difficulty}-{shard_number:04d}.json"
                    files.append(_write_shard(temporary_version, path, dataset_id, version, region, difficulty, shard_number, chunk))
            counts[region] = region_counts

        shared_chunks = list(_conversation_chunks(conversations, shard_size))
        counts["shared"] = {"total": len(conversations), "hard": len(conversations)}
        for shard_number, chunk in enumerate(shared_chunks, start=1):
            path = Path("sentences/shared") / f"hard-conversation-{shard_number:04d}.json"
            files.append(_write_shard(temporary_version, path, dataset_id, version, "shared", "hard", shard_number, chunk))

        write_canonical_json(temporary_version / "manifest.json", {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "dataset_version": version,
            "language": "vi-VN",
            "published_at": published_at,
            "collection_policy": {
                "counts_per_difficulty": {"easy": 10, "normal": 10, "hard": 10},
                "regional_style_per_difficulty": {"easy": 3, "normal": 3, "hard": 3},
                "conversation_groups_per_difficulty": {"easy": 0, "normal": 0, "hard": 2},
                "turns": [
                    {"id": "ONE_METER", "distance_meters": 1},
                    {"id": "THREE_METERS", "distance_meters": 3, "unlock_after": "ONE_METER"},
                ],
            },
            "counts": counts,
            "files": files,
        })

        backup_version = versions_root / f".{version}.backup"
        if backup_version.exists():
            shutil.rmtree(backup_version)
        if target_version.exists():
            target_version.rename(backup_version)
        try:
            temporary_version.rename(target_version)
        except Exception:
            if backup_version.exists() and not target_version.exists():
                backup_version.rename(target_version)
            raise
        if backup_version.exists():
            shutil.rmtree(backup_version)

        _write_latest_atomic(output_root, {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "latest_version": version,
            "manifest_path": f"versions/{version}/manifest.json",
            "published_at": published_at,
        })
        return target_version / "manifest.json"
    finally:
        if temporary_version.exists():
            shutil.rmtree(temporary_version)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build versioned Lumi voice dataset JSON")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--shard-size", type=int, default=5000)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = build_dataset(args.input, args.output, args.dataset_id, args.version, args.shard_size, args.published_at, args.force)
    print(f"Built dataset manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
