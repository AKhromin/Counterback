# WP3 — market orders and accounting

WP3 connects scripted historical market orders to the WP2 runtime and a checked,
cash-funded spot account. The [WP1 contract](v1-execution-contract.md) defines the
semantics; [WP3 validation](wp3-validation.md) records the acceptance evidence.
This is the first trading slice. Limit/cancel management and delayed account
notifications are WP4; Python strategy bindings and run manifests are WP5.

## Run the example

From the repository root:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel
ctest --test-dir build --output-on-failure
./build/engine/counterback_market_orders \
  engine/examples/runtime-fixture.l2 engine/examples/market-orders.txt \
  0 300 1000000000000 0.001 8
```

The account starts with 10,000 USDT, zero BTC and a **benchmark** fee of 0.1%.
The script buys 200,000 lots (2 BTC) at 101 USDT, then sells them at 100 USDT.
Expected gross realized P&L is −2 USDT, fees are 0.402 USDT, and final cash/equity
is **9,997.598 USDT** (`999759800000` quote atoms), with zero inventory/basis.
The recorded book remains exogenous; consuming liquidity changes a separate
budget, never the reconstruction or market view.

```text
counterback_market_orders INPUT ORDERS START_NS END_NS CASH_ATOMS FEE_RATE QUOTE_DECIMALS
                          [INITIAL_LOTS TOTAL_BASIS_ATOMS]
```

`INPUT` is an immutable, qualified WP1 `events.l2` bundle. Time is simulated UTC
receive-clock nanoseconds for real data; the interval is `[start, end)`. The
script is a text file containing:

```text
# submission time, fixed arrival delay, unique ID, side, quantity in lots
150 0 1 buy 200000
200 0 2 sell 200000
```

Each line schedules a C++ callback at submission time. It must fall inside the
run interval and have a trusted, ready market. A submission during bootstrap
fails explicitly; the demo does not silently move it to a future anchor. Zero
delay still uses the next causal round after the callback. Delayed arrivals see
intervening market observations. Orders arriving at/after end expire without
execution. Nonzero starting inventory requires its explicit total acquisition
basis; the CLI accepts an integer basis, and the C++ API also accepts a rational.

Exit 0 means normal end, 2 means a trust-loss halt, and 1 means a configuration,
input, arithmetic or output failure. Check `ever_ready`, `halted` and `reason`;
an empty script completing without a trusted anchor is not a trading success.

## C++ components and integration

Link against `counterback::runtime`, include `counterback/backtest_engine.hpp`,
construct a fresh `ReplayMarket`, and set `RuntimeConfig.account` to an explicit
`AccountConfig`. The observation-only WP2 configuration still leaves accounting
disabled. `submit_after(Order, delay_ns)` then supports historical market orders.
`portfolio()` exposes const authoritative account state; `RuntimeResult.account`
contains the last valuation, including the terminal end/halt observation.

| Component | Responsibility |
| --- | --- |
| `MarketOrderExecution` | Validate arrivals, dry-run best-first eligible depth, normalize fills, reject unfunded executions and commit the account/budget. |
| `LiquidityBudget` | Track consumption by book side and price; enforce available quantities and reset only explicitly observed prices or a replacing snapshot. |
| `FillRecord` / `ExecutionOutcome` | Run-scoped fill/order IDs, symbol, execution time, side, ticks/lots, exact notional/fee and filled/expired/rejected outcome with reason/remainder. |
| `MoneyGrid` / `Money` | Convert captured tick/lot metadata into quote atoms and check signed 128-bit arithmetic. |
| `Exact` | Reduced checked rational for gross acquisition basis and P&L attribution. |
| `FeeModel` | Exact configured rate and cumulative-per-order ceiling, charged incrementally per fill. |
| `Portfolio` | Apply a validated whole outcome once; maintain cash, long inventory, gross average basis, gross realized P&L and fees separately. |
| `ValuationPolicy` | Mark long inventory at the current trusted best bid; invalidate equity when required market trust is lost. |

Accounting is enabled for historical L2 only. The existing synthetic matcher
retains its WP2 fixture interface; converting its reports into this ledger is a
separate adapter extension. Direct `ReplayMarket.submit()` remains unsupported:
historical actions go through the execution policy, not through the FIFO matcher.

## Execution and money rules

- Walk asks for buys and bids for sells, best first, stopping at the snapshot's
  known horizon. Even an observed price beyond that horizon is ineligible.
- The executable remainder expires immediately if eligible liquidity runs out.
  It never rests or waits for another observation. Quantities reconcile as
  `requested = executed + remaining` for valid requests, including rejections.
- Pending orders reserve neither funds nor liquidity. Each arrival sees earlier
  committed fills. An unfundable buy rejects its entire dry-run; an oversize sell
  rejects if the requested quantity exceeds inventory, even if depth is smaller.
  Rejections leave cash, positions, fees, liquidity and committed fill IDs intact.
- A new applied absolute observation resets the budget at that side/price,
  including an unchanged quantity or deletion. A selected replacing snapshot
  resets all prices, including when its receive timestamp is zero. Duplicate,
  stale, unrelated-price observations and repeated queries do not replenish.
- Arrival IDs are nonzero and unique for this execution instance, including
  rejected/expired arrivals. Repeated IDs reject without applying anything again.
  The ledger separately rejects repeated order/fill applications and inconsistent
  fees, notionals, quantities, symbols or statuses.
- Fees are quote-currency cash charges: `ceil(cumulative_order_notional * rate)`.
  Each fill charges the increase. Spread and depth walking already affect fill
  prices and are never deducted again. All fills are labeled modeled taker-like
  executions; they are not observed trades or evidence of maker queue priority.
- Notionals, cash, fees, cumulative totals, inventory and rational arithmetic
  fail explicitly on overflow. A nonintegral quote-atom notional is an error.
  Cash/fee storage uses signed 128-bit integers; inventory stays signed 64-bit.
  Rational numerator/denominator/intermediates are checked too: sufficiently
  complex long-lived basis fractions can exceed that bound and stop the run.
  Supported accounting builds require GCC/Clang 128-bit integer support.
- Gross weighted-average basis excludes fees. Partial sales retain an exact
  fraction; a flat position has zero basis. Fees remain a separate attribution.
  Equity is cash plus bid-marked inventory, not a promise of full-size liquidation
  at the bid, and may legitimately change when market prices change.

At end, all pending historical orders are expired and open inventory is marked;
there is no forced terminal sale. After first readiness, required trust loss
halts permanently, expires pending orders, preserves holdings and gives a null,
invalid terminal equity. No stale mark or automatic resumption is introduced.

## Journal contract

The CLI emits JSON Lines: a resolved `trading_config`, the ordered `runtime_event`
rows, and a `trading_summary`. Each runtime row keeps WP2 IDs, causal keys, source
times, market validity and horizons, and adds these optional fields:

- `outcome`: order request, acceptance/expiry/rejection reason, executed/remaining
  lots, and normalized fills with exact incremental fees and model labels.
- `account`: authoritative cash, inventory, basis, realized gross P&L, cumulative
  fees, trusted bid mark, equity and unrealized gross P&L. These are present even
  on non-order events so price changes and terminal valuation are observable.
- `terminated_orders`: requests still pending at end/halt, with no invented fills.
- `validity_transition`: the previous and current validity when it changes.
  Market rows also identify whether a snapshot replaced the book.

Quote-atom values are **decimal strings**, preserving 128-bit precision in JSON
consumers. Rational values are `{numerator, denominator}` string pairs. Convert
atoms by `10**quote_decimals`; price ticks and inventory lots still use the
captured instrument grid. A fill and its fee form one atomic posting; the account
in the same event is its committed result. No separate fee debit is needed.

The runtime streams journal rows and retains only state, outstanding actions and
lifetime duplicate-ID sets. The CLI loads the small scripted order file into
scheduled callbacks. Full historical journals can be large. Save inputs and
configuration alongside the output; complete run bundles/manifests, latency-
delayed strategy knowledge, Python APIs, multi-day sessions and reports remain
later work. Do not use this authoritative portfolio as a delayed-notification
strategy view before WP4 defines that separate interface.
