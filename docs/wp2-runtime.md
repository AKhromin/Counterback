# WP2 runtime and backend adapters

WP2 implements the deterministic C++ coordinator and its historical/synthetic
backends. [Validation](wp2-validation.md) records the acceptance evidence.
The [WP1 contract](v1-execution-contract.md) remains the source of time and
validity rules. [WP3 now adds historical order fills, portfolio accounting and
fees](wp3-market-orders.md); Python strategy bindings are WP5. This page describes
the WP2 observation/synthetic interfaces and their original acceptance boundary.

## How the pieces fit

| Component | Job | Implementation |
| --- | --- | --- |
| `SimulationClock` | Holds simulated nanoseconds and refuses to rewind. No wall-clock sleeps. | `engine/include/counterback/backtest_engine.hpp` |
| `EventQueue` | Chooses the next event by time, causal round, priority, then insertion sequence. | `engine/src/backtest_engine.cpp` |
| `BacktestEngine` | Advances the clock, dispatches market events and C++ callbacks, gates readiness, halts on trust loss and journals results. | Same runtime files |
| `MarketModel` | Declares backend capabilities and separates input lookahead from publishing state. | `engine/include/counterback/market_model.hpp` |
| `ReplayMarket` | Streams the prepared L2 tape through the existing reconstruction engine at causal availability times. | `engine/src/market_model.cpp` |
| `SyntheticMarket` | Passes explicit individual-order fixtures and scheduled orders/cancels to the existing FIFO matcher. | Same backend files |
| `MarketView` | An immutable snapshot of currently published depth, best prices, instrument/grid, sequence, visibility time and trust boundaries. | Same backend files |
| `PreparedReplayReader` | Checks the prepared format and streams depth records individually. Also used by the original replay CLI. | `engine/src/prepared_replay.cpp` |

The normal path is:

```text
prepared events.l2 -> ReplayMarket.next_event() -> EventQueue
                                                  |
                                           SimulationClock
                                                  |
                               ReplayMarket.advance() publishes state
                                                  |
                                      read-only MarketView
                                                  |
                                  C++ callback + journal sink
```

The reader loads only the snapshot header and one pending depth record. Raw
decompression, Drive access, merged-recorder preparation and oracle generation
stay outside the simulation. Full depth is copied into a cached immutable view
when requested; retaining old views cannot reveal or mutate a later book.
Callers that retain every view also retain those depth snapshots. The coordinator
does not keep the full event tape or journal in memory.

## Run a small example

From the repository root:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel
ctest --test-dir build --output-on-failure
./build/engine/counterback_runtime engine/examples/runtime-fixture.l2 0 300
```

The fixture uses small illustrative nanosecond values. The snapshot is received
at 150, so updates received at 100 and 120 both become visible at 150. Both
market updates run before the callbacks at that timestamp. The callback sees
the final same-time state, with update ID 102 and a best-bid quantity of 500,000
lots. The duplicate received at 9,999 produces no market event, clock tick or
callback. Updates 103 and 104 are visible at 200 and 210. The run ends at 300.

The executable is an observation-only runtime demonstration. Its C++ callback
does not place historical orders or calculate P&L.

For prepared data:

```text
counterback_runtime INPUT START_NS END_NS [JOURNAL_EVERY] [WARMUP_UPDATES]
```

Start/end are UTC receive-clock nanoseconds for real tapes. The trading interval
is half-open `[start, end)`; earlier inputs are processed as causal warmup.
`JOURNAL_EVERY` defaults to 1. A larger value samples journal event IDs and
full-depth reconstruction checkpoints by source-record ID; invalid views and
termination are always logged. Sampling does not alter event IDs or dispatch.
Use the full sink/default interval when an exhaustive journal is required.

Each output line is JSON:

- `runtime_event`: stable run-scoped ID, parent ID, scheduling key, source ID,
  receive/exchange/bridge times where applicable, visible top/horizons, validity
  and readiness. IDs are insertion identities; dispatch order follows the full
  scheduling key, so IDs need not appear in numeric order.
- `checkpoint` / `summary`: the unchanged offline reconstruction schema, useful
  for comparing observed depth and counters to the Python oracle.
- `runtime_summary`: completion/halt reason, stop time, readiness and counts.

Exit 0 means the configured observation run ended, including an explicitly
reported run that never became ready. Exit 2 means an active historical segment
halted on required trust loss. Exit 1 means malformed input, configuration or
an exception. These exit codes do not certify profitable trading or archive-wide
data quality. Check `ever_ready`, `halted` and `reason` together.

## Timing, visibility and validity

`ReplayEngine::timing()` uses the same pure bridge-selection plan as `apply()`.
The adapter never applies a pending non-ignored update until its availability
time. Visibility is clamped to the preceding published market time, receive time
and selected bridge's receive time. Unanchored diagnostics also keep the clock
monotone. Exchange time remains provenance. Exact duplicate and stale records
still contribute to reconstruction statistics but do not publish market state.

Exogenous same-time market records stay in round zero and preserve source order.
They are not causal children of the preceding record. Strategy-generated work
uses WP1's child rule: a same-time child with an equal/earlier priority moves to
the next round. This allows a zero-delay order to follow its triggering callback
while all initially tied market inputs precede arrivals and callbacks.

Readiness requires trusted top prices, the configured start time, and the
configured count of trusted applied updates. A warmup callback may inspect its
causal view but must check `engine.ready()` before requesting actions. There is
an explicit start boundary even when no market event occurs at that time.
Initial invalid inputs keep the runtime in bootstrap. After first readiness,
historical horizon loss, gaps or structural corruption halt the segment
permanently; later snapshots cannot resume that engine instance.

`quantity_at(side, price)` distinguishes known absence (`0`) from unavailable
depth (`nullopt`). Prices outside historical snapshot boundaries remain unknown,
including observed outer levels; `bids()`/`asks()` expose the observed records
alongside the boundaries so execution policies can filter eligibility. An empty
synthetic side is known empty. A missing historical anchor is unavailable.
No backend currently advertises a public-trade feed.

## C++ integration and current boundary

Link against `counterback::runtime` and include
`counterback/backtest_engine.hpp`. Construct a fresh backend and engine for each
run; the input stream must outlive `ReplayMarket`. `run(callback)` delivers
read-only views after non-ignored market observations, including warmup. A view
can safely be retained by value. The engine is single-use and stops permanently
if a callback or sink throws.

`schedule(time, priority, callback)` supports deterministic scripted timers and
notifications. `submit_after(order, delay_ns)` and `cancel_after(id, delay_ns)`
retain the synthetic order-level fixture interface. WP3 also enables historical
`submit_after()` when `RuntimeConfig.account` is explicitly configured; historical
cancellation remains unsupported. Delayed actions see
intervening market changes; same-round arrivals precede cancellation. Actions
exactly at end expire without execution, and later queued work remains
undelivered in the termination diagnostics. Synthetic execution reports come
directly from the existing matcher; no portfolio is maintained.

The runtime stops publishing source events at end or halt. It may have read one
future record to determine its time, but never publishes its book. Consequently
an early-stop run does not validate the unread footer/remainder of the input.
Use an immutable WP1-prepared bundle with verified hashes; full-input integrity
checks belong outside the per-event loop.

WP3 adds historical execution, liquidity budgets, account state, fees,
fill-driven accounting and pending market expiry at end/halt. WP4 will add managed
order lifecycles, resting-order teardown, delayed authoritative-versus-visible account
knowledge and stochastic latency. The current generic notification scheduler
does not claim those features. Complete run manifests/configuration and Python
bindings remain WP5. Continuous multi-day sessions remain WP6.
