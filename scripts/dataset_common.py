import hashlib
import json
from pathlib import Path
from typing import Any


REGIONS = ("north", "central", "south")
DIFFICULTIES = ("easy", "normal", "hard")
LANGUAGE_STYLES = ("standard", "regional")
DIFFICULTY_CODE = {"easy": "E", "normal": "N", "hard": "H"}
SCHEMA_VERSION = 1


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def sentence_id(region: str, difficulty: str, sequence: int) -> str:
    return f"VI_{region.upper()}_{DIFFICULTY_CODE[difficulty]}_{sequence:04d}"


def deterministic_random_key(dataset_id: str, item_id: str) -> int:
    digest = hashlib.sha256(f"{dataset_id}:{item_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def canonical_json_bytes(value: Any) -> bytes:
    content = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"{content}\n".encode("utf-8")


def write_canonical_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
