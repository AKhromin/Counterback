# WP3 validation — 8 November 2026

WP3's first trading slice is implemented: historical market orders arriving
through the WP2 queue execute against trusted visible depth, consume a separate
checked budget, update an exact spot ledger and produce order/fill/fee/account/
valuation journals. See the [guide and runnable example](wp3-market-orders.md).

## Acceptance and regression evidence

- **123 offline Python tests passed**, with two live Binance tests deselected;
  combined Python package coverage including branches was **84.37%**. That metric
  covers recorder/replay Python, not C++ accounting line coverage.
- Debug and Release CTest: **5/5 passed** in each build, including the new
  `market_execution_accounting` target and a **62-case** Python/reference target.
- AddressSanitizer + UndefinedBehaviorSanitizer: **4/4 C++ targets passed**.
- Ruff, strict mypy for both Python packages and `git diff --check` passed.
  The C++ runtime compiles with warnings treated as errors.

The initial full Python run passed 122 tests but the existing local WebSocket
test could not bind a temporary localhost port under the execution restrictions.
The permitted full rerun passed all 123. Subsequent final C++ changes (fill symbol
validation and encapsulated rational invariants) were verified by all three C++
builds and the complete 62-case Python/reference target.

The C++ acceptance fixtures include hand-calculated buys and sells, both depth
walks, insufficient known liquidity, observed-but-ineligible outer levels,
unfundable buys and oversize sells, rejected-order atomicity, duplicate order/
fill application, exact funding, quantity conservation, zero-size/unsupported
orders, future/invalid views and explicit initial inventory basis. Separate
checks exercise cumulative fee rounding across multiple fills, gross average
basis under partial sales, a flat-position reset, legitimate mark-price changes,
a constant-execution-price zero-fee round trip, spread-only loss and separate
fee attribution. Checked money/inventory overflow and nonintegral atom notionals
fail explicitly without committing a dry-run.

Runtime fixtures verify arrival-time prices/quantities after intervening updates,
same-time causal children, immutable historical depth, immediate partial expiry,
no reuse of consumed levels, unrelated-price updates, absolute same-quantity
replenishment, replacing snapshots with receive timestamp zero, duplicate/stale
observations, end exclusion, pending expiry and gap/horizon/crossed-book halts.
Halts preserve holdings and fees and invalidate terminal equity, including an
ask-horizon failure where a bid is still observed. Full trading journals repeat
byte for byte.

Twelve new Python cases independently validate the CLI and its serialized ledger:
three seeded scenarios each contain **120 absolute observations and 240 scripted
orders**, with arrival delays, ties, duplicate IDs, funding/position constraints,
partial/empty results, horizon filtering and three grid/fee configurations.
An independent event list, integer quote/fee helpers and `Fraction` ledger check
every outcome/fill, sequential fill IDs, cash, inventory, fees, exact gross
realized/unrealized P&L, basis and equity reconciliation. Additional cases cover
three invalid-data halts, very large 128-bit notionals/fees and five malformed/
unrepresentable/overflowing money configurations. The very-large fee case checks
the exact multiplication/division fallback against Python rather than relying
on overflowing intermediates or floating point.

## Recorded-data smoke run

All four artifact hashes in the existing qualified **2026-09-14** WP1 bundle
were rechecked. Raw recordings and the prepared bundle were read without changes.
The final Release executable ran a short BTCUSDT script twice, starting at UTC
midnight and ending at **00:04:21.541142176 UTC**, with 10,000 USDT, zero BTC,
eight quote decimals and the explicit 0.001 benchmark fee rate.

| Action | Execution time (UTC) | Quantity | Execution price | Quote fee |
| --- | --- | --- | --- | --- |
| Buy | 00:02:21.541142176 | 100 lots = 0.001 BTC | 76,860.01 USDT/BTC | 0.07686001 USDT |
| Sell | 00:03:21.541142176 | 100 lots = 0.001 BTC | 76,846.00 USDT/BTC | 0.07684600 USDT |

Both requests fully filled. Gross realized P&L was **−0.01401000 USDT**; fees were
**0.15370601 USDT**. Terminal cash/equity was **9,999.83228399 USDT**, with zero
inventory/basis. Python's WP1 exact quote/fee helpers independently reconciled
the fills and terminal ledger. Both runs ended normally, dispatched **2,620
runtime events**, and emitted byte-identical **2,902,427-byte** journals.

This is a short integration smoke test, not a full-day strategy evaluation or
evidence of profitability. The independent fixtures provide stronger event-by-
event accounting checks. WP2's existing reconstruction/reference tests remain in
the regression target; WP3 does not reclassify the archive or silently qualify
new days. The [machine-readable record](wp3-validation.json) contains source,
binary, input and output hashes plus the exact real-data script and balances.
Local audit code and full outputs are under ignored `build/wp3/`.

## Reproduce

```sh
cmake -S . -B build/wp3-debug -DCMAKE_BUILD_TYPE=Debug \
  -DCOUNTERBACK_PYTHON_TESTS=ON -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build/wp3-debug --parallel
ctest --test-dir build/wp3-debug --output-on-failure
env COUNTERBACK_REPLAY_BINARY=build/wp3-debug/engine/counterback_replay \
  COUNTERBACK_RUNTIME_BINARY=build/wp3-debug/engine/counterback_runtime \
  COUNTERBACK_TRADING_BINARY=build/wp3-debug/engine/counterback_market_orders \
  .venv/bin/pytest -m 'not live' --cov=depth_recorder --cov=counterback_replay \
  --cov-report=term-missing --cov-fail-under=80
.venv/bin/ruff check .
.venv/bin/mypy src/depth_recorder src/counterback_replay
```

Use `build/wp3-release` with `-DCMAKE_BUILD_TYPE=Release` for the Release variant.
Use the sanitizer flags from [the C++ guide](cpp-engine.md#validation) and
`COUNTERBACK_PYTHON_TESTS=OFF` for the four-target C++ sanitizer check.

WP3 remains historical, market-order-only and single-instrument. The synthetic
matcher retains its existing fixture API without ledger integration. Historical
limits/cancels, reservations, delayed strategy account knowledge and stochastic
latency remain WP4; Python strategies/configured run bundles, multi-day trading,
reports and the external-engine comparison remain later packages. Remote CI,
Linux execution, live recorder health and full-history strategy performance were
not established by this local validation.
