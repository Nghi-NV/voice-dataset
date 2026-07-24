# Dataset JSON Contract

## Published tree

```text
latest.json
versions/<version>/manifest.json
versions/<version>/sentences/north/easy-0001.json
versions/<version>/sentences/north/normal-0001.json
versions/<version>/sentences/north/hard-0001.json
versions/<version>/sentences/central/*.json
versions/<version>/sentences/south/*.json
```

The first release uses one shard for each region/difficulty pair. Consumers must
still read the manifest file list because later, larger versions may contain
multiple shards.

## Enumerations

| Field | Values |
|---|---|
| `region` | `north`, `central`, `south` |
| `difficulty` | `easy`, `normal`, `hard` |
| `language_style` | `standard`, `regional` |
| `language` | `vi-VN` |

Difficulty expresses semantic difficulty:

- `easy`: L1 basic control or a natural equivalent.
- `normal`: L2 control with a value/parameter or a status query.
- `hard`: L5 ambiguous command or nonexistent room/device reference.

## Latest pointer

```json
{
  "dataset_id": "lumi-voice-vi",
  "latest_version": "2026.07.1",
  "manifest_path": "versions/2026.07.1/manifest.json",
  "published_at": "2026-07-22T10:00:00Z",
  "schema_version": 1
}
```

The app may use this mutable file to discover a version. Once it creates a user,
it pins that user's `dataset_version` and assigned sentence IDs.

## Manifest

Important fields:

```json
{
  "schema_version": 1,
  "dataset_id": "lumi-voice-vi",
  "dataset_version": "2026.07.1",
  "language": "vi-VN",
  "published_at": "2026-07-22T10:00:00Z",
  "collection_policy": {
    "counts_per_difficulty": {
      "easy": 10,
      "normal": 10,
      "hard": 10
    },
    "regional_style_per_difficulty": {
      "easy": 3,
      "normal": 3,
      "hard": 3
    },
    "turns": [
      {"id": "ONE_METER", "distance_meters": 1},
      {"id": "THREE_METERS", "distance_meters": 3, "unlock_after": "ONE_METER"}
    ]
  },
  "counts": {
    "north": {"easy": 100, "normal": 100, "hard": 100, "total": 300},
    "central": {"easy": 100, "normal": 100, "hard": 100, "total": 300},
    "south": {"easy": 100, "normal": 100, "hard": 100, "total": 300},
    "total": 900
  },
  "files": []
}
```

Each `files` entry contains:

| Field | Type | Meaning |
|---|---|---|
| `path` | string | Path relative to the version directory |
| `region` | enum | Region represented by the shard |
| `difficulty` | enum | Difficulty represented by the shard |
| `shard` | integer | One-based shard number |
| `count` | integer | Number of sentence records |
| `enabled_count` | integer | Enabled records available for selection |
| `language_style_counts` | object | `standard` and `regional` counts |
| `size_bytes` | integer | Exact UTF-8 file size |
| `sha256` | string | Lowercase SHA-256 hex digest |

Download each shard to a temporary file, verify `size_bytes` and `sha256`, then
import it. Never import a shard that fails either check.

## Sentence shard

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
      "id": "VI_NORTH_E_0001",
      "text": "Bật đèn trần ở phòng khách.",
      "random_key": 18374629,
      "language_style": "standard",
      "enabled": true
    }
  ]
}
```

`random_key` is a signed SQLite-safe positive integer derived from
`SHA-256(dataset_id + ':' + sentence_id)`. Collisions are resolved by sorting
on `(random_key, id)`.

## Android selection

After importing a shard, calculate `word_count` and `display_eligible` for the
1024×600 recording UI. Create this index:

```sql
CREATE INDEX sentence_selection_idx
ON sentences(
  dataset_version,
  region,
  difficulty,
  enabled,
  display_eligible,
  random_key,
  sentence_id
);
```

For a new user:

1. Read the user's selected region.
2. Select 10 enabled, display-eligible `easy` sentences around a seeded random
   pivot, wrapping to the start of the index when required.
3. Repeat independently for `normal` and `hard`.
4. Deterministically shuffle the resulting 30 IDs with the user selection seed.
5. Persist all 30 IDs, their order, and the dataset version.
6. Reuse exactly the same order for the 1 m and 3 m turns.

If any difficulty in the chosen region has fewer than 10 eligible sentences,
block user creation. Do not select from another region.
