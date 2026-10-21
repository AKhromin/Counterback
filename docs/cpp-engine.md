# C++ matching engine

The first engine slice is a standalone C++17 library for **synthetic individual
orders**, with a runnable example and offline tests. It has no third-party C++
dependencies. A separate historical L2 replay library is described in
[historical replay](historical-replay.md). Python bindings, strategy callbacks,
fees, portfolio accounting and reactive markets remain future work.

## Build and run

Install a C++17 compiler and CMake 3.20 or newer. From the repository root:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel
ctest --test-dir build --output-on-failure
./build/engine/counterback_demo
```

The demo submits asks for 3 lots at 100 ticks and 4 lots at 101 ticks, then a
5-lot market buy. Expected output:

```text
maker=1 taker=3 price_ticks=100 quantity_lots=3
maker=2 taker=3 price_ticks=101 quantity_lots=2
executed_lots=5 unfilled_lots=0 open_orders=1
```

Link CMake consumers against `counterback::engine` and include
`counterback/matching_engine.hpp`. Configure with `-DBUILD_TESTING=OFF` to omit tests.

## Contract

- Each `MatchingEngine` owns one instrument and is single-threaded. It cannot be
  copied or moved because its order index holds iterators into its queues.
- Prices and quantities are signed 64-bit integer **ticks and lots**, never
  floating point. Callers choose the instrument's tick/lot sizes and must reject
  decimal values that cannot be represented exactly. The historical adapter
  provides exact conversion for its separate L2 input. A price of 100 in the
  matching demo means 100 ticks, not 100 USDT.
- IDs are nonzero unsigned 64-bit integers, unique for the entire engine
  lifetime, including after filling, cancellation or expiry. Invalid submissions
  throw `std::invalid_argument` before changing state and do not reserve an ID.
  The used-ID set grows with accepted submissions; create a new engine per run.
- Limit orders require positive prices and quantities. They match better prices
  first, FIFO at equal prices, at the **maker's price**. Unfilled quantities rest
  until cancelled (good till cancelled). Partial fills preserve maker priority.
- Market orders require a zero price and positive quantity. They sweep available
  opposing liquidity; any remainder expires immediately and never rests. A
  partial market fill has `Expired` status, with both executed and remaining
  quantities reported explicitly. Fully filled orders have `Filled` status.
- `submit` returns trades in execution order, maker/taker IDs, aggressor side,
  executed quantity and unfilled quantity. No wall-clock time or randomness is
  used; call order determines priority and identical inputs produce identical
  field values. There is no binary serialization format yet.
- `cancel` removes the entire resting remainder, returning false for an unknown
  or terminal ID. `find` and `orders` return snapshots, not mutable book access.
  `orders(side)` returns best price first and FIFO within a level.
- No amendments, self-trade prevention, auctions, hidden orders, risk checks,
  notional arithmetic, latency or fees are modelled. Crosses between synthetic
  orders always execute. Allocation failures are fatal to a run: discard the
  engine rather than continuing after an allocation exception during mutation.

Price levels use ordered maps and FIFO lists. An ID index locates resting orders
without scanning queues. Insertion and final price-level removal take logarithmic
time in the number of price levels; matching also scales with makers consumed.

## Validation

CTest covers both sides, price priority, FIFO, maker-price execution, partial
fills, limit remainders, market expiry, empty books, cancellation of queue members
and price levels, invalid-input atomicity, ID reuse rejection and integer bounds.
A fixed-seed 10,000-event stream is compared after every event with an independent
flat-vector reference matcher. Reports, remaining books, FIFO order, best prices,
quantity conservation and uncrossed-book invariants are checked. Two engine
instances consume that stream to check repeatability. Checks stay enabled in
Release builds. CI builds Debug and Release on Linux and macOS.

For local memory and undefined-behaviour checks with Clang or GCC:

```sh
cmake -S . -B build/sanitized -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer" \
  -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=address,undefined"
cmake --build build/sanitized --parallel
ctest --test-dir build/sanitized --output-on-failure
```

## Boundary with Binance L2

**Do not translate Binance depth deltas into submissions to this matcher.** The
recorder stores aggregated absolute quantities at prices, without individual
order IDs, arrival times or known queue positions. A size reduction does not say
whether an order executed or was cancelled. This matcher establishes correctness
for synthetic order-level flow; it does not establish historical fill realism.

The Python `HorizonBook` remains the correctness reference for the separate
`counterback::historical` library and offline adapter. The adapter provides exact
decimal-to-tick/lot conversion, snapshot/delta sequencing, gap handling and explicit
horizon validity. See [historical replay](historical-replay.md) for the input
contract, reference compatibility limitation and real-data validation.
Simulated strategy execution against that
state needs an explicit liquidity-consumption/fill model; passive FIFO fills
cannot be inferred from these depth recordings alone.

No historical dataset is bundled. Existing recordings, deployed recorder behavior
and matching-engine implementation are untouched.
