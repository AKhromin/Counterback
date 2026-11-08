# COUNTERBACK v1 completion plan

Status review: 7 November 2026. WP1 is committed at `1f6a150`; WP2 runtime,
adapters, tests and documentation are implemented locally. See
[WP2 validation](wp2-validation.md) for acceptance evidence. This is a delivery
plan, not a claim that the remaining proposed classes already exist.

The [README roadmap](../README.md#roadmap) names 30 November 2026 as the v1 target.
That remains a target, not a completion forecast. Progress below is measured
against acceptance gates rather than an invented percentage or fixed-duration
estimate. Module IDs refer to the [system design](system-design.md#3-module-responsibilities-and-implementation-status).

## 1. What a completed v1 must do

A user selects a verified BTCUSDT dataset and a configuration, starts a Python
strategy, and obtains a reproducible historical trading backtest containing
order/fill records, fees, cash/inventory, equity, diagnostics and an HTML report.
The reference strategy is intraday mean reversion. Market, limit and cancel
requests are included, with disclosed L2 execution assumptions.

V1 is a historical replay engine: its simulated strategy does not change the
recorded market path. A CLI that reconstructs books does not, by itself, satisfy
this definition. The first market-order-only implementation is an intermediate
milestone, not the finished release.

The v1 boundary includes:

- A deterministic C++ event loop, explicit time/visibility rules and latency.
- Python strategy/configuration APIs through pybind11.
- Defined order lifecycle and execution/slippage policies for the supported
  order types, including partial fills and unfilled market-order expiry.
- Exact fill-driven accounting, commissions and an explicit valuation policy.
- Continuous multi-day replay, warmup and documented invalid-data behavior.
- A reproducible reference-strategy evaluation on the available qualified data,
  with chronological development/evaluation separation and honest limitations.
- Accounting/execution tests, the planned market-order reference cross-check,
  regression CI, reproducibility artifacts and a results write-up.

Reactive markets, explicit calibrated impact models, DiffLOB integration,
monthly momentum, research-scale divergence experiments and dedicated C++
profiling are v2/later work. Volume-participation and queue-estimation models are
optional refinements with additional data requirements, not v1 blockers. V1
does not promise that aggregate Binance data reveals actual passive FIFO fills.

## 2. Status definitions

- **Done — foundation:** implemented with code/tests or recorded validation
  evidence. This does not mean all integration or production acceptance is done.
- **In progress:** concrete current work exists, but its acceptance/review is
  incomplete. A class appearing in the design does not qualify as implementation.
- **Partial:** reusable foundation exists, but a required v1 extension is absent.
- **Not started:** no corresponding implementation was found in this checkout.
- **Not verified:** external/current operational evidence was not checked in this
  review. This is different from claiming an external task has never been done.

## 3. What is already done

| Completed foundation | Modules | Evidence and remaining boundary |
| --- | --- | --- |
| Binance recorder | M01 | `src/depth_recorder/recorder.py`, `config.py`, `sequence.py`; snapshots, depth, trades, tickers, metadata and receive-time capture. Current host health is not established by this code review. |
| Durable raw storage | M02 | `storage.py`, `jsonutil.py`, `models.py`; WAL recovery, hourly compressed files, ledgers, final manifests and checksum evidence. |
| Offline verification | M03 | `verify.py` and Python `HorizonBook`; merging, deduplication, reconstruction, coverage and ticker checks. This is dataset verification, not trading P&L validation. |
| Exact integer export | M04 | `src/counterback_replay/export.py`; captured tick/lot metadata, exact conversion, deterministic `events.l2`, oracle checkpoints and provenance. Per-day commands exist; a multi-day preparation/cache workflow does not. |
| C++ historical market state | M05 | `HistoricalBook`, `ReplayEngine`, `ReplayStatistics`; snapshot bridges, gaps, absolute L2 updates, trust/horizon and crossed-book handling. Trading-time visibility still needs a runtime contract. |
| Synthetic individual-order matching | M06 | `MatchingEngine`; FIFO, price priority, market/limit orders, partial fills, cancellation and deterministic reports. It remains separate from historical L2. |
| Foundation validation and build infrastructure | M16 | Python tests, matching/replay CTest targets and configured Linux/macOS Debug/Release CI. These cover existing components, not missing strategy/accounting integrations. |
| One real-day reconstruction milestone | M03–M05 | Recorded validation for BTCUSDT 2026-09-14: 862,298 applied updates, zero joint sequence gaps/structural errors, 99.8576% trusted coverage, and C++/Python equality at 172 depth checkpoints plus final state. This was state replay, not a strategy test. |
| WP1 simulation contract and policy primitives | M04, M07–M10 | Frozen availability/order/latency/fee/valuation/validity semantics, machine-readable example and tested helpers. These do not implement the WP2 event loop or WP3 account. |
| WP2 deterministic runtime and adapters | M05–M08, M15–M16 | `BacktestEngine`, queue/clock, immutable `MarketView`, streaming `ReplayMarket`, thin `SyntheticMarket`, stable journal sink and causal/reference tests. See [WP2 validation](wp2-validation.md). Historical fills/accounting remain WP3. |
| WP1 inventory and immutable preparation cache | M03–M04, M15–M16 | `dataset.py` provides read-only metadata inventory, fresh verification/export, content identities, validated reuse and explicit excluded/unavailable/failure states. See [WP1 validation](wp1-validation.md) for current measured scope. |

The complete original evidence and limitations are in
[historical-replay-validation.md](historical-replay-validation.md). Its reported
86 offline Python passes and sanitizer/Release results are past evidence, not a
claim that those entire suites were rerun for this planning task. The real-day
replay was not rerun during this review.

## 4. What is in progress or only partially complete

| Work | Status | What remains |
| --- | --- | --- |
| Architecture, module/class responsibilities and release allocation | **In progress — design** | `docs/system-design.md`, seven diagram sources and README documentation edits exist locally. Review/finalize the v1 contracts and publish the design through the normal Git workflow. |
| WP1 delivery | **Implemented — initial acceptance gate; bulk archive qualification remains pending** | Contract, inventory/cache tooling and synthetic tests exist; the first freshly prepared development day is usable for the next implementation stage. See [WP1 validation](wp1-validation.md) for measured audits and limitations. |
| Market-data collection/qualification | **Metadata inventoried; bulk payload qualification pending; current host operations not verified** | WP1 observed 62 mounted-archive dates and freshly prepared two restored development days. Remaining archive dates are unverified; no additional monthly-momentum dataset is required for v1. |
| Reproducibility | **Partial** | Capture/export/prepared-bundle identities and corruption-checked cache reuse exist. WP2 adds initial ordered event journals; full run configuration, order/fill/fee/equity journals and a backtest `RunManifest` remain missing. |
| Operations/validation | **Partial** | Deployment/offload/pruning scripts and foundation tests exist. Current two-host production acceptance, a broad restored-day audit, and trading-level validation remain unverified or unimplemented. |

**WP2 runtime is implemented.** `BacktestEngine`, `EventQueue`, `SimulationClock`,
`MarketModel`, `MarketView`, `ReplayMarket` and `SyntheticMarket` now have code
and acceptance tests. `OrderManager`, historical execution, `Portfolio`, Python
bindings and reporting remain future work.

## 5. Outstanding v1 work by module

| Module | Current v1 status | Remaining v1 deliverable |
| --- | --- | --- |
| M01 Capture | **Done — foundation; operational acceptance not verified** | Audit current capture/restore evidence required for the selected evaluation dataset; avoid unnecessary recorder rewrites. |
| M02 Raw storage | **Done — foundation** | Reuse immutable originals and restore into a local dataset workspace. |
| M03 Verification | **Done — software; dataset-wide qualification not verified** | Verify all candidate completed days and publish a usable/excluded-day inventory. |
| M04 Integer ingest | **WP1 preparation implemented; multi-day runtime feed outstanding** | Inventory, preparation orchestration, immutable dataset identities and validated reuse now exist. Complete the continuous multi-day trading feed in WP6. |
| M05 Historical market state | **Done — WP2 integration** | Causal availability now gates the unchanged reconstruction path; retain L2 execution limitations in WP3. |
| M06 Synthetic matching | **Done — foundation** | Reuse in the synthetic backend and execution regression scenarios; never route Binance deltas into it. |
| M07 Simulation scheduling | **Done — WP2 foundation** | Queue/clock/coordinator, causal priorities, read-only visibility and generic delayed callbacks. Managed order/account notifications remain WP4. |
| M08 Market backends | **Done — WP2** | Common backend/view contract, `ReplayMarket`, and thin synthetic adapter. Reactive implementation remains v2. |
| M09 Orders and execution | **Not started** | Lifecycle, arrival-time execution, explicit L2 fill/budget policies, market/limit/cancel support and latency. |
| M10 Accounting and costs | **Not started** | Checked money arithmetic, cash/position, commissions, valuation and cost attribution. Explicit impact models are excluded from v1. |
| M11 Strategy API | **Not started** | Python interface/bindings and the mean-reversion reference strategy. |
| M12 Reactive model adapters | **Deferred — v2/later** | No v1 deliverable. |
| M13 Experiments | **Not started** | Configured runs, fresh strategy state, seeds and baseline parameter/cost sweeps. |
| M14 Evaluation and reports | **Not started** | Trading diagnostics, equity/drawdown, costs/fills, HTML report and v1 results write-up. |
| M15 Reproducibility | **Partial** | Extend existing dataset evidence to complete backtest run bundles. |
| M16 Operations and validation | **Partial** | Accounting/lifecycle/causality/integration tests, independent reference comparison, full regression CI and explicit dataset acceptance. |
| M17 Performance profiling | **Deferred — v2** | No dedicated v1 profiling harness required; practical execution/storage feasibility still needs checking. |

## 6. Ordered implementation plan

WP1 and WP2 have passed their initial acceptance gates; WP3–WP8 remain **not
started as implementation**. Bulk day qualification can continue alongside WP3. Finish and demonstrate an acceptance gate before
making the next package depend on it. Reviewable changes can be delivered as
separate branches/PRs; proposed branch names or APIs are not pre-created here.

### WP1 — Freeze v1 semantics and qualify the dataset

**Status: implemented for the initial gate.** The
[frozen contract](v1-execution-contract.md), [preparation guide](dataset-preparation.md)
and [validation record](wp1-validation.md) describe the delivered code and data
scope. Remaining archive-wide qualification does not block starting WP2.

Modules: M03, M04, M07–M10, M13, M15–M16. Data inventory/preparation can proceed
alongside runtime development after the compatibility contract is fixed.

Deliverables:

- Resolve source ordering, snapshot availability, event time, strategy visibility,
  order arrival and notification time; fix same-time event priority and warmup.
  The current update-ID-correct offline replay is not yet proof of a causally
  timed trading simulation.
- Define invalid/horizon-loss behavior, end-of-run handling, and starting account
  constraints. A simple cash-funded spot account is the proposed v1 default;
  margin, leverage and unsupported short positions are not silently introduced.
- Define the baseline execution/fee/valuation policy and label assumptions.
  Keep current snapshot-list selection compatible with existing verification;
  any change requires renewed verification rather than silent divergence.
- Inventory completed Drive days and required hourly files, snapshots, metadata
  and manifests. Restore without modifying the old raw data; verify day by day.
- Prepare reusable local event bundles keyed by source hashes, instrument grid
  and preparation-rule version. A strategy/config change should reuse the input
  bundle; a changed source/conversion contract requires a new bundle.
- Record qualified/excluded days and invalid intervals, storage requirements and
  chronological development/evaluation partitions. Do not choose days by results.

**Acceptance:** written policies plus small causal examples; a passing prepared
day; reproducible dataset identities; a candidate-day inventory with explicit
quality decisions. The whole reported two-month archive is not assumed verified.
WP1 tools preserve unverified candidates rather than silently marking them accepted.

### WP2 — Build the deterministic runtime and backend adapters

**Status: implemented and acceptance-tested.** See the [runtime guide](wp2-runtime.md)
and [validation record](wp2-validation.md). The synthetic path supports order-level
fixtures; historical order execution/accounting remains WP3.

Modules: M05–M08, M15. Depends on WP1's time/validity contracts.

- Implement `SimulationClock`, `EventQueue`, `BacktestEngine`, `MarketModel`,
  read-only `MarketView` and `ReplayMarket`. Add the thin synthetic adapter around
  the existing matcher for supported order-level scenarios.
- Stream the prepared local input on each run. Drive access, raw decompression
  and cross-recorder preparation stay outside the per-run simulation loop.
- Schedule events in one deterministic order; expose no future snapshots, prices
  or features through the strategy view.
- Introduce stable event IDs and the first journal records. Preserve L2 trust and
  distinguish unavailable depth from an empty book.

**Acceptance:** identical tapes/configuration produce identical ordered outputs;
fixtures verify timestamp ties, delayed data/snapshots, gaps and invalid views;
the replay adapter still agrees with its reference under the declared contract.

### WP3 — Complete the first vertical slice: market orders and accounting

Modules: M09–M10, M15–M16. Depends on WP2.

- Use scripted orders first to remove strategy/binding uncertainty.
- Implement arrival-time visible-depth execution, checked liquidity consumption,
  partial fills and expiry of insufficient market-order remainders. Stop at the
  known/trusted horizon; never assume unknown outer liquidity exists.
- Implement `LiquidityBudget`, normalized `FillRecord`/`ExecutionOutcome`,
  `Portfolio`, `FeeModel`, `ValuationPolicy` and exact/checked money arithmetic.
- Journal order, fill, fee, position, cash, equity and validity transitions.
  Keep cash charges distinct from slippage already present in execution prices.

**Acceptance:** hand-calculated buy/sell/depth-walk/insufficient-liquidity examples
match; quantities reconcile; each fill/fee is applied once; overflow and invalid
data fail explicitly. A constant-price zero-fee round trip gives zero P&L, while
spread/fees create the expected loss when enabled. Do not assert that equity is
constant when market prices legitimately change.

This milestone demonstrates a real trading simulation, but remains short of v1
because limit/cancel support, the Python API, multi-day evaluation and reports
still need completion.

### WP4 — Finish v1 order lifecycle, limit/cancel behavior and latency

Modules: M07, M09–M10, M16. Depends on WP3.

- Implement pending/accepted/rejected/resting/partially-filled/cancelled/expired
  lifecycle states and deterministic ID handling.
- Add marketable-limit execution with price constraints and supported resting
  limit/cancel behavior under a documented conservative L2 model. Specify fill
  price and liquidity-role assumptions; do not infer exact maker FIFO position
  or equate every depth reduction with an execution.
- Implement arrival and notification delay, starting with zero/fixed delay and
  adding configured seeded stochastic delay for the v1 latency contract.
- Resolve cancellation races, multiple outstanding orders and delayed portfolio/
  order knowledge without leaking authoritative future notifications.

**Acceptance:** lifecycle/race fixtures cover both sides, partial fills, limit
eligibility, non-execution, cancellation, expiry and seeded repeatability. Lack
of L3 data is disclosed rather than converted into a claim of exact queue fills.
Participation and queue-estimation extensions are not required for this gate.

### WP5 — Expose the Python API and configured run command

Modules: M11, M13, M15–M16. Depends on the tested WP3/WP4 core.

- Add pybind11 packaging and `StrategyBridge` with clear object lifetimes and
  read-only book/trade/clock/portfolio views.
- Implement the documented Python `Strategy.on_event(...) -> orders` interface.
  The v1 feed capability contract must state which trade information is actually
  available; the current integer export has no public-trade stream.
- Add validated `RunConfig`, fresh strategy instances, explicit seed streams,
  failure handling, and a single command to run a saved configuration.
- Produce `RunManifest`/`RunResult` artifacts with resolved config, code/environment
  identities and input hashes. No ad hoc source edits should be needed per run.

**Acceptance:** a Python smoke strategy places, fills and cancels supported orders
through the real core; failures are surfaced cleanly; the same config reproduces
the same committed results; the package installs and runs in a clean environment.

### WP6 — Support continuous multi-day runs

Modules: M04, M07–M10, M13, M15–M16. Depends on WP1–WP5.

- Define a dataset-day iterator/session policy and bridge day boundaries without
  using a later snapshot before it would be available.
- Carry cash, positions, outstanding orders, pending delays, IDs and strategy
  indicators across UTC midnight. Do not silently reset the account each day.
- Specify weekend/day handling for the selected continuous crypto data, missing
  days/segments, warmup, and terminal open positions/orders. Record any optional
  independent-day resets as a distinct experiment configuration.

**Acceptance:** several consecutive-day fixtures and a short real-data run agree
with a logically equivalent uninterrupted run where applicable; midnight/gaps/
pending-action cases are covered; invalid data never silently generates returns.

### WP7 — Implement the reference strategy and evaluate qualified history

Modules: M11, M13–M15. Depends on WP5/WP6 and prepared qualified days.

- Implement one specified intraday mean-reversion strategy with causal signal
  windows, warmup, order sizing and position limits. Define parameters before
  touching the reserved evaluation period.
- Use short examples for development, then the available qualified history from
  the two months+ of recordings. Keep a chronological development/calibration
  period and a later reserved evaluation period.
- Run the reference strategy with declared fees, execution/slippage and latency;
  add controlled baseline/cost sensitivity comparisons. Report failures, excluded
  periods and limited sample size; profitability is not a release criterion.
- Preserve complete configurations/results. Monthly momentum is not required
  and does not create a new data-collection dependency for v1.

**Acceptance:** held-out evaluation artifacts are reproducible, every result has
a dataset/config identity, and reported P&L reconciles with fill/fee/account logs.
A single reconstruction day does not constitute multi-day strategy evaluation.

### WP8 — Complete reporting, independent validation and release

Modules: M13–M16. Reporting can be developed incrementally as artifacts stabilize.

- Implement `MetricsCalculator` and `ReportBuilder`: equity/drawdown, net/gross
  performance, cash/position, turnover, fees, execution-cost attribution, fill
  diagnostics, validity coverage, assumptions and reproducibility references.
- Deliver the self-contained HTML report and v1 results write-up. Compute risk
  statistics only when their sampling/inputs support them; record selection/trial
  information needed for the promised Deflated Sharpe analysis, and explicitly
  mark a statistic unestimable when evidence is insufficient.
- Complete the README's market-order-only NautilusTrader reference comparison on
  identical controlled scenarios with comparable fees/fill assumptions; document
  and resolve/explain divergences rather than relying on unrelated defaults.
- Add accounting, causality, lifecycle, multi-day and Python-binding integration
  tests to CI. Preserve the existing matcher, recorder and replay regression tests.
- Run the complete offline Python suite, C++ Debug/Release and sanitizer checks;
  distinguish any excluded live tests and external operational checks.
- Document installation, preparation/reuse, one-command backtest execution,
  report reproduction and known limitations. Commit/push reviewed changes through
  PRs and pass CI before marking/tagging a v1 release.

**Acceptance:** a fresh checkout/environment can reproduce a documented run and
its report from the selected prepared dataset, with correct accounting, passing
required checks and no hidden invalid-data or unsupported-fill assumptions.

## 7. Data-use plan

The day-level preparation workflow already exists. The planned multi-day layer
must automate and index it, rather than repeat Drive downloads and raw-file
merging for each strategy run:

1. Restore completed days from Drive into a preserved local raw-data workspace.
2. Verify each selected day into a new immutable report directory.
3. Export a qualified day once per source/preparation version and register its
   content identity. Keep failed/excluded days visible in the inventory.
4. Each run reads the selected prepared event files and builds its own market,
   strategy, execution and account state; it does not mutate the input bundles.
5. Reuse prepared inputs when strategy/cost parameters change. Create a new
   prepared version if source data, grids or sequencing/conversion policies change.

Bulk qualification can start before the trading runtime is finished. The first
working backtest uses a small trusted interval, then consecutive days, then the
qualified multi-day evaluation set during v1. More recordings can improve the
study, but v1 development does not wait for a monthly-momentum dataset. Do not
claim that every date on Drive is complete or accepted without examining it.

## 8. Release checklist

The following are completion gates, not claims of current success:

- [ ] V1 time, validity, execution, fee and valuation policies are resolved.
- [ ] Dataset inventory and immutable prepared bundles identify usable/excluded data.
- [ ] Python strategies run through the deterministic C++ coordinator.
- [ ] Market/limit/cancel lifecycle and latency work under declared assumptions.
- [ ] Cash, position, fees and equity reconcile with committed fills.
- [ ] Known liquidity cannot be consumed repeatedly without the declared replenishment.
- [ ] Gaps/unknown horizons cannot silently produce fills or valid-looking results.
- [ ] Multi-day runs preserve account, order, delay and strategy state.
- [ ] Reference mean reversion has a documented chronological evaluation.
- [ ] Reports, run manifests and raw diagnostic artifacts are reproducible.
- [ ] Required reference comparisons, regressions, CI and sanitizer checks pass.
- [ ] Installation, workflow, assumptions and results write-up are complete.

## 9. Evidence reviewed and verification limits

This plan is based on tracked source/interfaces, local WP1/design changes and
recorded validation. The initial planning review and subsequent WP1 implementation
checks below are separate audits. Neither establishes current recorder-host
health, complete archive payload qualification or a trading-runtime implementation.

The review freshly configured and built the existing C++ targets in the isolated
ignored `build/v1-status-audit/` directory with Python differential CTest enabled.
**All three CTest targets passed:** matching engine, historical replay and the
Python/C++ differential suite. The command was:

```sh
cmake -S . -B build/v1-status-audit -DCMAKE_BUILD_TYPE=Debug \
  -DCOUNTERBACK_PYTHON_TESTS=ON -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build/v1-status-audit --parallel
ctest --test-dir build/v1-status-audit --output-on-failure
```

That initial planning audit did not rerun the complete Python suite, full-day
replay, Release/sanitizer configurations or external checks. Subsequent WP1
implementation checks did run the complete offline suite (101 passed, two live
tests deselected), the expanded Debug CTest target (3/3 passed), and fresh
verification/export/C++ comparison/cache-reuse checks for 10/14 September.
The [WP1 validation record](wp1-validation.md) gives exact scope and hashes,
including metadata-only inventory of 62 mounted Drive dates. No remote CI,
new Release/sanitizer run or live host-health check is claimed for WP1.

Relevant sources:

- [Project scope and roadmap](../README.md).
- [System modules, releases and classes](system-design.md).
- [Current matching contract](cpp-engine.md).
- [Current replay/ingest contract](historical-replay.md).
- [Recorded real-day and regression evidence](historical-replay-validation.md).
- [Operational acceptance requirements](runbook.md#seven-day-production-acceptance).

**Immediate next implementation:** WP3's market-order execution/accounting slice
on the tested WP2 runtime and adapters. Continue bulk qualification of
the discovered archive using the implemented WP1 preparation tooling.
Do not start v2 response models to compensate for an incomplete v1 trading loop.
