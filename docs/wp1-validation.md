# WP1 validation — 7 November 2026

WP1's initial gate is implemented: a versioned baseline contract and tested
policy primitives, read-only dataset inventory, fresh day verification/export,
immutable preparation identities, corruption-checked cache reuse and explicit
catalogue states. WP2's trading runtime remains unimplemented.

## Archive inventory

The visible mounted Google Drive backup folders were inventoried by names and
logical file sizes, without decoding/checksumming the whole archive:

- 62 candidate UTC dates: **2026-09-06 through 2026-11-06**.
- Recorder A: 62 dated folders; recorder B: 60 dated folders.
- 122 filenames matching final daily manifests were observed. Their presence
  alone is not proof of valid manifest contents, payload checksums or coverage.
- Observed logical size: **42,559,734,321 bytes**, not a measured download size
  or a claim that all payloads are resident locally.
- Fixed partition: **39 development dates before 2026-10-15**, and **23 reserved
  evaluation dates from 2026-10-15**. No strategy performance was inspected.
- The initial 6 September date lacks depth-hour filenames 00–21 jointly. Other
  dates' hourly presence is metadata evidence only, not verified continuity.

The connected Drive folder searches returned no matching backup folders; the
inventory therefore uses the already-accessible local Drive mount. It is not an
independent server-side backup audit. All 62 archive dates remain explicitly
`discovered_unverified` in `build/wp1/drive-inventory.json`.

## Fresh local qualification and preparation

Fully restored files in the old `Backtesting Engine/verification-input/extracted`
roots were read without modification. These are separate input locations from
the metadata-only Drive inventory. Each prepared day received a fresh report in
a new staging directory, exact export/reference agreement, and a completed
content-addressed bundle.

| UTC day | Result | Applied depth updates | Stream sequence gaps | Trusted ID coverage | C++ reference comparison |
| --- | --- | ---: | ---: | ---: | --- |
| 2026-09-10 | Prepared; fresh verifier `pass` | 862,759 | 1 | 99.70847189931933% | 172 full-depth checkpoints plus final state equal |
| 2026-09-14 | Prepared; fresh verifier `pass` | 862,298 | 0 | 99.85761010045807% | 172 full-depth checkpoints plus final state equal |
| 2026-09-12 | Unavailable for the requested two-recorder local preparation | — | — | — | Not run |

The local restored interval also has no copies for 11/13 September; those dates
remain unavailable in that local catalogue. Missing local copies do not mean the
corresponding Drive dates are missing or excluded. The initial selected-day
batch deliberately exited **2** because 12 September lacked the second finalized
local recorder input, while preserving the two successful prepared bundles.

The 10 September report's passing thresholds do not erase its observed gap or
additional untrusted intervals. These are preserved in the bundle and C++/Python
summary comparison. The v1 trading contract waits for initial anchoring and halts
on required trust loss after starting; it cannot treat a passing reconstruction
report as proof of uninterrupted tradability. Neither day was strategy-tested.

Both successful days were then prepared again using the same cache. The second
audit returned **`reused: true` for both identical dataset IDs**, rechecking raw
and output hashes while skipping verification/reconstruction/export. It exited
0 for the two selected dates. Source root paths are provenance; content/grid/
rule/checkpoint identities determine cache reuse.

The existing C++ CLI replayed each newly generated event file independently;
structured comparison checked IDs, all observed levels at the checkpoint
boundaries, best prices, trust flags/boundaries, intervals and summary counts.
Full-depth equality after every real event between checkpoints is not claimed.
This comparison does not implement WP2's snapshot-availability clock.

## Tests and static checks

- **101 offline Python tests passed**, including 15 new WP1 tests; two live
  Binance tests were deselected. Combined recorder/replay coverage including
  branches was **84.37%**, exceeding the 80% gate.
- Debug CTest **3/3 passed**. The Python/differential target now includes the
  existing replay tests plus WP1 contract/preparation tests: 40 pytest cases.
- Ruff and strict mypy passed for both Python packages.
- Policy tests check snapshot availability, receive-time regressions, causal
  rounds for zero-latency child work, cancellation priority, overflow, exact
  quote notionals, fee rounding and unsupported-policy rejection.
- Dataset tests check metadata-only inventory, fixed partitions and missing
  days, immutable outputs, source/rule identities, successful reuse without
  reconstruction, source/cache corruption, review exclusion, insufficient
  recorder inputs, raw-root output protection and CLI behavior.
- The first complete Python run hit the sandbox restriction on binding a
  localhost port. The permitted rerun passed, including that existing WebSocket
  protocol test. No live exchange endpoint was tested.
- CI was updated to include both Python packages in coverage and WP1 tests in
  the engine test target. No remote CI run, Release/sanitizer rerun, current
  recorder-host health check or full-archive payload audit is claimed here.

## Reproducibility and remaining scope

Measured IDs, hashes, intervals, summaries and test counts are in the
[machine-readable validation record](wp1-validation.json). Large/raw artifacts
remain outside Git under ignored `build/wp1/`:

- `drive-inventory.json`: full observed mounted-archive metadata.
- `local-inventory.json`, `local-catalogue.json`, `local-reuse-catalogue.json`:
  local candidate/qualification/reuse audits.
- `cache/bundles/<dataset_id>/`: immutable prepared files and fresh reports.
- `2026-09-10-comparison.json`, `2026-09-14-comparison.json`: independent C++
  comparison summaries; the corresponding `*-cpp.ndjson` files contain checkpoints.

The [preparation guide](dataset-preparation.md) gives reusable commands and the
[contract](v1-execution-contract.md) states baseline assumptions, including the
synthetic fee/latency example. Qualifying every Drive payload, implementing the
causal runtime and running strategies remain future work. WP1's initial gate
provides enough verified input to start WP2 without mislabeling the whole archive.
