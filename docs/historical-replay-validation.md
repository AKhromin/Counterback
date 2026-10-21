# Milestone 2 validation — 21 October 2026

One real Binance Spot BTCUSDT UTC day, **14 September 2026**, was restored from
the existing recorder-a / recorder-b directories in the old `Backtesting Engine`
checkout. Those source files were read without modification. A new verification
report was written under `build/historical-validation/verification/`; the previous
passing report was preserved. No large data or exported depth is tracked in Git.

The fresh verifier passed after checking final manifests and checksums. Joint
update-ID coverage was 100%; all 24 UTC hours were present with boundary evidence.
It performed 607,547 trusted bookTicker comparisons with zero mismatches and
reported no structural errors or joint sequence gaps. Both captured exchangeInfo
files agreed on tick size **0.01000000 USDT** and lot size **0.00001000 BTC**.

The offline export and C++ replay produced these measured results:

| Measurement | Result |
| --- | ---: |
| Captured depth records across both sources | 1,727,164 |
| Depth updates applied | 862,298 |
| Duplicate records ignored | 863,555 |
| Unique records skipped before an anchor | 1,311 |
| Stale records | 0 |
| Available snapshots / loaded bridge segments | 198 / 101 |
| Joint sequence gaps / structural errors | 0 / 0 |
| Last applied update ID | 100126273281 |
| Trusted update-ID coverage | 99.85761010045807% |
| Final best bid | 78,189.20 USDT, 0.67886 BTC |
| Final best ask | 78,189.21 USDT, 2.51657 BTC |
| Final spread | 0.01 USDT (1 tick) |
| Final observed bid / ask levels | 5,145 / 5,055 |

C++ and the unchanged Python HorizonBook agreed on **172 full-depth checkpoints**
(every 10,000 original records) plus the final state. Equality includes all
observed level prices/quantities, best bid/ask, spread, boundaries, per-side trust,
last applied IDs, and aggregate applied/duplicate counts, trusted/invalid
intervals, gaps and unseen removals. The Python oracle's summary and trusted
coverage also matched the fresh verifier report. Output from the C++ CLI was
checked as structured JSON, not by visual inspection of its last line.

The trusted interval is `[100071596661, 100126273281]`. The initial interval
`[100071518696, 100071596660]` remains invalid/unanchored. No snapshot backfills
that prefix. Known levels outside a snapshot's original horizon do not imply
that intervening outer depth is known. The verifier's source-list snapshot order
is deliberately retained; see [the compatibility limitation](historical-replay.md#reference-compatibility-limitation).
No claim is made of full-depth equality at every real event between checkpoints.

Local regression results on macOS arm64, Apple Clang 21, Python 3.11.0:

- Debug CTest: 3/3 passed (matching engine, historical book, Python/C++ oracle).
- Release CTest: 3/3 passed.
- AddressSanitizer + UndefinedBehaviorSanitizer CTest: 3/3 passed.
- Complete offline Python suite: 86 passed, 2 live Binance tests deselected;
  recorder coverage including branches: 82.83% (80% threshold passed).
- Ruff and strict mypy checks passed for both Python packages.
- Synthetic state comparison covers every event, including a fixed-seed
  2,200-record test and a compressed two-recorder integration fixture.

The Python suite initially encountered the sandbox's localhost bind restriction.
It passed in full when rerun with permission to bind the local WebSocket test
server. No exchange-facing tests were run. Linux and Python 3.12 are configured
in CI and are not represented as locally verified here.

[The machine-readable validation record](historical-replay-validation.json)
contains input manifest/metadata hashes, exported file hashes, measured summary
fields and test results. Full local evidence remains in ignored
`build/historical-validation/`: `day/evidence.json`, `day/events.l2`,
`day/expected.ndjson`, `day/actual.ndjson`, `comparison.json`, and the new verifier
report. The integer event file is about 895 MB; the expected checkpoints are about
24 MB. This is a historical market-state simulator, without strategy fills or
backtest P&L.
