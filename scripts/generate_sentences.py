import argparse
import csv
import hashlib
import itertools
import json
import string
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from dataset_common import DIFFICULTIES, REGIONS, normalize_text, sentence_id


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = ROOT / "data" / "catalog.json"
DEFAULT_TEMPLATES_PATH = ROOT / "data" / "templates.json"
TARGET_PER_GROUP = 100
STANDARD_PER_GROUP = 70
REGIONAL_PER_GROUP = 30
CSV_FIELDS = (
    "id",
    "text",
    "region",
    "difficulty",
    "language_style",
    "enabled",
    "task_shape",
    "recording_mode",
    "region_scope",
    "conversation_id",
    "turn_index",
    "turn_count",
    "template_id",
)


def _slot_names(template_text: str) -> List[str]:
    return [
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template_text)
        if field_name
    ]


def _value_source(slot: str, template: Dict, catalog: Dict) -> Iterable[object]:
    capability = template["required_capability"]
    intent = template["intent"]
    if slot == "ROOM":
        return catalog["rooms"]
    if slot == "FLOOR":
        return catalog["floors"]
    if slot == "AREA":
        return catalog["areas"]
    if slot == "NONEXISTENT_ROOM":
        return catalog["nonexistent"]["rooms"]
    if slot == "NONEXISTENT_DEVICE":
        return catalog["nonexistent"]["devices"]
    if slot == "DEVICE":
        return catalog["devices"][capability]
    if slot == "DEVICE_NAME":
        return catalog["device_names"]
    if slot == "VALUE":
        if intent == "set_temperature" or capability == "temperature":
            return catalog["values"]["temperature"]
        if intent == "set_brightness" or capability == "brightness":
            return catalog["values"]["brightness"]
        if intent == "set_curtain":
            return catalog["values"]["curtain"]
        if intent == "set_fan_level" or capability == "fan_level":
            return catalog["values"]["fan_level"]
    raise ValueError(f"Unsupported slot {slot} in {template['id']}")


def _capitalize_first(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def _expand_template(template: Dict, catalog: Dict) -> Iterable[str]:
    slots = _slot_names(template["text"])
    if not slots:
        yield _capitalize_first(normalize_text(template["text"]))
        return

    sources = [_value_source(slot, template, catalog) for slot in slots]
    for values in itertools.product(*sources):
        replacements = dict(zip(slots, values))
        yield _capitalize_first(normalize_text(template["text"].format(**replacements)))


def _candidates(
    templates: List[Dict],
    catalog: Dict,
    dataset_id: str,
    seed: int,
    region: str,
    difficulty: str,
    language_style: str,
) -> List[Dict]:
    unique = {}
    for template in templates:
        if template["language_style"] != language_style:
            continue
        if language_style == "regional" and template.get("region") != region:
            continue
        for text in _expand_template(template, catalog):
            unique.setdefault(text.casefold(), {"text": text, "template": template})

    def stable_order(candidate: Dict) -> str:
        value = (
            f"{dataset_id}:{seed}:{region}:{difficulty}:{language_style}:"
            f"{candidate['template']['id']}:{candidate['text']}"
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    return sorted(unique.values(), key=stable_order)


def _conversation_rows(conversations: List[Dict], dataset_id: str) -> List[Dict]:
    rows = []
    for conversation in conversations:
        turn_count = len(conversation["turns"])
        for turn_index, text in enumerate(conversation["turns"], start=1):
            rows.append(
                {
                    "id": f"{dataset_id}_{conversation['id']}_T{turn_index}",
                    "text": text,
                    "region": "all",
                    "difficulty": "hard",
                    "language_style": "standard",
                    "enabled": "true",
                    "task_shape": "conversation",
                    "recording_mode": "conversation",
                    "region_scope": "all",
                    "conversation_id": conversation["id"],
                    "turn_index": str(turn_index),
                    "turn_count": str(turn_count),
                    "template_id": f"{conversation['id']}-T{turn_index}",
                }
            )
    return rows


def generate_rows(
    seed: int = 20260723,
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    templates_path: Path = DEFAULT_TEMPLATES_PATH,
    dataset_id: str = "lumi-voice-vi",
) -> List[Dict]:
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    payload = json.loads(Path(templates_path).read_text(encoding="utf-8"))
    conversations = _conversation_rows(payload["conversations"], dataset_id)
    used_texts = {row["text"].casefold() for row in conversations}
    rows = []

    for region in REGIONS:
        for difficulty in DIFFICULTIES:
            group = []
            for language_style, target in (
                ("standard", STANDARD_PER_GROUP),
                ("regional", REGIONAL_PER_GROUP),
            ):
                available = [
                    candidate
                    for candidate in _candidates(
                        payload["templates"][difficulty],
                        catalog,
                        dataset_id,
                        seed,
                        region,
                        difficulty,
                        language_style,
                    )
                    if candidate["text"].casefold() not in used_texts
                ]
                if len(available) < target:
                    raise ValueError(
                        f"Insufficient candidates for {region}/{difficulty}/"
                        f"{language_style}: need {target}, found {len(available)}"
                    )
                for candidate in available[:target]:
                    used_texts.add(candidate["text"].casefold())
                    group.append((candidate, language_style))

            if len(group) != TARGET_PER_GROUP:
                raise AssertionError(
                    f"Unexpected group size for {region}/{difficulty}: {len(group)}"
                )
            for sequence, (candidate, language_style) in enumerate(group, start=1):
                template = candidate["template"]
                rows.append(
                    {
                        "id": sentence_id(region, difficulty, sequence),
                        "text": candidate["text"],
                        "region": region,
                        "difficulty": difficulty,
                        "language_style": language_style,
                        "enabled": "true",
                        "task_shape": template["task_shape"],
                        "recording_mode": "single",
                        "region_scope": region,
                        "conversation_id": "",
                        "turn_index": "",
                        "turn_count": "",
                        "template_id": template["id"],
                    }
                )

    return rows + conversations


def generate_sentences(
    catalog_path: Path,
    templates_path: Path,
    output_path: Path,
    dataset_id: str,
    seed: int = 20260723,
) -> None:
    rows = generate_rows(seed, catalog_path, templates_path, dataset_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Lumi Vietnamese voice corpus")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--templates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--seed", type=int, default=20260723)
    args = parser.parse_args(argv)

    generate_sentences(
        args.catalog,
        args.templates,
        args.output,
        args.dataset_id,
        args.seed,
    )
    print("Generated 900 single utterances and 12 shared L3 turns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
