# Lumi Voice Dataset Tooling

Local tooling for generating and validating the Vietnamese smart-home recording
dataset consumed by the Lumi speaker app.

The GitHub repository is a data publication target. Only `latest.json` and
`versions/**/*.json` may be committed. Source CSV, scripts, tests, and this
documentation stay local.

## Requirements

- Python 3.9 or newer
- Git
- No third-party Python packages

## Generate the source corpus

```bash
PYTHONPATH=scripts python3 scripts/generate_sentences.py \
  --catalog data/catalog.json \
  --templates data/templates.json \
  --output data/source.csv \
  --dataset-id lumi-voice-vi
```

Expected output:

```text
Generated 900 sentences across 9 groups
```

## Run tests

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -v
```

## Build a version

```bash
PYTHONPATH=scripts python3 scripts/build_dataset.py \
  --input data/source.csv \
  --output . \
  --dataset-id lumi-voice-vi \
  --version 2026.07.1 \
  --shard-size 5000 \
  --published-at 2026-07-22T10:00:00Z
```

Add `--force` only when intentionally rebuilding the same unpublished version.
Without it, the builder preserves the existing version and fails.

## Validate generated JSON

```bash
PYTHONPATH=scripts python3 scripts/validate_dataset.py \
  --root . \
  --version 2026.07.1
```

Expected output:

```text
VALID: 900 sentences, 3 regions, 3 difficulties, 9 shards
```

## Publish

Never run `git add .` in this repository. Stage generated data explicitly:

```bash
git add latest.json versions/2026.07.1
unexpected="$(git diff --cached --name-only | grep -Ev '^(latest\.json|versions/.+\.json)$' || true)"
test -z "$unexpected"
git commit -m "✨ feat(data): publish Vietnamese voice dataset"
git push origin main
```

Before committing, rerun the tests and validator. See
[`docs/dataset-format.md`](docs/dataset-format.md) for the Android contract.

## Dataset rules

- 900 globally unique sentences.
- 100 sentences per region and difficulty.
- 70 standard and 30 regional expressions per group.
- `L1 → easy`, `L2 → normal`, `L5 → hard`.
- No `[CONTEXT]` annotations or unresolved placeholders in published text.
- A user's selected region never falls back to another region.

## License

Internal Lumi data and tooling. No public reuse license is granted.
