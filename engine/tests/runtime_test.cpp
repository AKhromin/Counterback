#include "counterback/backtest_engine.hpp"

#include <iostream>
#include <sstream>

using namespace counterback;
namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template <typename Fn> void rejects(Fn fn) {
    try { fn(); } catch (const std::exception&) { return; }
    throw std::runtime_error("expected exception");
}
std::string tape(const std::string& rows, unsigned count,
                 const std::string& snapshots = "S 100 150 a r 2 2 100 2 99 1 101 3 102 1\n") {
    return "COUNTERBACK_L2 1 BTCUSDT 0.01 0.00001\n" + snapshots + "BEGIN\n" + rows +
           "END " + std::to_string(count) + "\n";
}
std::string depth(unsigned first, unsigned last, unsigned rx, const std::string& levels = "0 0") {
    return "D " + std::to_string(first) + " " + std::to_string(last) + " " + std::to_string(rx) +
           " 1 7 a r " + levels + "\n";
}
void queue_cases() {
    SimulationClock clock;
    clock.advance_to(100);
    clock.advance_to(100);
    rejects([&] { clock.advance_to(99); });
    const auto max = std::numeric_limits<Timestamp>::max();
    check(delayed_time_ns(max - 1, 1) == max, "maximum timestamp");
    rejects([&] { delayed_time_ns(max, 1); });
    const ScheduleKey parent{100, 0, EventPriority::Strategy, 1};
    check(schedule_key(100, EventPriority::OrderArrival, 2, parent).causal_round == 1,
          "zero delay orders follow callback");
    check(schedule_key(100, EventPriority::Valuation, 2, parent).causal_round == 0,
          "later child priority stays in round");
    rejects([&] { schedule_key(99, EventPriority::OrderArrival, 2, parent); });
    rejects([&] { schedule_key(100, EventPriority::OrderArrival, 1, parent); });
    rejects([&] { schedule_key(100, static_cast<EventPriority>(7), 2); });
    rejects([&] { schedule_key(100, EventPriority::Market, 2,
                              ScheduleKey{100, max, EventPriority::Strategy, 1}); });
    EventQueue queue;
    queue.push(100, EventPriority::CancelArrival, CancelRequest{1});
    queue.push(100, EventPriority::OrderArrival, Order{1, OrderSide::Buy, OrderType::Market, 0, 1});
    check(queue.pop_next().key.priority == EventPriority::OrderArrival, "order wins cancellation tie");
    check(queue.pop_next().key.priority == EventPriority::CancelArrival, "cancellation second");
    queue.push(100, EventPriority::OrderArrival, Order{2, OrderSide::Buy, OrderType::Market, 0, 1});
    auto child = queue.pop_next();
    check(child.key.causal_round == 1 && child.parent_id == 1, "causal parent IDs");
    rejects([&] { queue.push(99, EventPriority::Notification, EndEvent{}); });
    rejects([&] { queue.push_market({1, 100, 0, CancelRequest{1}}); });
    rejects([&] { queue.pop_next(); });
}
void visibility_cases() {
    // Duplicate and stale receive timestamps must not move the market availability clock.
    std::istringstream input(tape(depth(99, 102, 100) + depth(99, 102, 9999) +
                                 depth(100, 101, 99999) + depth(103, 103, 120, "1 0 100 7"), 4));
    ReplayMarket market(input);
    check(!market.view().best_bid() && !market.view().quantity_at(OrderSide::Buy, 100),
          "loaded header is not visible depth");
    auto first = *market.next_event();
    check(first.available_at_ns == 150 && first.snapshot_receive_ns == 150, "delayed bridge gate");
    check(!market.view().best_bid(), "lookahead does not publish pending book");
    rejects([&] { market.next_event(); });
    rejects([&] { market.advance(first, 149); });
    market.advance(first, 150);
    const auto retained = market.view();
    check(retained.best_bid()->quantity == 2, "first published state");
    check(retained.quantity_at(OrderSide::Buy, 98) == std::nullopt, "unknown outer depth");
    check(retained.quantity_at(OrderSide::Buy, 101) == 0, "known absent level");
    auto second = *market.next_event();
    check(second.available_at_ns == 150 && second.source_id == 4, "ignored observations create no tick");
    check(market.view().best_bid()->quantity == 2, "lookahead retains committed view");
    market.advance(second, 150);
    check(market.view().best_bid()->quantity == 7 && retained.best_bid()->quantity == 2,
          "retained views are immutable");
    check(!market.next_event(), "stream ended");
    check(market.statistics().duplicates == 1 && market.statistics().stale == 1, "reference ignored counts");
    rejects([&] { market.submit({1, OrderSide::Buy, OrderType::Market, 0, 1}, 150); });
    rejects([&] { market.cancel(1, 150); });
}
void timing_and_repeatability() {
    const auto data = tape(depth(101, 101, 100) + depth(102, 102, 120, "1 0 100 5") +
                           depth(103, 103, 200), 3);
    auto run = [&] {
        std::istringstream input(data);
        ReplayMarket market(input);
        std::ostringstream journal;
        BacktestEngine engine(market, {0, 250, 0}, [&](const JournalRecord& row) {
            write_journal_record(journal, row);
        });
        engine.schedule(149, EventPriority::Valuation, [](BacktestEngine& e, const MarketView& v) {
            check(e.now() == 149 && !v.best_bid(), "timer cannot see future snapshot");
        });
        engine.schedule(150, EventPriority::OrderArrival, [](BacktestEngine&, const MarketView& v) {
            check(v.last_update_id() == 102 && v.best_bid()->quantity == 5,
                  "all timestamp-tied source events precede arrivals");
        });
        auto result = engine.run([](BacktestEngine& e, const MarketView& v) {
            check(e.ready() && v.visible_at_ns() <= e.now(), "causal strategy view");
        });
        check(!result.halted && result.ever_ready && result.applied_updates == 3 &&
              result.strategy_callbacks == 3 && result.stopped_at_ns == 250, "completed runtime");
        rejects([&] { engine.run(); });
        rejects([&] { engine.schedule(251, EventPriority::Strategy, [](auto&, const auto&) {}); });
        return journal.str();
    };
    check(run() == run(), "identical tape/configuration journals are byte identical");
}
void invalid_and_warmup_cases() {
    for (const auto& invalid : {depth(105, 105, 200),
                              depth(102, 102, 200, "2 0 100 0 99 0"),
                              depth(102, 102, 200, "0 1 100 5")}) {
        std::istringstream input(tape(depth(101, 101, 100) + invalid + depth(106, 106, 300), 3));
        ReplayMarket market(input);
        BacktestEngine engine(market, {0, 400, 0});
        auto result = engine.run();
        check(result.halted && result.stopped_at_ns == 200 && !engine.ready(),
              "gap/horizon loss/crossed book halt active runtime");
    }
    // The baseline may bootstrap through unavailable depth, then starts at the first trusted anchor.
    std::istringstream input(tape(depth(90, 90, 50) + depth(101, 101, 100) +
                                 depth(102, 102, 200), 3));
    ReplayMarket market(input);
    BacktestEngine engine(market, {175, 300, 2});
    unsigned callbacks = 0;
    auto result = engine.run([&](BacktestEngine& e, const MarketView&) {
        ++callbacks;
        check(e.ready() == (e.now() == 200), "trusted-update warmup and interval start");
    });
    check(!result.halted && result.ever_ready && callbacks == 2, "bootstrap and warmup complete");

    // End is exclusive, including a new bridge that would only become available at end.
    std::istringstream late(tape(depth(101, 101, 100), 1));
    ReplayMarket pending(late);
    BacktestEngine short_run(pending, {0, 150, 0});
    auto stopped = short_run.run();
    check(!stopped.ever_ready && stopped.applied_updates == 0 && !pending.view().best_bid(),
          "end excludes future snapshot and its update");

    // Activation at start is explicit even if the next source event is later and invalid.
    std::istringstream gap(tape(depth(101, 101, 100) + depth(105, 105, 200), 2));
    ReplayMarket gap_market(gap);
    BacktestEngine starts_between(gap_market, {175, 300, 0});
    check(starts_between.run().halted, "segment active between source ticks");

    std::istringstream at_start(tape(depth(101, 101, 100) + depth(105, 105, 200), 2));
    ReplayMarket at_start_market(at_start);
    BacktestEngine starts_invalid(at_start_market, {200, 300, 0});
    const auto never_started = starts_invalid.run();
    check(!never_started.halted && !never_started.ever_ready,
          "start-time market update precedes first activation");

    // A gap covered by a compatible new bridge is not an unbridged trust loss.
    const auto bridges = "S 100 150 a r 2 2 100 2 99 1 101 3 102 1\n"
                         "S 110 250 a r 2 2 100 9 99 1 101 3 102 1\n";
    std::istringstream bridged(tape(depth(101, 101, 100) + depth(111, 111, 200), 2, bridges));
    ReplayMarket bridged_market(bridged);
    BacktestEngine delayed_bridge(bridged_market, {0, 300, 0});
    delayed_bridge.schedule(225, EventPriority::Valuation, [](BacktestEngine&, const MarketView& v) {
        check(v.last_update_id() == 101 && v.best_bid()->quantity == 2,
              "late replacement snapshot stays hidden between updates");
    });
    check(!delayed_bridge.run().halted && bridged_market.view().last_update_id() == 111 &&
          bridged_market.view().visible_at_ns() == 250, "compatible delayed reanchor preserves active run");
}
void synthetic_cases() {
    SyntheticMarket market({"TEST", "1", "1"}, {
        {10, Order{1, OrderSide::Buy, OrderType::Limit, 99, 3}},
        {10, Order{2, OrderSide::Sell, OrderType::Limit, 101, 4}},
        {20, Order{3, OrderSide::Sell, OrderType::Limit, 100, 2}}
    });
    std::vector<JournalRecord> rows;
    BacktestEngine engine(market, {0, 50, 0}, [&](const auto& row) { rows.push_back(row); });
    bool submitted = false;
    engine.run([&](BacktestEngine& e, const MarketView& v) {
        if (submitted) return;
        submitted = true;
        check(v.best_ask()->price == 101, "two-sided synthetic market at first callback");
        // Submit at 20: intervening exogenous ask 100 must be used before arrival.
        e.submit_after({10, OrderSide::Buy, OrderType::Market, 0, 3}, 10);
        e.cancel_after(10, 10);
        e.schedule(25, EventPriority::Notification, [](BacktestEngine& current, const MarketView&) {
            check(current.now() == 25, "fixed notification delay");
        });
        // Exactly at end cannot execute, and beyond-end work stays pending.
        e.submit_after({11, OrderSide::Buy, OrderType::Market, 0, 1}, 40);
        e.submit_after({12, OrderSide::Buy, OrderType::Market, 0, 1}, 41);
    });
    bool executed = false, cancelled_after = false, expired = false;
    for (const auto& row : rows) {
        if (row.execution) {
            check(row.execution->order_id == 10 && row.execution->executed == 3, "only eligible order executes");
            check(row.execution->trades.size() == 2 && row.execution->trades[0].price == 100 &&
                  row.execution->trades[1].price == 101, "arrival-time price and maker matching");
            executed = true;
        }
        if (row.status == "cancel_miss") { check(executed, "fill precedes same-time cancel"); cancelled_after = true; }
        if (row.status == "expired_at_end") expired = true;
    }
    check(executed && cancelled_after && expired && engine.result().pending_events == 1,
          "synthetic arrival/cancel/end behavior");

    SyntheticMarket zero({"TEST", "1", "1"}, {
        {10, Order{1, OrderSide::Buy, OrderType::Limit, 99, 1}},
        {10, Order{2, OrderSide::Sell, OrderType::Limit, 101, 1}}
    });
    bool once = false, child_seen = false;
    BacktestEngine zero_engine(zero, {0, 20, 0}, [&](const JournalRecord& row) {
        if (row.execution) {
            check(row.event.key.causal_round == 1 && row.event.parent_id.has_value(), "zero-delay causal round");
            child_seen = true;
        }
    });
    zero_engine.run([&](BacktestEngine& e, const MarketView&) {
        if (!once) { once = true; e.submit_after({3, OrderSide::Buy, OrderType::Market, 0, 1}); }
    });
    check(child_seen, "zero-delay action dispatched");
    check(zero.view().quantity_at(OrderSide::Sell, 101) == 0,
          "known empty synthetic side differs from unknown historical depth");
}
} // namespace
int main() {
    try {
        queue_cases(); visibility_cases(); timing_and_repeatability();
        invalid_and_warmup_cases(); synthetic_cases();
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
    std::cout << "deterministic runtime tests passed\n";
}
