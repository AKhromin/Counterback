#include "counterback/backtest_engine.hpp"

#include <charconv>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string_view>

using namespace counterback;
namespace {
std::uint64_t number(const std::string& value) {
    std::uint64_t result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size()) {
        throw std::invalid_argument("expected uint64 integer");
    }
    return result;
}
Quantity quantity(const std::string& value) {
    const auto result = number(value);
    if (result > static_cast<std::uint64_t>(std::numeric_limits<Quantity>::max())) {
        throw std::invalid_argument("quantity outside int64 range");
    }
    return static_cast<Quantity>(result);
}
} // namespace
int main(int argc, char** argv) {
    try {
        if (argc != 8 && argc != 10) throw std::invalid_argument(
            "usage: counterback_market_orders INPUT ORDERS START_NS END_NS CASH_ATOMS FEE_RATE QUOTE_DECIMALS"
            " [INITIAL_LOTS TOTAL_BASIS_ATOMS]");
        RuntimeConfig config{number(argv[3]), number(argv[4]), 0};
        AccountConfig account;
        account.cash_atoms = parse_money(argv[5]);
        account.fee_rate = argv[6];
        const auto decimals = number(argv[7]);
        if (decimals > 18) throw std::invalid_argument("quote decimals outside 0..18");
        account.quote_decimals = static_cast<unsigned>(decimals);
        if (argc == 10) {
            account.inventory_lots = quantity(argv[8]);
            account.initial_basis_atoms = Exact(parse_money(argv[9]));
        }
        config.account = account;
        std::ifstream input(argv[1]), orders(argv[2]);
        if (!orders) throw std::invalid_argument("cannot open scripted orders");
        ReplayMarket market(input);
        BacktestEngine engine(market, config, [](const JournalRecord& row) {
            write_journal_record(std::cout, row);
            if (!std::cout) throw std::runtime_error("cannot write trading journal");
        });
        // Fixture format: submission time, fixed delay, ID, side, lots. All units are integers.
        std::string line;
        while (std::getline(orders, line)) {
            std::istringstream row(line);
            std::string time, delay, id, side, lots, extra;
            if (!(row >> time)) continue;
            if (time.front() == '#') continue;
            if (!(row >> delay >> id >> side >> lots) || (row >> extra) || (side != "buy" && side != "sell")) {
                throw std::invalid_argument("expected: TIME_NS DELAY_NS ORDER_ID buy|sell QUANTITY_LOTS");
            }
            const auto when = number(time), latency = number(delay);
            if (when < config.start_ns) throw std::invalid_argument("scripted submission before run start");
            const Order order{number(id), side == "buy" ? OrderSide::Buy : OrderSide::Sell,
                              OrderType::Market, 0, quantity(lots)};
            engine.schedule(when, EventPriority::Strategy, [order, latency](BacktestEngine& e, const MarketView&) {
                e.submit_after(order, latency);
            });
        }
        if (orders.bad()) throw std::runtime_error("cannot read scripted orders");
        const auto& initial = engine.portfolio();
        std::cout << "{\"kind\":\"trading_config\",\"schema_version\":1,\"start_ns\":" << config.start_ns
                  << ",\"end_ns\":" << config.end_ns << ",\"quote_decimals\":" << account.quote_decimals
                  << ",\"initial_cash_atoms\":\"" << money_string(initial.state().cash_atoms)
                  << "\",\"initial_inventory_lots\":" << initial.state().inventory_lots
                  << ",\"initial_basis_atoms\":\"" << money_string(initial.state().basis_atoms.numerator())
                  << "\",\"fee_rate\":{\"numerator\":\"" << money_string(initial.fees().rate().numerator())
                  << "\",\"denominator\":\"" << money_string(initial.fees().rate().denominator())
                  << "\"},\"fee_source\":\"benchmark_assumption\",\"execution\":\"historical_visible_depth_v1\","
                     "\"valuation\":\"trusted_best_bid\",\"latency\":\"per_script_fixed_delay_ns\"}\n";
        const auto result = engine.run();
        std::cout << "{\"kind\":\"trading_summary\",\"schema_version\":1,\"halted\":"
                  << (result.halted ? "true" : "false") << ",\"ever_ready\":"
                  << (result.ever_ready ? "true" : "false") << ",\"stopped_at_ns\":" << result.stopped_at_ns
                  << ",\"dispatched\":" << result.dispatched << ",\"pending_events\":" << result.pending_events
                  << ",\"reason\":\"" << result.reason << "\"}\n";
        if (!std::cout) throw std::runtime_error("cannot write trading journal");
        return result.halted ? 2 : 0;
    } catch (const std::exception& error) {
        std::cerr << "trading run failed: " << error.what() << '\n';
        return 1;
    }
}
