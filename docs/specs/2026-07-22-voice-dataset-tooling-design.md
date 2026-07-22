# Voice Dataset Tooling Design

## 1. Objective

Create a small, reproducible toolkit that converts one source CSV into a
versioned Vietnamese voice dataset suitable for the Lumi speaker collection
flow. The generated dataset is split by region and difficulty, can be fetched
from GitHub without loading the full corpus into memory, and contains enough
integrity metadata for the Android app to validate every download.

The repository will contain tooling and sample input only. Real production
sentences can be added later through the same CSV contract without changing the
output schema.

## 2. Source Contract

The generator accepts UTF-8 CSV with this header:

```csv
id,text,region,difficulty,enabled
VI_NORTH_E_000001,Bật đèn phòng khách.,north,easy,true
```

Rules:

- `id` is globally unique, stable, and contains only `A-Z`, `0-9`, and `_`.
- `text` is non-empty Vietnamese text after surrounding whitespace is removed.
- `region` is one of `north`, `central`, or `south`.
- `difficulty` is one of `easy`, `normal`, or `hard`.
- `enabled` is `true` or `false` and defaults to `true` only when the column is
  present but empty.
- Duplicate IDs are rejected even if the remaining content is identical.
- Exact duplicate sentences inside the same region and difficulty are rejected.
- The generator never silently moves an invalid record to another group.

## 3. Repository Layout

```text
voice-dataset/
├── README.md
├── data/
│   └── source.sample.csv
├── docs/
│   ├── dataset-format.md
│   └── specs/
│       └── 2026-07-22-voice-dataset-tooling-design.md
├── scripts/
│   ├── build_dataset.py
│   └── validate_dataset.py
├── tests/
│   ├── test_build_dataset.py
│   └── test_validate_dataset.py
└── versions/
    └── <dataset-version>/
        ├── manifest.json
        └── sentences/
            ├── north/
            ├── central/
            └── south/
```

The mutable `latest.json` pointer is generated at repository root only after a
version passes validation.

## 4. Generated Dataset Format

### 4.1 Latest pointer

```json
{
  "schema_version": 1,
  "dataset_id": "lumi-voice-vi",
  "latest_version": "2026.07.1",
  "manifest_path": "versions/2026.07.1/manifest.json",
  "published_at": "2026-07-22T10:00:00Z"
}
```

### 4.2 Manifest

The manifest contains:

- schema, dataset, language, version, and publication time;
- a fixed selection policy of 10 `easy`, 10 `normal`, and 10 `hard` sentences
  for the selected region;
- counts grouped by region and difficulty;
- one entry per shard containing its relative path, region, difficulty, shard
  number, enabled sentence count, total sentence count, byte size, and SHA-256.

File entries are sorted by region, difficulty, and shard number so two builds
from the same input produce stable structural output.

### 4.3 Sentence shard

```json
{
  "schema_version": 1,
  "dataset_id": "lumi-voice-vi",
  "dataset_version": "2026.07.1",
  "region": "north",
  "difficulty": "easy",
  "shard": 1,
  "sentences": [
    {
      "id": "VI_NORTH_E_000001",
      "text": "Bật đèn phòng khách.",
      "random_key": 18374629,
      "enabled": true
    }
  ]
}
```

`region` and `difficulty` are stored at shard level to avoid repeating them for
every sentence. Each shard is self-describing and can be validated without
loading another shard.

## 5. Deterministic Random Keys

The generator derives `random_key` from a stable hash of:

```text
dataset_id + sentence_id
```

It maps the digest into the inclusive range `0..2147483647`. The key is stable
across repeated builds and safe to store as a SQLite integer. Collisions are
allowed and resolved by ordering on `(random_key, sentence_id)`.

The Android app selects sentences with an indexed pivot query scoped to:

```text
dataset_version + region + difficulty + enabled + display_eligible
```

It takes 10 records at or after the pivot, wrapping to the start when required.
There is no full-table `ORDER BY RANDOM()` operation.

## 6. Generator CLI

Planned command:

```bash
python3 scripts/build_dataset.py \
  --input data/source.csv \
  --output . \
  --dataset-id lumi-voice-vi \
  --version 2026.07.1 \
  --shard-size 5000 \
  --published-at 2026-07-22T10:00:00Z
```

Behavior:

1. Stream and validate the source CSV.
2. Group records by region and difficulty without retaining generated JSON in
   memory.
3. Sort each group by sentence ID for reproducible output.
4. Derive deterministic random keys.
5. Write JSON to a temporary version directory.
6. Generate shard checksums and the manifest.
7. Run full output validation.
8. Atomically replace the target version directory and update `latest.json`.

If the target version already exists, the command fails unless `--force` is
explicitly supplied. Failed builds do not modify the previous version or
`latest.json`.

## 7. Validation CLI

Planned command:

```bash
python3 scripts/validate_dataset.py --root . --version 2026.07.1
```

Validation checks:

- every referenced file exists and no undeclared shard exists;
- every shard matches the manifest version, region, difficulty, and shard ID;
- SHA-256, file size, and counts match the manifest;
- IDs are globally unique and valid;
- sentences are non-empty and normalized to single-line text;
- enum values and booleans are valid;
- random keys match the deterministic generation rule;
- every region has at least 10 enabled records for each difficulty;
- `latest.json`, when present, points to an existing valid manifest.

Validation prints actionable errors with the file and sentence ID, then exits
non-zero. It does not repair malformed source or generated output silently.

## 8. Android Consumption

The Android app downloads `latest.json`, then the selected manifest and shards.
It verifies SHA-256 before importing a shard into indexed local SQLite storage.
During import it computes `word_count` and `display_eligible` for the exact
1024x600 Lumi display.

New users use the latest fully imported dataset. Existing users keep their
stored `dataset_version` and assigned 30 sentence IDs. The selected profile
region is immutable after assignment, and the same 30 sentences in the same
order are used for the 1 m and 3 m turns.

If any region/difficulty has fewer than 10 enabled, display-eligible sentences,
the app blocks creation for that region instead of selecting from another
region.

## 9. GitHub Publication

Generated versions are published through immutable Git tags or releases. The
Android app may check a mutable `latest.json`, but it downloads the versioned
paths declared by that pointer and never binds an active recording session to
the mutable `main` branch.

For a private repository, the APK must not contain a GitHub personal access
token. A backend or an authenticated mirror must provide temporary download
access instead.

## 10. Testing and Success Criteria

Unit tests cover:

- valid CSV generation for all nine region/difficulty combinations;
- deterministic output and random keys;
- CSV quoting for Vietnamese text containing commas and quotation marks;
- shard splitting at exact boundaries;
- duplicate ID and duplicate sentence rejection;
- invalid enums, booleans, IDs, and empty text;
- insufficient enabled sentences per group;
- checksum and count mismatch detection;
- safe failure when a version already exists;
- no update to `latest.json` after a failed build.

The work is complete when:

1. Both CLIs use only the Python standard library and run on Python 3.9+.
2. All unit tests pass with `python3 -m unittest discover -s tests -v`.
3. A sample build generates a valid nine-group dataset.
4. Repeating the same build produces byte-identical shard and manifest content
   when `--published-at` is fixed.
5. README and format documentation allow another engineer to prepare, build,
   validate, and publish a dataset without reading script internals.
