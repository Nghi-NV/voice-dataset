# Voice Dataset Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate, validate, and publish exactly 900 app-consumable Vietnamese smart-home recording sentences, split into 100 sentences for each region/difficulty combination.

**Architecture:** Local-only Python tools deterministically expand capability-safe L1/L2/L5 templates into a UTF-8 CSV, package the CSV into versioned JSON shards, and validate all counts, checksums, and content invariants. The Git repository `main` branch publishes only `latest.json` and `versions/**/*.json`; source CSV, tools, tests, plans, and documentation stay excluded locally.

**Tech Stack:** Python 3.9+ standard library, `unittest`, UTF-8 CSV/JSON, SHA-256, Git/GitHub.

---

## File Map

Local-only files, excluded through `.git/info/exclude`:

- `data/catalog.json`: typed rooms, devices, values, nonexistent-context names, and regional wording.
- `data/templates.json`: L1/L2/L5 templates with intent, capability, and language-style metadata.
- `data/source.csv`: flattened 900-sentence intermediate artifact.
- `scripts/dataset_common.py`: shared schema constants, normalization, IDs, hashes, and JSON helpers.
- `scripts/generate_sentences.py`: deterministic template expansion into `source.csv`.
- `scripts/build_dataset.py`: shard, manifest, checksum, and `latest.json` generation.
- `scripts/validate_dataset.py`: independent validation of published artifacts.
- `tests/test_dataset_common.py`: shared helper tests.
- `tests/__init__.py`: package marker for direct `unittest` module execution.
- `tests/test_generate_sentences.py`: content-generation invariant tests.
- `tests/test_build_dataset.py`: deterministic packaging tests.
- `tests/test_validate_dataset.py`: corruption and contract validation tests.
- `README.md`: local operator workflow.
- `docs/dataset-format.md`: exact public JSON contract for Android integration.

Published files, and the only files allowed in Git:

- `latest.json`: current immutable version pointer.
- `versions/2026.07.1/manifest.json`: dataset metadata, selection policy, counts, and shard checksums.
- `versions/2026.07.1/sentences/north/*.json`, `central/*.json`, and
  `south/*.json`: nine sentence shards.

## Task 1: Lock the Publication Boundary

**Files:**
- Modify local-only: `.git/info/exclude`
- Test: Git index allowlist

- [ ] **Step 1: Verify the current published tree is empty**

Run:

```bash
git ls-tree -r --name-only HEAD
```

Expected: no output.

- [ ] **Step 2: Ensure local paths are excluded**

Ensure `.git/info/exclude` contains exactly these relevant entries:

```text
data/
docs/
scripts/
tests/
README.md
__pycache__/
*.pyc
```

- [ ] **Step 3: Verify the publication allowlist**

Run:

```bash
tracked="$(git ls-files | grep -Ev '^(latest\.json|versions/.+\.json)$' || true)"
test -z "$tracked"
```

Expected: exit code 0 and no output.

## Task 2: Implement Shared Dataset Primitives

**Files:**
- Create: `scripts/dataset_common.py`
- Create: `tests/__init__.py`
- Create: `tests/test_dataset_common.py`

- [ ] **Step 1: Write failing helper tests**

Create tests that assert:

```python
from dataset_common import deterministic_random_key, normalize_text, sentence_id


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  Bật   đèn\nphòng khách. ") == "Bật đèn phòng khách."


def test_random_key_is_stable_and_sqlite_safe():
    first = deterministic_random_key("lumi-voice-vi", "VI_NORTH_E_0001")
    second = deterministic_random_key("lumi-voice-vi", "VI_NORTH_E_0001")
    assert first == second
    assert 0 <= first <= 2_147_483_647


def test_sentence_id_uses_region_and_difficulty():
    assert sentence_id("north", "easy", 1) == "VI_NORTH_E_0001"
    assert sentence_id("central", "normal", 12) == "VI_CENTRAL_N_0012"
    assert sentence_id("south", "hard", 100) == "VI_SOUTH_H_0100"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_dataset_common -v
```

Expected: import failure because `dataset_common.py` does not exist.

- [ ] **Step 3: Implement the shared contract**

Define these exact constants and functions:

```python
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
```

Also implement canonical UTF-8 JSON writing with `ensure_ascii=False`, two-space indentation, sorted dictionary keys, and exactly one trailing newline.

- [ ] **Step 4: Run helper tests and verify GREEN**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_dataset_common -v
```

Expected: all tests pass.

## Task 3: Define Capability-Safe Catalogs and Templates

**Files:**
- Create: `data/catalog.json`
- Create: `data/templates.json`
- Test: `tests/test_generate_sentences.py`

- [ ] **Step 1: Create the typed catalog**

Use these capability families:

```json
{
  "rooms": ["phòng khách", "phòng ngủ chính", "phòng ngủ nhỏ", "phòng bếp", "phòng ăn", "phòng làm việc", "phòng thờ"],
  "floors": ["tầng một", "tầng hai", "tầng ba"],
  "areas": ["khu vực sofa", "bàn ăn", "quầy bếp", "ban công", "hành lang"],
  "devices": {
    "toggle": ["đèn trần", "đèn ngủ", "quạt trần", "điều hòa"],
    "open_close": ["rèm cửa", "rèm bên trái", "rèm bên phải"],
    "brightness": ["đèn trần", "đèn bàn", "đèn hắt trần"],
    "temperature": ["điều hòa", "máy lạnh"],
    "fan_level": ["quạt trần", "quạt đứng"]
  },
  "values": {
    "temperature": [22, 23, 24, 25, 26, 27, 28],
    "brightness": [20, 30, 40, 50, 60, 70, 80, 100],
    "curtain": [25, 30, 40, 45, 50, 60, 75],
    "fan_level": [1, 2, 3]
  },
  "nonexistent": {
    "rooms": ["phòng đọc sách", "phòng chơi", "phòng ngủ khách", "phòng tập", "phòng giặt"],
    "devices": ["đèn hồ cá", "quạt ban công", "rèm phòng đọc", "điều hòa phòng kho"]
  }
}
```

- [ ] **Step 2: Create template records with explicit metadata**

Every template object uses this schema:

```json
{
  "template_id": "L2-TEMP-001",
  "difficulty": "normal",
  "language_style": "standard",
  "intent": "set_temperature",
  "required_capability": "temperature",
  "text": "Đặt {DEVICE} ở {ROOM} thành {VALUE} độ."
}
```

Include L1 basic/natural equivalents for `easy`, L2 parameter/state templates for `normal`, and L5 ambiguous/nonexistent-context templates for `hard`. Regional templates are separate per region and use restrained vocabulary such as `giúp`, `giùm`, `máy lạnh`, or `màn cửa`; no obscure slang is allowed.

- [ ] **Step 3: Write schema and capability tests**

Tests must load both JSON files and assert:

```python
assert template["difficulty"] in {"easy", "normal", "hard"}
assert template["language_style"] in {"standard", "regional"}
assert template["required_capability"] in allowed_capabilities
assert "[CONTEXT]" not in template["text"]
```

Also assert L1 templates only map to `easy`, L2 to `normal`, and L5 to `hard` by checking the `template_id` prefix.

- [ ] **Step 4: Run catalog/template tests**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_generate_sentences.TemplateContractTest -v
```

Expected: all template contract tests pass.

## Task 4: Generate Exactly 900 Sentences

**Files:**
- Create: `scripts/generate_sentences.py`
- Modify: `tests/test_generate_sentences.py`
- Generate local-only: `data/source.csv`

- [ ] **Step 1: Write failing generation tests**

Generate into a temporary CSV and assert:

```python
assert len(rows) == 900
assert len({row["id"] for row in rows}) == 900
assert len({normalize_text(row["text"]).casefold() for row in rows}) == 900

for region in REGIONS:
    for difficulty in DIFFICULTIES:
        group = [r for r in rows if r["region"] == region and r["difficulty"] == difficulty]
        assert len(group) == 100
        assert sum(r["language_style"] == "standard" for r in group) == 70
        assert sum(r["language_style"] == "regional" for r in group) == 30

assert all("[CONTEXT]" not in row["text"] for row in rows)
assert all("{" not in row["text"] and "}" not in row["text"] for row in rows)
```

- [ ] **Step 2: Run generation tests and verify RED**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_generate_sentences.SentenceGenerationTest -v
```

Expected: failure because `generate_sentences.py` is missing.

- [ ] **Step 3: Implement deterministic candidate expansion**

Implement these boundaries:

```python
TARGET_PER_GROUP = 100
STANDARD_PER_GROUP = 70
REGIONAL_PER_GROUP = 30
```

For every region/difficulty/style:

1. Expand only templates valid for the requested region and style.
2. Fill slots from the matching capability catalog.
3. Normalize text and discard duplicates globally.
4. Sort candidates by SHA-256 of `region:difficulty:style:text`.
5. Take exactly 70 standard or 30 regional candidates.
6. Assign IDs only after the final 100-item group is stable.

Fail with the exact group name and available candidate count when a group cannot meet its target. Do not duplicate or silently relabel a sentence to fill a shortage.

- [ ] **Step 4: Generate the local CSV**

Run:

```bash
PYTHONPATH=scripts python3 scripts/generate_sentences.py \
  --catalog data/catalog.json \
  --templates data/templates.json \
  --output data/source.csv \
  --dataset-id lumi-voice-vi
```

Expected: `Generated 900 sentences across 9 groups`.

- [ ] **Step 5: Run all generation tests and verify GREEN**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_generate_sentences -v
```

Expected: all tests pass.

## Task 5: Build Versioned JSON Artifacts

**Files:**
- Create: `scripts/build_dataset.py`
- Create: `tests/test_build_dataset.py`
- Generate: `latest.json`
- Generate: `versions/2026.07.1/manifest.json`
- Generate: `versions/2026.07.1/sentences/**/*.json`

- [ ] **Step 1: Write failing packaging tests**

Use a temporary root and assert:

- nine shards are generated when `--shard-size 5000` is used;
- each shard contains exactly 100 records from one region and difficulty;
- sentence records contain `id`, `text`, `random_key`, `language_style`, and `enabled`;
- manifest counts total 900 and group counts total 100;
- manifest file checksums and byte sizes match actual files;
- `latest.json` points to `versions/2026.07.1/manifest.json`;
- rebuilding with the same fixed timestamp produces byte-identical output.
- building an existing version fails without `--force` and leaves both the
  existing version and `latest.json` unchanged;
- building an existing version with `--force` replaces it only after the new
  temporary version passes validation.

- [ ] **Step 2: Run packaging tests and verify RED**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_build_dataset -v
```

Expected: failure because `build_dataset.py` is missing.

- [ ] **Step 3: Implement atomic packaging**

Parse CSV with `csv.DictReader`, validate every row, and group by `(region, difficulty)`. Write to a temporary directory beside `versions/2026.07.1`, compute SHA-256 and byte size from final shard bytes, then atomically rename the directory only after all artifacts are valid.

Manifest selection policy is exactly:

```json
{
  "per_region": {
    "easy": 10,
    "normal": 10,
    "hard": 10
  }
}
```

Do not update `latest.json` until the complete version directory succeeds.

- [ ] **Step 4: Run packaging tests and verify GREEN**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_build_dataset -v
```

Expected: all tests pass.

## Task 6: Implement Independent Artifact Validation

**Files:**
- Create: `scripts/validate_dataset.py`
- Create: `tests/test_validate_dataset.py`

- [ ] **Step 1: Write failing corruption tests**

Tests build a valid temporary dataset, then independently mutate one condition per test:

- change a shard byte to trigger SHA-256 failure;
- delete one shard to trigger missing-file failure;
- duplicate one sentence ID;
- replace one sentence text with `{ROOM}`;
- change a group count from 100 to 99;
- change a style split from 70/30;
- point `latest.json` to a missing version.

Each test asserts a non-zero validation result and an error naming the affected file or group.

- [ ] **Step 2: Run validator tests and verify RED**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_validate_dataset -v
```

Expected: failure because `validate_dataset.py` is missing.

- [ ] **Step 3: Implement independent validation**

The validator must not call the builder's private validation functions. It may reuse only schema constants, normalization, hash, and canonical JSON helpers from `dataset_common.py`. It prints every discovered error, returns exit code 1 on any error, and prints this exact summary on success:

```text
VALID: 900 sentences, 3 regions, 3 difficulties, 9 shards
```

- [ ] **Step 4: Run validator tests and verify GREEN**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest tests.test_validate_dataset -v
```

Expected: all tests pass.

## Task 7: Document the Local Workflow and Public Contract

**Files:**
- Create local-only: `README.md`
- Create local-only: `docs/dataset-format.md`

- [ ] **Step 1: Write the operator README**

Document these exact operations:

1. Generate `data/source.csv`.
2. Run all unit tests.
3. Build version `2026.07.1` with a fixed ISO-8601 timestamp.
4. Validate the generated version.
5. Inspect the Git allowlist.
6. Commit and push only generated JSON.

Include a warning that `git add .` is prohibited in this repository because local-only tooling must not be published.

- [ ] **Step 2: Write the Android JSON contract**

Document field types, enum values, selection behavior, wraparound pivot query, checksum verification, dataset version pinning, and the rule that a region must never fall back to another region.

- [ ] **Step 3: Verify documentation commands**

Run every local command copied into the README. Expected: each exits 0 and produces the documented summary.

## Task 8: Generate and Audit Version 2026.07.1

**Files:**
- Generate: `data/source.csv`
- Generate: `latest.json`
- Generate: `versions/2026.07.1/manifest.json`
- Generate: nine JSON shards

- [ ] **Step 1: Run the complete unit test suite**

Run:

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Generate the source corpus**

Run the Task 4 generator command. Expected: exactly 900 rows plus one CSV header.

- [ ] **Step 3: Build the published version**

Run:

```bash
PYTHONPATH=scripts python3 scripts/build_dataset.py \
  --input data/source.csv \
  --output . \
  --dataset-id lumi-voice-vi \
  --version 2026.07.1 \
  --shard-size 5000 \
  --published-at 2026-07-22T10:00:00Z
```

Expected: nine shards and 900 total sentences.

- [ ] **Step 4: Validate published artifacts**

Run:

```bash
PYTHONPATH=scripts python3 scripts/validate_dataset.py \
  --root . \
  --version 2026.07.1
```

Expected: `VALID: 900 sentences, 3 regions, 3 difficulties, 9 shards`.

- [ ] **Step 5: Perform a language-quality audit**

For each of the nine groups, inspect at least 10 deterministic samples and verify:

- text is natural Vietnamese and readable on a 1024x600 screen;
- regional wording is restrained and understandable;
- L1/L2/L5 semantic classification is correct;
- capability combinations are valid;
- no sentence contains an unresolved annotation or placeholder.

Record any rejected sentence ID, replace the source template or catalog entry, then regenerate the entire deterministic version and rerun all tests.

## Task 9: Publish Data-Only Artifacts

**Files:**
- Stage: `latest.json`
- Stage: `versions/2026.07.1/**/*.json`

- [ ] **Step 1: Verify local-only files are untracked**

Run:

```bash
git status --short --ignored
git check-ignore data/source.csv scripts/build_dataset.py tests/test_build_dataset.py README.md
```

Expected: every local-only file is reported as ignored.

- [ ] **Step 2: Stage only published artifacts**

Run:

```bash
git add latest.json versions/2026.07.1
```

- [ ] **Step 3: Enforce the staged allowlist**

Run:

```bash
unexpected="$(git diff --cached --name-only | grep -Ev '^(latest\.json|versions/.+\.json)$' || true)"
test -z "$unexpected"
```

Expected: exit code 0 and no output.

- [ ] **Step 4: Revalidate staged file content**

Run the complete unit tests and artifact validator again. Expected: all tests pass and the validator prints the 900-sentence success summary.

- [ ] **Step 5: Commit the dataset**

Run:

```bash
git commit -m "✨ feat(data): publish Vietnamese voice dataset"
```

Expected: only `latest.json` and versioned JSON artifacts are committed.

- [ ] **Step 6: Push and verify raw GitHub artifacts**

Run:

```bash
git push origin main
```

Then fetch `latest.json`, the version manifest, and one shard from the raw GitHub URLs. Verify their SHA-256 relationship and rerun the validator against a fresh download directory.
