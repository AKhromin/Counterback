#include "counterback/backtest_engine.hpp"

#include <charconv>
#include <fstream>
#include <iostream>
#include <string_view>

using namespace counterback;
namespace {
std::uint64_t argument(const char* value) {
    std::string_view text(value);
    std::uint64_t number = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), number);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
        throw std::invalid_argument("arguments must be uint64 integers");
    }
    return number;
}
}
int main(int argc, char** argv) {
    try {
        if (argc < 4 || argc > 6) {
            throw std::invalid_argument(
                "usage: counterback_runtime INPUT START_NS END_NS [JOURNAL_EVERY] [WARMUP_UPDATES]");
        }
        RuntimeConfig config{argument(argv[2]), argument(argv[3]), argc == 6 ? argument(argv[5]) : 0};
        const auto every = argc >= 5 ? argument(argv[4]) : 1;
        if (every == 0) throw std::invalid_argument("journal interval must be positive");
        std::ifstream input(argv[1]);
        ReplayMarket market(input);
        BacktestEngine engine(market, config, [&](const JournalRecord& record) {
            if (record.event.id % every == 0 || record.status == "end" || !record.view.top_trusted()) {
                write_journal_record(std::cout, record);
            }
            if (const auto* event = std::get_if<MarketEvent>(&record.event.payload)) {
                if (event->source_id % every == 0) market.write_checkpoint(std::cout);
            }
        });
        // An observation-only C++ callback exercises dispatch; strategy bindings are WP5.
        const auto result = engine.run([](BacktestEngine&, const MarketView&) {});
        market.write_checkpoint(std::cout, true);
        std::cout << "{\"kind\":\"runtime_summary\",\"schema_version\":1,\"halted\":"
                  << (result.halted ? "true" : "false") << ",\"ever_ready\":"
                  << (result.ever_ready ? "true" : "false") << ",\"stopped_at_ns\":"
                  << result.stopped_at_ns << ",\"dispatched\":" << result.dispatched
                  << ",\"applied_updates\":" << result.applied_updates
                  << ",\"strategy_callbacks\":" << result.strategy_callbacks
                  << ",\"pending_events\":" << result.pending_events
                  << ",\"reason\":\"" << result.reason << "\"}\n";
        return result.halted ? 2 : 0;
    } catch (const std::exception& error) {
        std::cerr << "runtime failed: " << error.what() << '\n';
        return 1;
    }
}
