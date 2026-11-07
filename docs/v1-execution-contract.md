# V1 simulation contract — WP1

Contract: `counterback-v1-contract-1`. Preparation: `counterback-preparation-1`.
Availability rule: `merged-receive-prefix-bridge-1`.

This freezes the baseline semantics for WP2–WP6. The tested Python primitives in
`src/counterback_replay/contract.py` specify ordering, availability, delay and
money rules; they do not implement an event loop, strategy executor or portfolio.
[The reference policy JSON](../config/v1-policy.example.json) is machine-readable.
It is a policy example, not an already-supported backtest run configuration.

## Time, sequencing and snapshot availability

1. Preserve the existing verifier merge key `(U, u, rx_utc)` and input-recorder
   priority for exact ties. Deduplicate raw updates before normalizing decimal
   spelling; conflicting duplicates fail. Ignored duplicate/stale observations
   must not create liquidity, callbacks or a new market clock tick.
2. Preserve the current source-list snapshot cursor and bridge predicate
   `U <= snapshot_id + 1 <= u`, with `snapshot_id < u`. Do not independently
   globally sort snapshots in C++ or silently change existing report semantics.
3. For each unique applied event, its earliest strategy-visible market time is
   `max(previous_visible_time, event.rx_utc, selected_bridge_snapshot.rx_utc)`.
   Without a new bridge, omit the snapshot term. This prevents either receive-
   time regressions or use of a snapshot before its capture observation exists.
   Pending/unanchored observations never expose a future book to a strategy.
4. Exchange `E` remains provenance/diagnostic time, not the availability clock.
   Monotonic receive timestamps are meaningful only within one recorder/run.
   Merging two recorder feeds is a declared reconstructed-feed experiment; it
   is not a measurement of a strategy's actual network latency. Clock alignment
   and late bridges can distort elapsed market timing; report that policy.
5. Run time intervals are half-open `[start_ns, end_ns)`. Warmup inputs must be
   strictly available by their simulated time. The strategy cannot trade before
   a trusted anchor, the configured trusted-update warmup and its declared
   feature-history requirements are satisfied. The reference numeric warmup is
   zero; a real strategy must declare its own lookback/readiness.

The existing `events.l2` header lists snapshots before depth for reconstruction
convenience. Loading that header is not permission to reveal every snapshot at
time zero. WP2 must implement the availability gate above while keeping the
reference reconstruction path intact. Prepared bundles are therefore qualified
for reconstruction, not certified as completed causal trading simulations.

## Same-time ordering and causal children

The scheduling key is `(time_ns, causal_round, priority, insertion_sequence)`.
Within one round, the priorities are:

| Priority | Work |
| --- | --- |
| 0 | Market state updates, preserving their source sequence |
| 1 | Order arrivals |
| 2 | Cancellation arrivals |
| 3 | Execution/account notifications |
| 4 | Strategy callbacks |
| 5 | Valuation/report observations |
| 6 | End-of-run processing |

A child generated at the current time stays in the current round only if its
priority is later than the parent's. Otherwise use the next causal round. Global
insertion sequence increases for every scheduled event. Never schedule into
the past. This avoids sorting a zero-latency order before the callback that
created it. An order arrival wins a same-time cancellation race within a round;
this is an explicit conservative convention, not an exchange measurement.

Delay is an integer nonnegative number of nanoseconds with uint64 overflow
checks. The reference policy uses zero delay for deterministic fixtures; run
configuration must state its synthetic order/notification latency. Seeded
stochastic variants remain WP4 implementation work. Do not use wall-clock sleep,
Python callback duration or GPU inference duration as market time.

| Causal example | Required outcome |
| --- | --- |
| An update is received at 100, its selected bridge snapshot at 150 | Its book cannot be exposed before 150. |
| The next sequence-ordered update has receive time 120 | Its visible time remains at least 150; the simulation clock never rewinds. |
| A callback at time 150 requests an order with zero latency | Its arrival occurs at 150 in a later causal round, after that callback. |
| Order and cancel arrive at the same time/round | Arrival is processed first; cancellation observes any fills already committed. |
| A fill at 200 has notification delay 20 | The authoritative account changes at execution; strategy knowledge changes at 220. |

## Account, order and execution baseline

- One BTCUSDT, cash-funded spot account; no leverage, margin or short inventory.
  Starting balances are explicit run parameters; the initial example is 10,000
  USDT and zero BTC. Cash and inventory are never allowed to become negative.
- The first vertical slice supports market orders. Full v1 also supports
  marketable limit orders, resting limits and cancellation under the rules here.
  Unsupported order types or malformed/off-grid values are rejected.
- At arrival, dry-run against current eligible liquidity and unreserved funds.
  If the simulated executable quantity cannot be funded, reject before changing
  liquidity/account state. An oversize sell beyond unreserved inventory rejects.
  Multiple arrivals see earlier committed fills/reservations in event order.
- Resting buy limits reserve quote cash at their limit price plus the upper-bound
  cumulative fee; sell limits reserve inventory. Release/adjust reservations on
  fill, cancellation or termination. Pending requests do not reserve liquidity;
  arrival validation decides acceptance against the then-current account.
- Markets and marketable limits walk best prices first using absolute visible
  quantities. Buy limits never fill above their limit; sells never below it.
  Partial market fills expire the remainder; limits may retain a remainder.
- Consume only observed depth inside the snapshot's known horizon. A trusted top
  does not make outer depth complete. Missing eligible liquidity produces a
  disclosed partial/unfilled result, not invented levels.
- Maintain a separate per-price liquidity-consumption budget. Reset it only when
  a new applied absolute observation for that price or a fresh snapshot replaces
  it. Duplicates, stale batches and repeated strategy queries do not replenish it.
  Resetting on a new absolute observation is an explicit replenishment assumption.
- For v1 resting limits, use **crossing-only eligibility**: leave them unfilled
  until a later trusted opposing quote becomes marketable at their limit, then
  apply the same depth/price/budget constraints. A quote merely touching the
  same-side queue or an L2 reduction is not proof of a passive fill.
- Label all baseline fills as modeled taker-like executions and apply the
  configured taker benchmark charge. Do not claim maker queue position, maker
  rebates or exact passive execution from L2. Synthetic FIFO tests can still use
  `MatchingEngine`; do not route Binance aggregate updates through that matcher.

## Fees, money, valuation and end of run

Prices/quantities remain exact integer ticks/lots using captured grid metadata.
Use integer quote atoms, with eight decimal places in the BTCUSDT reference
policy. Reject a grid/notional that cannot be represented exactly, and use
checked signed-128-bit-range arithmetic for intermediate notionals. Python
policy helpers use exact fractions; a future C++ implementation must enforce
the same bounds. Analytics cannot mutate the accounting ledger.

The reference fee rate is **0.001 (0.1%) as a benchmark assumption**, not a claim
about Binance's current or the user's actual account fee. It is an explicit run
parameter and may be zero only for a labeled baseline/test. Fees are in quote
currency. For an order with cumulative executed notional `N`, total charged fee
is `ceil(N * rate)` in quote atoms. Each fill charges the increase in that total,
so splitting one order into more fills does not repeatedly round the same fee.
Record each execution and fee once. Spread/depth slippage is already reflected
in fill prices; do not deduct it again as a separate cash cost. Calibrated impact
charges and reactive market response belong to v2.

Use weighted-average acquisition cost for the long position's gross realized/
unrealized P&L attribution, with fees reported separately. Maintain the basis
exactly (or as a checked rational for attribution) until presentation; do not
round it into cash movements. A partial sale retains the remaining average
basis; a flat position resets it. Nonzero starting inventory needs an explicit
initial basis/reference mark so historical profits are not invented.

Long inventory is marked at the latest **trusted best bid**. This is a valuation
convention, not a promise that the entire position could be liquidated at that
price. Report cash, inventory, realized/unrealized components and validity.
At the configured end, stop new actions, expire pending unexecuted requests,
terminate local resting remainders, and mark open inventory. Do not invent a
terminal liquidation, fee or round-trip return. Delayed notifications may remain
undelivered, while already-executed fills remain in the authoritative ledger.

Before the first valid anchor, remain in bootstrap/warmup with no fills. After
trading begins, a required top/horizon loss, unbridged sequence gap, crossed book
or structural error **halts the trading segment**. Do not automatically reset
the account or resume across missing data. Preserve holdings and diagnostics;
label terminal valuation invalid if it lacks a trusted mark. A future explicitly
versioned segmented-run policy may define recovery; this baseline does not.

## Preparation identity and evaluation partition

`dataset.py` inventories filenames/logical sizes without reading payloads. A
discovered directory is not a verification pass. Preparation hashes finalized
inputs, checks captured metadata, invokes the unchanged verifier into a fresh
directory, requires `status: pass`, and runs the existing exact export/oracle.
Source/input order is part of the identity. Source root paths are provenance
but not content identity, so identical restored copies can share a bundle.

Cache identity includes the raw file/manifest hashes, instrument grid,
preparation/availability versions, relevant reader/reference/export source hashes
and checkpoint interval. Strategy parameters, starting cash, fee rate and run
latency do not belong to dataset preparation identity. Reusing preparation
rechecks raw and bundle hashes but skips decompression/reconstruction on a cache
hit. Publish a bundle by renaming a completed staging directory; incomplete or
failed staging directories are diagnostics and are never returned as prepared.

The initial archive inventory fixes **2026-10-15** as the evaluation start date:
earlier dates are development; that date onward is reserved evaluation. This
choice is recorded before any strategy results and is not silently moved when
a day fails data-quality checks. Each new inventory has a content ID. Changing
the partition must create a new declared experiment specification and must not
be represented as an untouched holdout after its performance has been examined.

WP1's first gate is this contract, tested policy examples, a complete candidate
inventory with honest unverified/excluded states, and at least one freshly
qualified reusable day. Qualifying every archived day remains batch work before
the full-history v1 evaluation; WP2 can start from the first usable bundle.
