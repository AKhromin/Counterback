# counterback

**A backtester where the market can answer back.** An execution-realistic backtesting engine, extended to run strategies against order books that react to them.

> **Status: skeleton (September 2026).** The design is fixed; the code is being built. v1 ships on 30 November 2026. Nothing in this repository is a result yet, and the README says so on purpose: the question, the assumptions and the limitations are written down before the first number exists, so that the numbers cannot quietly reshape them later.

---

## The question

Standard backtests make two silent assumptions: that orders fill at the price on the screen at no cost, and that your own orders do not move the market. Both are false, and both flatter results.

**By how much does the no-market-impact assumption inflate backtested performance, and how does that inflation scale with order size?**

Three hypotheses, stated in advance:

- **H1.** Frictionless replay overstates the net return of high-turnover strategies, and the overstatement grows as the fill model gets cruder.
- **H2.** The divergence between results in a replayed market and results in a market that reacts to the strategy's orders grows with order size, roughly as the square root of size.
- **H3.** Below some order size the divergence is indistinguishable from noise. That threshold, with a confidence interval, is a result in itself.

If H2 fails and the divergence is negligible at realistic sizes, that is reported as a finding, not buried as a failure. The experiment is designed so that a null answer is still an answer.

## Why it matters

The size of the error is not academic. A one-way cost of 5 basis points on a strategy that trades twice a day removes roughly 25 percentage points of annual return. Across 215 systematic strategies promoted by banks, the median Sharpe ratio fell from 1.20 in backtest to 0.31 live, a 73% deterioration (Suhonen, Lennkh and Perez, 2017). The people most exposed are retail algorithmic traders using open-source backtesters that are frictionless by default; the people who would most like a public, reproducible method are academic researchers and the quant desks who currently keep their own answers private.

## What this is, and what it is not

Existing open-source engines already model commissions and slippage, and one research simulator (ABIDES) provides a market that reacts to the agent through hand-coded participants. The gap this project fills is narrower and specific:

> No widely used open-source backtester couples a market that reacts endogenously to the agent's own orders with a **learned** generative model of order flow, inside an engine that models execution realistically.

So this repository is:

- an **event-driven backtesting engine** with a full limit order book, price-time matching, a latency model, and fill and cost models at selectable fidelity;
- a **replay mode**, the validated baseline, where the recorded market cannot react;
- a **counterfactual mode**, where the market responds to the strategy's orders, using a learned generator where one is available and a mechanistic reactive baseline where it is not;
- a **divergence experiment** that measures the gap between the two as a function of order size.

It is not a trading system, it will never connect to a broker, and it is not a new generative model. Where it uses generated order books it consumes and measures generators built by others, and cites them.

## Design

- **Language split.** Python API for strategies, configuration, experiments and analysis; a C++ core, exposed through pybind11, for the matching engine, the event loop and anything called once per market message.
- **Strategy interface.** A strategy is a plain Python class implementing `on_event(book, trades, clock, portfolio) -> orders`. Market, limit and cancel orders in v1.
- **Fill models**, from crude to faithful: mid-price plus half the spread; a cap on participation in traded volume; full queue-position simulation against the reconstructed book. Comparing results across the three is itself one of the experiments.
- **Cost models.** Commission schedules, spread, and market impact as a fixed cost, as the square-root law, and optionally as a decaying propagator.
- **Determinism.** A fixed seed and fixed inputs produce bit-identical output. This is what makes a stochastic system debuggable.
- **Reports.** Each run emits a self-contained HTML report: equity curve, drawdown, cost attribution, fill diagnostics, and for counterfactual runs the divergence chart and a stylised-facts scorecard.

## Data

- **Primary: BTC/USDT on Binance spot.** Full-depth order-book updates are recorded continuously from September 2026 by two independent recorders in different cloud regions, because historical depth is not available at the free tier and cannot be obtained retroactively. The recorder is a separate small project. The recordings themselves are **not** in this repository.
- **Validation: LOBSTER samples.** Level-3 NASDAQ order-book data for a handful of large-cap tickers, used to validate replay-mode correctness and to benchmark the stylised-facts battery against the academic literature. LOBSTER data is under an academic licence and is **not** redistributed here; the code to consume it is.
- **Not used.** FX (no central order book to study), options and fixed income (data cost and pricing machinery out of scope).

## Assumptions and limitations, stated before any result

1. **There is no counterfactual ground truth.** A market's reaction to an order that was never sent cannot be observed. The reactive mode produces model-implied responses; their realism is tested indirectly, through stylised facts and consistency with the empirical impact literature, never directly.
2. **Binance depth is Level 2, not Level 3.** Aggregated quantity per price, without individual orders. Full queue-position simulation can therefore only be validated on the LOBSTER equity samples; on crypto it is an estimate.
3. **One instrument at a time.** No cross-impact between instruments, no portfolio effects.
4. **The participant mix is baked in.** A generator trained on one venue and period implicitly fixes who is trading; regime changes and other algorithms' reactions to this strategy are not modelled.
5. **Hidden liquidity is under-observed.** Iceberg reserves and hidden executions bias fills computed from visible depth, and bias any generator trained on visible books.
6. **Latency is synthetic.** A stochastic delay model, not measured network behaviour.
7. **Market structure is simplified.** No opening and closing auctions, halts, fee tiers or rebates. Conclusions drawn on crypto carry an external-validity caveat when applied to equities.
8. **Impact calibration has a ceiling.** Public data allows only indirect calibration; the proprietary metaorder records used in the literature are not available here.
9. **Overfitting is untouched.** This engine fixes frictions, not statistics. A better execution model does nothing for a strategy that was selected from a thousand variants. Results will report the Deflated Sharpe Ratio alongside the raw Sharpe to make that visible.

## How it will be validated

- **Level 1, engine correctness.** Accounting invariants (a zero-cost round trip yields exactly zero profit; cash plus inventory value is conserved), property-based tests, deterministic replay, and a cross-check against NautilusTrader on a market-order-only scenario where both engines must agree, with any divergence explained in writing.
- **Level 2, cost realism.** Modelled impact for synthetic metaorders must track the square-root law; cost magnitudes are compared with published transaction-cost figures; every cost parameter is swept across its plausible range, and any strategy whose sign flips is reported as fragile.
- **Level 3, market realism and the claim.** A stylised-facts battery run on generated and real data side by side, with failures reported rather than hidden; a no-manipulation test (a round-trip pump-and-dump strategy must not profit in the simulator, per Gatheral's no-dynamic-arbitrage condition); and the divergence experiment over thirty or more seeds against a zero-size noise floor.

## Roadmap

| Milestone | Date | Contents |
|---|---|---|
| Skeleton public | September 2026 | This README; repository layout; CI; the depth recorder running |
| **v1** | **30 November 2026** | Working engine; transaction-cost and slippage model; one reference strategy (intraday mean reversion); unit tests; results write-up |
| v2 | February 2027 | Impact models; reactive market mode; second strategy (monthly momentum as a cost-insensitive control); profiling of the C++ core |
| Experiments | March to April 2027 | The divergence experiment; the stylised-facts scorecard; the written answer to the question above |

Deferred beyond this year: a reinforcement-learning execution agent, a generator conditioned on the agent's own order flow, multi-asset and cross-impact. Never: live trading.

## Repository layout

```
engine/        C++ core: order book, matching engine, event loop (pybind11 bindings)
pyengine/      Python API: strategies, fill and cost models, market modes, reports
strategies/    reference strategies
experiments/   one reproducible script and config per figure in the write-up
data/          loaders and pointers to sources; no raw market data is committed
tests/         unit, property-based and golden-master tests
docs/          design notes and the results write-up
```

## Getting started

Build and run instructions arrive with v1. Until then the repository is a skeleton with tests for the parts that exist.

## Reproducibility

Every figure in the eventual write-up will be reproducible with one command from a config file in `experiments/`, under a pinned environment and a fixed seed. Results will report confidence intervals, and the stylised facts the simulator fails as well as the ones it passes.

## Reading that shaped the design

Harris, *Trading and Exchanges*; Almgren and Chriss (2000) on optimal execution; Kyle (1985); Gatheral (2010) on no-dynamic-arbitrage and market impact; Cont (2001) on stylised facts; Bailey and Lopez de Prado on the Deflated Sharpe Ratio; Harvey and Liu on backtesting haircuts; Byrd, Hybinette and Balch on ABIDES; Vyetrenko et al. on realism metrics for order-book simulators; Nagy et al. (2023), Xiao et al. (2025) and Wang and Ventre (2025, 2026) on generative models of limit order books.

## Licence and author

Apache-2.0. Built by Alexey Khromin, MSci Computer Science, King's College London, as a personal project. Issues are welcome; pull requests are not accepted until v1 has shipped.
