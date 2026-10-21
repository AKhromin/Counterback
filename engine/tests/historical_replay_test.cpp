#include "counterback/replay_engine.hpp"

#include <iostream>
#include <limits>
#include <sstream>

using namespace counterback;
namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template <typename Fn> void rejects(Fn fn) {
    try { fn(); } catch (const BookDataError&) { return; }
    throw std::runtime_error("expected BookDataError");
}
DepthSnapshot snapshot(std::uint64_t id = 100) {
    return {"BTCUSDT", id, {{100, 1}, {99, 1}}, {{101, 1}, {102, 1}}, {}};
}
DepthUpdate update(std::uint64_t first, std::uint64_t last,
                   std::vector<PriceLevel> bids = {}, std::vector<PriceLevel> asks = {}) {
    return {"BTCUSDT", first, last, std::move(bids), std::move(asks), {}};
}
void book_cases() {
    HistoricalBook book(snapshot());
    auto outcome = book.apply(update(99, 101, {{100, 7}, {98, 0}, {100, 2}}, {{103, 0}}));
    check(book.best_bid()->quantity == 2, "absolute replacements and repeated prices");
    check(outcome.unseen_removals == 2 && outcome.outer_unseen_removals == 2, "unknown removals");
    book.apply(update(100, 101, {{100, 99}}));
    check(book.best_bid()->quantity == 2, "stale overlap ignored");
    book.apply(update(101, 102, {{100, 0}, {99, 0}, {98, 5}}));
    check(!book.bid_trusted() && !book.best_bid() && book.bids().at(98) == 5,
          "unknown outer horizon cannot become best bid");
    book.apply(update(103, 103, {{99, 3}}));
    check(book.top_trusted() && book.best_bid()->price == 99, "trust returns inside horizon");
    rejects([&] { book.apply(update(105, 105)); });
    rejects([&] { book.apply(update(104, 104, {{101, 1}})); });
    rejects([] { HistoricalBook invalid({"BTCUSDT", 1, {}, {}, {}}); });
    rejects([] { HistoricalBook b(snapshot()); b.apply(update(101, 101, {{100, -1}})); });
    rejects([] { HistoricalBook b(snapshot()); auto e = update(101, 101); e.symbol = "ETHUSDT"; b.apply(e); });
    const auto max = std::numeric_limits<std::uint64_t>::max();
    HistoricalBook last(snapshot(max - 1));
    last.apply(update(max, max, {{100, std::numeric_limits<Quantity>::max()}}));
    check(last.last_update_id() == max, "maximum update ID");
    last.apply(update(max, max));
}
void replay_cases() {
    ReplayEngine replay("BTCUSDT", {snapshot(), snapshot(110)});
    check(replay.apply(update(90, 90)) == "unanchored", "no future snapshot lookahead");
    check(replay.apply(update(99, 102)) == "applied", "snapshot bridge overlap");
    check(replay.apply(update(99, 102)) == "duplicate", "exact duplicate");
    rejects([&] { replay.apply(update(99, 102, {{100, 3}})); });
    check(replay.apply(update(100, 101)) == "stale", "stale batch");
    check(replay.apply(update(105, 105)) == "gap" && !replay.book(), "gap invalidates book");
    check(replay.apply(update(106, 106)) == "unanchored", "no carry across invalid interval");
    check(replay.apply(update(111, 111)) == "applied", "snapshot recovery");
    check(replay.statistics().sequence_gaps == 3, "stream gaps include snapshot-covered gaps");
    check(replay.statistics().segments == 2, "two segments");
    check(replay.apply(update(112, 112, {}, {{100, 1}})) == "structural_error" && !replay.book(),
          "locked book discarded");
    rejects([&] { replay.apply(update(110, 110)); });
    std::ostringstream out;
    replay.write_checkpoint(out, true);
    check(out.str().find("\"last_update_id\":null") != std::string::npos, "invalid checkpoint");
    ReplayEngine empty("BTCUSDT", {});
    empty.write_checkpoint(out, true);
}
} // namespace
int main() {
    try { book_cases(); replay_cases(); }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    std::cout << "historical replay tests passed\n";
}
