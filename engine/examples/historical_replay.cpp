#include "counterback/replay_engine.hpp"

#include <charconv>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>

using namespace counterback;
namespace {
std::uint64_t number(std::istream& input) {
    std::string text;
    if (!(input >> text)) throw BookDataError("missing integer");
    std::uint64_t value = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size()) {
        throw BookDataError("invalid unsigned integer");
    }
    return value;
}
void end_line(std::istream& input) {
    std::string extra;
    if (input >> extra) throw BookDataError("unexpected trailing field");
}
void identity(std::istream& input, Observation& observation) {
    if (!(input >> observation.recorder_id >> observation.run_id)) {
        throw BookDataError("missing recorder/run identity");
    }
}
void levels(std::istream& input, std::vector<PriceLevel>& bids, std::vector<PriceLevel>& asks) {
    const auto nb = number(input), na = number(input);
    if (nb > 1000000 || na > 1000000) throw BookDataError("too many levels in record");
    for (auto* side : {&bids, &asks}) {
        const auto count = side == &bids ? nb : na;
        for (std::uint64_t i = 0; i < count; ++i) {
            const auto price = number(input), quantity = number(input);
            if (price == 0 || price > static_cast<std::uint64_t>(std::numeric_limits<Price>::max()) ||
                quantity > static_cast<std::uint64_t>(std::numeric_limits<Quantity>::max())) {
                throw BookDataError("level outside int64 range");
            }
            side->push_back({static_cast<Price>(price), static_cast<Quantity>(quantity)});
        }
    }
    end_line(input);
}
bool positive_decimal(const std::string& text) {
    bool digit = false, dot = false, nonzero = false;
    for (char ch : text) {
        if (ch == '.' && !dot && digit) { dot = true; digit = false; }
        else if (ch >= '0' && ch <= '9') { digit = true; nonzero |= ch != '0'; }
        else return false;
    }
    return digit && nonzero;
}
} // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2 || argc > 3) throw BookDataError("usage: counterback_replay INPUT [CHECKPOINT_EVERY]");
        std::uint64_t every = 1;
        if (argc == 3) {
            std::istringstream value(argv[2]);
            every = number(value); end_line(value);
            if (every == 0) throw BookDataError("checkpoint interval must be positive");
        }
        std::ifstream file(argv[1]);
        std::string line, magic, symbol, tick, lot;
        if (!std::getline(file, line)) throw BookDataError("missing replay header");
        std::istringstream header(line);
        header >> magic;
        const auto version = number(header);
        if (!(header >> symbol >> tick >> lot) || magic != "COUNTERBACK_L2" || version != 1 ||
            !positive_decimal(tick) || !positive_decimal(lot)) throw BookDataError("invalid replay header");
        end_line(header);
        std::vector<DepthSnapshot> snapshots;
        bool begin = false;
        while (std::getline(file, line)) {
            std::istringstream input(line);
            std::string kind; input >> kind;
            if (kind == "BEGIN") { end_line(input); begin = true; break; }
            if (kind != "S") throw BookDataError("expected snapshot or BEGIN");
            DepthSnapshot snapshot{symbol, number(input), {}, {}, {}};
            snapshot.observation.rx_utc_ns = number(input);
            identity(input, snapshot.observation);
            levels(input, snapshot.bids, snapshot.asks);
            snapshots.push_back(std::move(snapshot));
        }
        if (!begin) throw BookDataError("missing BEGIN");
        ReplayEngine replay(symbol, std::move(snapshots));
        bool ended = false;
        while (std::getline(file, line)) {
            std::istringstream input(line);
            std::string kind; input >> kind;
            if (kind == "END") {
                if (number(input) != replay.statistics().events) throw BookDataError("event count mismatch");
                end_line(input); ended = true; break;
            }
            if (kind != "D") throw BookDataError("expected depth update or END");
            DepthUpdate event{symbol, number(input), number(input), {}, {}, {}};
            event.observation.rx_utc_ns = number(input);
            event.observation.rx_mono_ns = number(input);
            event.observation.exchange_time_ms = number(input);
            identity(input, event.observation);
            levels(input, event.bids, event.asks);
            replay.apply(event);
            if (replay.statistics().events % every == 0) replay.write_checkpoint(std::cout);
        }
        if (!ended || std::getline(file, line) || file.bad()) throw BookDataError("truncated or trailing replay data");
        replay.write_checkpoint(std::cout, true);
        // Gaps and unknown horizons are valid observations; structural corruption is failure.
        return replay.statistics().structural_errors.empty() ? 0 : 2;
    } catch (const std::exception& error) {
        std::cerr << "replay failed: " << error.what() << '\n';
        return 1;
    }
}
