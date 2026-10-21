# Historical Binance L2 replay

Milestone 2 is an offline, deterministic market-state reconstruction component.
`counterback::historical` owns aggregated price levels independently of
`counterback::engine` / `MatchingEngine`. Binance depth quantities **replace**
the quantity at a price; they never submit individual orders or generate fills.
No strategies, execution assumptions, portfolio accounting or Python bindings
are introduced.

## Build and test

From the repository root, with the existing Python development environment:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug \
  -DCOUNTERBACK_PYTHON_TESTS=ON -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build --parallel
ctest --test-dir build --output-on-failure
.venv/bin/pytest -m 'not live'
.venv/bin/ruff check .
.venv/bin/mypy src/depth_recorder src/counterback_replay
```

Without `COUNTERBACK_PYTHON_TESTS`, CMake needs only a C++17 compiler and builds
the matching and historical C++ tests. Enabling the option adds a CTest that runs
the Python/C++ differential suite. CI enables it on Linux/macOS in Debug/Release
and builds the binary before the complete Python regression suite. Python replay
tests explicitly skip if no executable exists; set `COUNTERBACK_REPLAY_BINARY`
when using another build directory. CI always provides an executable.

## Restore, verify, export, replay, compare

Use a completed UTC day with final manifests, compressed depth streams,
snapshots, bookTicker streams and captured `exchange-info-*.json.zst` metadata.
The recorder roots must contain `<symbol>/<UTC-date>/`. Work on restored,
immutable files; the following commands only read those roots. Raw recordings
and generated integer replay files belong outside Git or in ignored `build/`.

```sh
.venv/bin/depth-recorder verify --date 2026-09-14 \
  --input recorder-a=/absolute/restore/recorder-a \
  --input recorder-b=/absolute/restore/recorder-b \
  --output build/day-2026-09-14-verification

.venv/bin/python -m counterback_replay export --date 2026-09-14 \
  --input recorder-a=/absolute/restore/recorder-a \
  --input recorder-b=/absolute/restore/recorder-b \
  --verification-report build/day-2026-09-14-verification/verification-2026-09-14-btcusdt.json \
  --output build/day-2026-09-14-replay --checkpoint-every 10000

./build/engine/counterback_replay build/day-2026-09-14-replay/events.l2 10000 \
  > build/day-2026-09-14-replay/actual.ndjson

.venv/bin/python -m counterback_replay compare \
  --expected build/day-2026-09-14-replay/expected.ndjson \
  --actual build/day-2026-09-14-replay/actual.ndjson
```

Use a **new verification directory** on each run: existing reports are immutable.
The export directory must also be new. Only use an export once `evidence.json`
exists and the exporter exits successfully. That completion marker records the
report, manifests, metadata, event file and oracle SHA-256 hashes. An interrupted
or rejected export can leave incomplete diagnostic files without a completion
marker. The C++ reader requires a count-checked `END` record and rejects truncated
or trailing data. It does not itself authenticate `evidence.json`: the offline
exporter is the validated trust boundary. Keep completed bundles immutable.

The adapter requires `status: pass`, matching symbol/date/source identities in
the report, fresh manifest checksum checks, and finalized checksum/size evidence
for every consumed depth, snapshot and metadata file. Its reconstruction summary
and trusted coverage must also agree with the supplied report. It does not turn
a `review` report into a verified replay. Use the current verifier first, as above.

The first implementation targets BTCUSDT only. For a new day, substitute its
date and restored roots. The Python adapter and C++ reader are streaming over
depth events; snapshots stay in memory. The integer export is uncompressed and
can be substantially larger than the captures. Checkpoints contain **all
observed levels**, so decreasing the interval increases output size markedly.

## Integer event interface (version 1)

`engine/include/counterback/historical_book.hpp` defines `DepthSnapshot`,
`DepthUpdate`, `PriceLevel` and `Observation`. Prices/quantities use signed int64
ticks/lots; IDs and timestamps use uint64. Provenance preserves recorder ID,
run ID, UTC/monotonic receive nanoseconds and exchange event milliseconds (`E`,
zero if absent). REST snapshots have no exchange event time or monotonic time in
the Python `Snapshot` model. These timestamps are observations, not replay clocks.
Monotonic times must never be compared across recorder/run pairs.

`events.l2` is a whitespace-delimited, line-oriented protocol, not raw Binance
JSON. Identity tokens are restricted to safe ASCII characters. Every level is a
pair of integer `price_ticks quantity_lots`; there are `nb` bid pairs followed by
`na` ask pairs. Empty sides use count zero.

```text
COUNTERBACK_L2 1 BTCUSDT tick_size lot_size
S last_update_id rx_utc_ns recorder_id run_id nb na [bid pairs] [ask pairs]
... more snapshots ...
BEGIN
D U u rx_utc_ns rx_mono_ns exchange_time_ms recorder_id run_id nb na [bid pairs] [ask pairs]
... more depth events ...
END number_of_depth_records
```

All captured `PRICE_FILTER.tickSize` and `LOT_SIZE.stepSize` values must be
positive and agree across the selected recorder metadata. Conversion uses exact
integer ratios from decimal text, with divisibility and int64 bounds checks;
there is no binary float or Decimal-context rounding. Non-power-of-ten steps
are supported. Missing/changing metadata, negative/non-finite values, zero
prices, unrepresentable values and overflow fail closed. Zero **quantities**
remain valid removals. Order min/max size filters are not applied to aggregate
levels, which can exceed one order's limits. Captured metadata is assumed to
describe the entire day, including the period before its first capture; changed
grids require explicitly splitting the dataset and are not automatically inferred.

## Sequencing, validity and unknown depth

- Reuse the recorder readers for compressed records. Each recorder must already
  be sorted by `(U, u, rx_utc)`; the adapter checks this before the verifier's
  heap merge. Input order breaks exact ties deterministically. Neither wall time
  nor exchange time reorders market updates.
- Adjacent batches with identical `(symbol, U, u)` and identical raw bid/ask
  tuples are duplicates. A conflicting duplicate rejects the export. Decimal
  spelling/order conflicts are checked before conversion, as in the verifier.
  The C++ controller additionally checks conflicts in its integer input.
- A snapshot is eligible only when `snapshot_id < u` and
  `U <= snapshot_id + 1 <= u`. Earlier events remain unanchored; snapshots do not
  fill the unobserved prefix of a day. Periodic bridge snapshots replace state
  even when the current book remains usable.
- Stale batches (`u <= last_applied_id`) are ignored. Overlapping batches covering
  the next ID are applied in full using absolute replacement. Quantity zero
  removes a level; removal of an absent level is a counted, idempotent no-op.
- `U > previous_stream_u + 1` records the actual missing ID interval. A book
  without a valid bridge is discarded on a sequence gap and stays unanchored
  until a later snapshot bridges an event. A snapshot can recover state, but
  cannot recreate depth inside the missing interval.
- The original snapshot's lowest bid and highest ask bound its known horizon.
  Later outer levels are observed but the space between them remains unknown.
  Exhausting all levels inside one side's original horizon suppresses that
  side's best price. Adding a level inside it can restore trust. Trust does not
  imply that the entire market depth is known.
- If both sides are trusted, a best bid greater than or equal to the best ask is
  a structural error. The replay discards that book until a later bridge. The
  C++ CLI still emits diagnostic checkpoints but exits 2 for structural errors;
  malformed adapter input exits 1. Gaps/horizon exhaustion are reported states,
  so callers must inspect the summary even when exit status is zero.

Checkpoints and the final JSON summary expose update ID, per-side trust,
boundaries, best prices and quantities, spread in ticks, and full observed depth
in best-price-first order. The summary adds application/duplicate/stale counts,
snapshot segments, unseen removals, missing ID intervals, structural errors,
trusted intervals and invalid intervals. Counts include all original recorder
records before duplicate removal; `segments` counts loaded bridge snapshots,
not every available snapshot. Trusted/invalid intervals describe observed batch
ranges after application; overlapping batches can make these ranges overlap.
Neither metric asserts trust at every individual update inside a batch. Missing
ID ranges are included in invalid intervals. Snapshot recovery never erases them.

### Reference compatibility limitation

The existing verifier loads snapshots sorted per recorder, then concatenates
those lists in `--input` order. `verify.reconstruct` consumes that list with a
forward cursor, without globally sorting it. Replay deliberately preserves this
source-list order to produce the **same** snapshot selections and coverage;
independently sorting in C++ would silently change the reference result. This
can delay use of snapshots from later sources. Keep recorder input order equal
to the verification report. Correcting global snapshot selection is a separate
verifier change that needs renewed verification of historical reports. This
branch does not change recorder/verifier behavior.

## Validation boundary

Synthetic fixtures compare C++ against the unchanged Python `HorizonBook` at
every event, including all observed depth. A fixed-seed 2,200-record stream
tests determinism, stale/overlapping batches, duplicates, horizon loss/recovery,
gaps, periodic snapshots, crossed/locked books and uint64 endpoints. Aggregate
reference results are independently checked against `verify.reconstruct`.
An integration fixture uses actual compressed recorder files and manifests;
negative tests exercise metadata, checksum, report, grid and parser failures.

For the full-day run, state/depth equality is sampled at the chosen checkpoint
interval and at the final state; summary trust intervals, gaps and removal
counts cover every event. This does **not** assert full-depth equality after
every real event between checkpoints. Use interval 1 if exhaustive output is
required. Ticker validation remains in the unchanged Python verifier; the C++
adapter consumes depth/snapshots, not ticker messages. See
[the recorded validation results](historical-replay-validation.md) for the real
day, measured results and local test coverage.
