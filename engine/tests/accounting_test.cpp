#include "counterback/backtest_engine.hpp"

#include <iostream>
#include <sstream>

using namespace counterback;
namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template <typename Error = std::exception, typename Fn> void rejects(Fn fn) {
    try { fn(); } catch (const Error&) { return; }
    throw std::runtime_error("expected exception");
}
const Instrument instrument{"BTCUSDT", "1", "1"};
AccountConfig config(Money cash = 10000, std::string fee = "0.001") {
    AccountConfig result;
    result.cash_atoms = cash; result.quote_decimals = 0; result.fee_rate = std::move(fee);
    return result;
}
Order buy(OrderId id, Quantity q) { return {id, OrderSide::Buy, OrderType::Market, 0, q}; }
Order sell(OrderId id, Quantity q) { return {id, OrderSide::Sell, OrderType::Market, 0, q}; }
MarketView view() {
    return MarketView({instrument, 10, 101, MarketValidity::Trusted, 99, 102, true, true,
                       {{100, 5}, {99, 2}, {98, 100}}, {{101, 2}, {102, 3}, {103, 100}}});
}
ExecutionOutcome one(OrderId id, OrderSide side, Price price, Quantity q, Money fee = 0) {
    return {{id, side, OrderType::Market, 0, q}, 10, ExecutionStatus::Filled, "filled", q, 0,
             {{id, id, 10, side, price, q, checked_mul(price, q), fee, "BTCUSDT"}}};
}
void money_cases() {
    const auto max = parse_money("170141183460469231731687303715884105727");
    check(money_string(max) == "170141183460469231731687303715884105727", "128-bit round trip");
    check(money_string(checked_sub(checked_sub(0, max), 1)) == "-170141183460469231731687303715884105728",
          "minimum signed integer printing");
    rejects<std::overflow_error>([&] { checked_add(max, 1); });
    rejects<std::overflow_error>([&] { checked_mul(max, 2); });
    rejects<std::overflow_error>([&] { parse_money("170141183460469231731687303715884105728"); });
    rejects<std::invalid_argument>([] { parse_money("-1"); });
    check(MoneyGrid({"BTCUSDT", "0.01", "0.00001"}).notional(10000, 20000) == 2000000000,
          "WP1 notional example");
    check(MoneyGrid({"T", "1e-9", "1"}).notional(10, 1) == 1, "fractional grid exact notional");
    rejects<std::invalid_argument>([] { MoneyGrid({"T", "0.000000001", "0.000000001"}).notional(1, 1); });
    rejects<std::overflow_error>([] { MoneyGrid({"T", "1", "1"}, 18).notional(INT64_MAX, INT64_MAX); });
    for (const auto* rate : {"NaN", "inf", "1.1", "-0.001", "1e", "."}) {
        rejects([&] { FeeModel{rate}; });
    }
    check(FeeModel("0.001").cumulative_fee(2000000000) == 2000000, "WP1 fee example");
    check(FeeModel("0.999").cumulative_fee(max) == max - max / 1000, "large multiply/divide fee");
    check(FeeModel("1").cumulative_fee(max) == max, "maximum fee");
    check(FeeModel("0").cumulative_fee(max) == 0, "zero fee");
    check(Exact::decimal("1.2500e-2") == Exact(1, 80), "exact decimal exponent");
    check(Exact(-2, 3) + Exact(5, 6) == Exact(1, 6), "signed rational addition");
    check(Exact(3, 4) / Exact(-9, 2) == Exact(-1, 6), "signed rational division");
    rejects([] { Exact(1, 0); });
    rejects([] { (void)(Exact(1) / Exact(0)); });
}
void budget_cases() {
    LiquidityBudget budget;
    const FillRecord fill{1, 1, 10, OrderSide::Buy, 101, 2, 202, 0, "BTCUSDT"};
    budget.consume(fill, {101, 2});
    check(budget.available(OrderSide::Sell, {101, 2}) == 0, "consumed once");
    rejects<BookDataError>([&] { budget.consume(fill, {101, 2}); });
    budget.observe({"BTCUSDT", 102, 102, {{101, 9}}, {{102, 9}}, {}}, false);
    check(budget.available(OrderSide::Sell, {101, 2}) == 0, "unrelated observations do not replenish");
    budget.observe({"BTCUSDT", 103, 103, {}, {{101, 2}}, {}}, false);
    check(budget.available(OrderSide::Sell, {101, 2}) == 2, "same absolute quantity explicitly replenishes");
    budget.consume(fill, {101, 2});
    budget.observe({"BTCUSDT", 110, 110, {}, {}, {}}, true);
    check(budget.available(OrderSide::Sell, {101, 2}) == 2, "fresh snapshot resets all consumption");
}
void walk_and_basis_cases() {
    Portfolio portfolio(instrument, config());
    MarketOrderExecution execution;
    const auto first = execution.execute(buy(1, 7), 10, view(), portfolio);
    check(first.status == ExecutionStatus::Expired && first.executed == 5 && first.remaining == 2,
          "partial market remainder expires at known horizon");
    check(first.fills.size() == 2 && first.fills[0].price == 101 && first.fills[1].price == 102,
          "walk best first, exclude observed outer levels");
    check(first.fills[0].fee_atoms == 1 && first.fills[1].fee_atoms == 0, "fee rounds cumulatively per order");
    check(portfolio.state().cash_atoms == 9491 && portfolio.state().inventory_lots == 5 &&
          portfolio.state().basis_atoms == Exact(508), "buy ledger");
    const auto mark = portfolio.snapshot(view());
    check(mark.equity_atoms == 9991 && mark.unrealized_gross_atoms == Exact(-8), "best bid valuation");
    const auto empty = execution.execute(buy(2, 3), 10, view(), portfolio);
    check(empty.status == ExecutionStatus::Expired && empty.executed == 0 && empty.remaining == 3,
          "same view cannot reuse consumed depth");
    auto sale = execution.execute(sell(3, 3), 10, view(), portfolio);
    check(sale.executed == 3 && sale.fills.front().price == 100, "sell at bid");
    check(portfolio.state().cash_atoms == 9790 && portfolio.state().inventory_lots == 2 &&
          portfolio.state().basis_atoms == Exact(1016, 5) &&
          portfolio.state().realized_gross_atoms == Exact(-24, 5), "partial sale retains exact average basis");
    auto second_sale = execution.execute(sell(4, 2), 10, view(), portfolio);
    check(second_sale.executed == 2 && portfolio.state().inventory_lots == 0 &&
          portfolio.state().basis_atoms == Exact(0) && portfolio.state().cash_atoms == 9989 &&
          portfolio.state().realized_gross_atoms == Exact(-8) && portfolio.state().fees_atoms == 3,
          "flat position resets basis; spread not charged a second time");
    check(execution.execute(buy(1, 1), 10, view(), portfolio).reason == "duplicate_or_invalid_order_id",
          "lifetime duplicate order rejection");
    rejects<std::invalid_argument>([&] { portfolio.apply(first); });
    auto duplicated_fill = one(9, OrderSide::Buy, 101, 1, 1);
    duplicated_fill.fills[0].id = 1;
    rejects<std::invalid_argument>([&] { portfolio.apply(duplicated_fill); });
    auto initial = config(); initial.inventory_lots = 10; initial.initial_basis_atoms = Exact(900);
    Portfolio selling(instrument, initial);
    MarketOrderExecution walk;
    const auto sold = walk.execute(sell(1, 9), 10, view(), selling);
    check(sold.executed == 7 && sold.remaining == 2 && sold.fills.size() == 2 &&
          sold.fills[1].price == 99 && selling.state().cash_atoms == 10697 &&
          selling.state().inventory_lots == 3 && selling.state().basis_atoms == Exact(270) &&
          selling.state().realized_gross_atoms == Exact(68), "hand-calculated sell walk and expired remainder");
}
void transactional_and_rejection_cases() {
    Portfolio portfolio(instrument, config(203));
    MarketOrderExecution execution;
    auto rejected = execution.execute(buy(1, 3), 10, view(), portfolio);
    check(rejected.reason == "insufficient_cash" && rejected.fills.empty() && rejected.executed == 0 &&
          rejected.remaining == 3 && portfolio.state().cash_atoms == 203 && !portfolio.state().inventory_lots,
          "unfunded depth walk rejects before any mutation");
    auto accepted = execution.execute(buy(2, 2), 10, view(), portfolio);
    check(accepted.executed == 2 && accepted.fills.front().id == 1 && portfolio.state().cash_atoms == 0,
          "rejected order consumed neither depth nor fill IDs; exact funding succeeds");
    check(execution.execute(sell(3, 3), 10, view(), portfolio).reason == "insufficient_inventory",
          "oversize sell rejects despite limited executable depth");
    check(execution.execute(sell(4, 2), 10, view(), portfolio).executed == 2, "sell liquidity unchanged after rejection");
    check(execution.execute(buy(5, 0), 10, view(), portfolio).reason == "invalid_order", "zero quantity rejection");
    check(execution.execute({6, OrderSide::Buy, OrderType::Limit, 0, 1}, 10, view(), portfolio).status ==
          ExecutionStatus::Rejected, "unsupported type rejection");
    check(execution.execute(buy(7, 1), 9, view(), portfolio).reason == "market_untrusted", "future view rejected");
    MarketView invalid({instrument, 10, {}, MarketValidity::Invalid, {}, {}, false, false, {}, {}});
    check(execution.execute(buy(8, 1), 10, invalid, portfolio).reason == "market_untrusted", "invalid data cannot fill");
    check(!portfolio.snapshot(invalid).valuation_valid && !portfolio.snapshot(invalid).equity_atoms,
          "invalid terminal valuation has no carried-forward equity");

    auto initial = config(1000, "0");
    initial.inventory_lots = INT64_MAX;
    initial.initial_basis_atoms = Exact(0);
    Portfolio full(instrument, initial);
    MarketOrderExecution overflow;
    rejects<std::overflow_error>([&] { overflow.execute(buy(1, 1), 10, view(), full); });
    check(full.state().cash_atoms == 1000 && full.state().inventory_lots == INT64_MAX,
          "overflow leaves authoritative state untouched");
    overflow.execute(sell(2, 1), 10, view(), full);
    check(overflow.execute(buy(3, 1), 10, view(), full).executed == 1, "overflow left ask budget untouched");

    auto bad = config(); bad.inventory_lots = 1;
    rejects<std::invalid_argument>([&] { Portfolio p(instrument, bad); });
    bad.initial_basis_atoms = Exact(90);
    Portfolio initialized(instrument, bad);
    check(initialized.snapshot(view()).unrealized_gross_atoms == Exact(10), "explicit initial basis");
}
void zero_cost_and_spread_cases() {
    Portfolio constant(instrument, config(10000, "0"));
    constant.apply(one(1, OrderSide::Buy, 100, 3));
    constant.apply(one(2, OrderSide::Sell, 100, 3));
    check(constant.state().cash_atoms == 10000 && constant.state().realized_gross_atoms == Exact(0) &&
          !constant.state().inventory_lots && !constant.state().fees_atoms, "constant price zero fee round trip");
    Portfolio spread(instrument, config(10000, "0"));
    MarketOrderExecution execution;
    execution.execute(buy(1, 2), 10, view(), spread);
    execution.execute(sell(2, 2), 10, view(), spread);
    check(spread.state().cash_atoms == 9998 && spread.state().realized_gross_atoms == Exact(-2),
          "spread-only loss equals two lots times one tick");
    auto holdings = config(); holdings.inventory_lots = 2; holdings.initial_basis_atoms = Exact(200);
    Portfolio changed(instrument, holdings);
    const auto before = changed.snapshot(view());
    MarketView higher({instrument, 20, 102, MarketValidity::Trusted, 99, 103, true, true,
                       {{102, 5}}, {{103, 5}}});
    check(changed.snapshot(higher).equity_atoms == *before.equity_atoms + 4,
          "legitimate market move changes marked equity");
}
std::string tape(const std::string& rows, unsigned count, const std::string& snapshots =
                 "S 100 0 a r 2 2 100 5 99 2 101 2 102 3\n") {
    return "COUNTERBACK_L2 1 BTCUSDT 1 1\n" + snapshots + "BEGIN\n" + rows + "END " + std::to_string(count) + "\n";
}
void runtime_cases() {
    const auto data = tape("D 101 101 10 1 7 a r 0 0\nD 102 102 20 2 7 a r 0 3 101 1 102 0 103 99\n", 2);
    auto run = [&] {
        std::istringstream input(data);
        ReplayMarket market(input);
        std::ostringstream journal;
        RuntimeConfig cfg{0, 40, 0, config()};
        std::vector<ExecutionOutcome> outcomes, terminated;
        BacktestEngine engine(market, cfg, [&](const JournalRecord& row) {
            write_journal_record(journal, row);
            if (row.outcome) outcomes.push_back(*row.outcome);
            terminated.insert(terminated.end(), row.terminated_orders.begin(), row.terminated_orders.end());
        });
        engine.schedule(15, EventPriority::Strategy, [](BacktestEngine& e, const MarketView&) {
            e.submit_after(buy(1, 7), 10);
            e.submit_after(buy(2, 1), 25);
            e.submit_after(buy(3, 1), 26);
        });
        const auto result = engine.run();
        check(outcomes.size() == 2 && outcomes.front().executed == 1 && outcomes.front().remaining == 6 &&
              outcomes.front().fills.front().time_ns == 25 && outcomes.front().fills.front().price == 101,
              "arrival-time updated liquidity and horizon used");
        check(outcomes.back().reason == "expired_at_end" && terminated.size() == 1 &&
              terminated.front().order.id == 3 && result.pending_events == 1, "all pending markets expire at end");
        check(result.account->state.cash_atoms == 9898 && result.account->state.inventory_lots == 1 &&
              result.account->equity_atoms == 9998, "authoritative terminal ledger");
        check(market.view().asks().front().quantity == 1, "strategy does not rewrite recorded depth");
        return journal.str();
    };
    check(run() == run(), "trading journals repeat byte for byte");

    for (const auto& invalid : {"D 105 105 20 2 7 a r 0 0\n", "D 102 102 20 2 7 a r 0 2 101 0 102 0\n",
                               "D 102 102 20 2 7 a r 0 1 100 1\n"}) {
        std::istringstream input(tape("D 101 101 10 1 7 a r 0 0\n" + std::string(invalid), 2));
        ReplayMarket market(input);
        bool terminated = false;
        BacktestEngine engine(market, {0, 40, 0, config()}, [&](const JournalRecord& row) {
            if (!row.terminated_orders.empty()) {
                terminated = row.terminated_orders.front().reason == "expired_on_halt";
                check(row.validity_from == MarketValidity::Trusted, "validity transition journaled");
            }
        });
        engine.schedule(15, EventPriority::Strategy, [](BacktestEngine& e, const MarketView&) {
            e.submit_after(buy(1, 1)); e.submit_after(sell(2, 1), 10);
        });
        auto result = engine.run();
        check(result.halted && result.stopped_at_ns == 20 && terminated && result.account->state.inventory_lots == 1 &&
              !result.account->valuation_valid && !result.account->equity_atoms, "halt preserves holdings and invalidates mark");
    }
}
void replenishment_cases() {
    // A zero-receive-time snapshot still resets *all* levels. Duplicates/stale data and
    // unrelated price updates cannot replenish the consumed ask 101.
    std::istringstream valid(tape(
        "D 99 102 10 1 7 a r 0 0\nD 99 102 11 2 7 b r 0 0\n"
        "D 100 101 12 3 7 a r 0 0\nD 103 103 20 4 7 a r 1 0 100 5\n"
        "D 104 104 30 5 7 a r 0 1 101 2\nD 111 111 40 6 7 a r 0 0\n", 6,
        "S 100 0 a r 2 2 100 5 99 2 101 2 102 3\nS 110 0 a r 2 2 100 5 99 2 101 2 102 3\n"));
    ReplayMarket market(valid);
    std::vector<ExecutionOutcome> outcomes;
    BacktestEngine engine(market, {0, 50, 0, config()}, [&](const JournalRecord& row) {
        if (row.outcome) outcomes.push_back(*row.outcome);
    });
    for (Timestamp time : {15, 25, 35, 45}) engine.schedule(time, EventPriority::Strategy,
        [time](BacktestEngine& e, const MarketView&) { e.submit_after(buy(time, time == 15 ? 5 : 2)); });
    check(!engine.run().halted && outcomes.size() == 4 && outcomes[0].executed == 5 && outcomes[1].executed == 0 &&
          outcomes[2].executed == 2 && outcomes[3].executed == 2, "only applied per-price observations or snapshot replenish");
    check(market.statistics().duplicates == 1 && market.statistics().stale == 1, "ignored tape counters retained");
}
} // namespace
int main() {
    try {
        money_cases(); budget_cases(); walk_and_basis_cases(); transactional_and_rejection_cases();
        zero_cost_and_spread_cases(); runtime_cases(); replenishment_cases();
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    std::cout << "market execution and accounting tests passed\n";
}
