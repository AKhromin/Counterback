# WP2 validation — 7 November 2026

WP2's acceptance gate is implemented: a deterministic queue/clock/coordinator,
causal streaming replay, immutable read-only market views, a compatible synthetic
adapter, stable event IDs and initial journal records. See the
[runtime guide](wp2-runtime.md) for the API, example and implementation boundaries.
Historical fills, account/fee calculations and P&L are not part of this gate.

## Regression and acceptance tests

- **111 offline Python tests passed**, with two live Binance tests deselected.
  Combined Python recorder/replay coverage including branches was **84.37%**.
- Debug and Release CTest: **4/4 passed** in each configuration, including the
  matching engine, historical replay, new deterministic-runtime tests and the
  Python/reference target containing **50 pytest cases**.
- AddressSanitizer + UndefinedBehaviorSanitizer CTest: **3/3 passed**. This
  configuration ran the C++ targets; the Python target was disabled.
- Ruff and strict mypy passed for the existing Python packages; `git diff --check`
  passed. Runtime C++ compiles under the project's warnings-as-errors settings.

The C++ fixtures exercise timestamp priorities, zero-delay causal rounds,
same-time order/cancel races, delayed arrivals and notification timers, timestamp
overflow, clock/past-scheduling rejection, stable IDs and byte-identical journals.
They check initial bootstrap, warmup/start boundaries, late replacement snapshots,
known empty versus unavailable depth, retained-view immutability, compatible
bridged gaps, unbridged gaps, horizon loss, crossed books, exclusive end times and
single-use/error behavior. Synthetic arrival-time fills use the existing matcher;
historical order-level execution is explicitly rejected.

Ten new Python test cases cover independent reconstruction and time contracts.
A seeded 1,100-record fixture is compared to `ReferenceReplay`/`HorizonBook` at
**every non-ignored visible state**, including full observed depth. Python's WP1
availability/scheduling helpers independently check the emitted visibility times
and causal keys. Separate fixtures cover late/source-list snapshots, receive-time
regressions, ties, duplicates/stale observations, trust-loss halts, end exclusion
and malformed/truncated input. Repeated full fixture outputs are byte-identical.

The first complete Python run hit the environment's restriction on binding a
localhost test port; the permitted rerun passed the existing WebSocket test.
An initial Release reference invocation selected a system Python when given a
relative interpreter path; configuring the absolute project-venv interpreter
resolved collection, and the complete Release CTest suite passed. No production
behavior was changed for either environment issue.

## Prepared development-day runtime runs

All four prepared-bundle artifact hashes were checked before each day's audit.
The original recordings and WP1 bundles were read without modification. The
Release runtime ran with the UTC day's `[start, end)` interval, zero trusted-update
warmup, an observation-only C++ callback and journal sampling interval 10,000.
No fees, portfolio or strategy performance were calculated.

| UTC day | Runtime outcome | Applied updates | Reference comparison | Repeatability |
| --- | --- | ---: | --- | --- |
| 2026-09-14 | Completed the day; became ready and retained trust through end | 862,298 | 138 sampled full-depth checkpoints plus the complete final reconstruction summary equal the stored Python oracle | Sampled output byte-identical across repeated runs |
| 2026-09-10 | Halted at **12:47:05.850352727 UTC** on bid-horizon trust loss | 459,048, including the final trust-losing observation | 91 sampled full-depth checkpoints before halt equal the stored Python oracle | Sampled output byte-identical across repeated runs |

The 10 September halt is the required baseline behavior. At source record
918,048, update range `99940147247–99940148021` leaves the bid top outside the
known horizon. The run emits `horizon_lost`, suppresses further callbacks and does
not resume at later snapshots. The WP1 reconstruction report's passing aggregate
threshold does not authorize uninterrupted trading through that interval. Its
partial terminal summary is not compared to a full-day final state.

The 14 September run dispatched 1,725,909 runtime events, including 862,298
observation callbacks and 1,311 bootstrap observations. Ignored duplicates remain
in reconstruction counters and do not produce callbacks. The 10 September run
dispatched 918,116 events and 459,047 callbacks before halting. An undelivered end
event remains in its termination diagnostics.

Real-day equality is checked at the sampled checkpoints and, for the completed
day, the final state. Full-depth equality after every real event is not claimed;
the seeded synthetic reference fixture covers that stronger per-state check.
Byte equality here concerns the sampled CLI output. The full fixture journals
are checked without sampling.

## Reproduction and evidence

From the project root, with the documented development environment installed:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug \
  -DCOUNTERBACK_PYTHON_TESTS=ON -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build --parallel
ctest --test-dir build --output-on-failure
.venv/bin/pytest -m 'not live' --cov=depth_recorder --cov=counterback_replay \
  --cov-report=term-missing --cov-fail-under=80
.venv/bin/ruff check .
.venv/bin/mypy src/depth_recorder src/counterback_replay
./build/engine/counterback_runtime engine/examples/runtime-fixture.l2 0 300
```

Use `build/wp2-release` and `-DCMAKE_BUILD_TYPE=Release` for the Release variant.
For sanitizers, use the flags in [the C++ guide](cpp-engine.md#validation), with
`COUNTERBACK_PYTHON_TESTS=OFF` for the three-target C++ check.

The [machine-readable record](wp2-validation.json) contains dataset/input/output
identities, counts and checksums. Local real-day outputs and audit scripts remain
under ignored `build/wp2/`; large datasets and journals are not added to Git.
WP1 bundle paths/qualification are historical preparation evidence, not modified
to pretend they originally certified the runtime availability gate.

Current recorder-host operations, archive-wide payload qualification, remote CI,
Linux execution, historical trading/accounting, Python bindings and multi-day
trading continuity were not established by this validation.
