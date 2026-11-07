# Dataset inventory and preparation

This offline WP1 tool reads restored recorder roots or their locally mounted
Drive copies. It does not modify raw files, manage Drive synchronization, or
automatically download the entire archive. Inventory lists names/logical sizes;
preparation reads payloads and may trigger file-provider downloads if its input
is a Drive mount. Prefer a fully restored local copy for checksum/replay work.

From the repository root with the existing development environment:

```sh
.venv/bin/python -m counterback_replay.dataset inventory \
  --input recorder-a=/absolute/restore/recorder-a \
  --input recorder-b=/absolute/restore/recorder-b \
  --evaluation-start 2026-10-15 \
  --output build/wp1/inventory.json

.venv/bin/python -m counterback_replay.dataset prepare \
  --inventory build/wp1/inventory.json --date 2026-09-14 \
  --cache build/wp1/cache --output build/wp1/catalogue.json
```

Outputs must be outside raw roots. Inventory/catalogue files are immutable:
choose a new `--output` path for another audit. The cache is intentionally reused.
On a verified cache hit the tool rechecks source/artifact hashes and returns the
same dataset ID without rerunning reconstruction/export. It never trusts only
an old report's status or file existence. Modifying an existing cached artifact
fails validation instead of overwriting it.

This qualification covers the snapshot/depth reconstruction and existing ticker
checks. Manifest-listed public-trade files are integrity-hashed, but this tool
does not decode/qualify a strategy-facing trade feed or promise volume coverage.
That capability needs its own adapter and semantic validation before use.

To prepare multiple days, repeat `--date`. To process every candidate, explicitly
use `--all` instead. Full archives can take substantial time and disk space:
integer exports are uncompressed. `--minimum-recorders` defaults to two; selecting
one during inventory is an explicit weaker evidence configuration. Each day uses
available finalized inputs in the declared recorder order, and must meet that
minimum. Missing/incomplete recorder copies are not silently called two-source
verification. Inventory `--start-date` / `--end-date` may constrain completed UTC
days; missing dates inside the interval are recorded, not dropped.

Catalogue states:

| State | Meaning |
| --- | --- |
| `discovered_unverified` | A date exists, but payloads/checksums/coverage have not been qualified. |
| `unavailable` | No copy exists or selected preparation lacks enough finalized recorder inputs. |
| `excluded` | A fresh verifier report failed the strict passing-day gate; diagnostics retain the report/quality issues. |
| `failed_preparation` | Input/cache integrity, metadata, conversion or I/O checks failed. No usable bundle is returned. |
| `prepared` | Fresh verification and reference export passed, or the matching immutable bundle was revalidated/reused. Still requires WP2's causal availability gate before strategy use. |

`prepare` writes a catalogue even when some days fail, then exits 2 for selected
excluded/failed/unavailable days. Global configuration errors exit 1. Successful
selected days exit 0. A catalogue preserves its original full inventory and
partition, including unselected/unverified dates. Another invocation produces a
new catalogue audit rather than mutating the previous one.

Each completed bundle is under `cache/bundles/<dataset_id>/`:

```text
bundle.json                 Identity, source/grid/rule fingerprints and artifact hashes
verification/               Fresh immutable report and coverage ledger
replay/events.l2             Existing integer replay interface
replay/expected.ndjson       Python HorizonBook reference checkpoints
replay/evidence.json         Existing export completion/provenance marker
```

`bundle.json` also records reconstruction summary and invalid update-ID intervals.
Failed/review attempts remain under `cache/staging/` with failure/exclusion
diagnostics; they are not cache hits. Raw roots and prepared data remain out of
Git; use ignored `build/` or another private data workspace.

To independently check a prepared bundle with the existing C++ replay:

```sh
./build/engine/counterback_replay /absolute/bundle/replay/events.l2 10000 \
  > build/wp1/actual.ndjson
.venv/bin/python -m counterback_replay compare \
  --expected /absolute/bundle/replay/expected.ndjson --actual build/wp1/actual.ndjson
```

Use the bundle's checkpoint interval in the C++ invocation. This is reconstruction
comparison, not a completed trading backtest. See the
[v1 contract](v1-execution-contract.md) for the frozen time/execution/accounting
rules and [WP1 validation](wp1-validation.md) for measured local results.
