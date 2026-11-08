#include "counterback/prepared_replay.hpp"

#include <charconv>
#include <istream>
#include <limits>
#include <sstream>

namespace counterback {
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
        if (ch == '.' && !dot && digit) { 
            dot = true; digit = false; 
        } else if (ch >= '0' && ch <= '9') { 
            digit = true; nonzero |= ch != '0'; 
        } else {
            return false;
        } 
    }
    
    return digit && nonzero;
}
} // namespace


PreparedReplayReader::PreparedReplayReader(std::istream& input) : input_(input) {
    std::string line, magic;
    if (!std::getline(input_, line)) throw BookDataError("missing replay header");
    std::istringstream header(line);
    header >> magic;
    const auto version = number(header);
    if (!(header >> instrument_.symbol >> instrument_.tick_size >> instrument_.lot_size) ||
        magic != "COUNTERBACK_L2" || version != 1 ||
        !positive_decimal(instrument_.tick_size) || !positive_decimal(instrument_.lot_size)) {
        throw BookDataError("invalid replay header");
    }
    end_line(header);
    while (std::getline(input_, line)) {
        std::istringstream row(line);
        std::string kind;
        row >> kind;
        if (kind == "BEGIN") { end_line(row); return; }
        if (kind != "S") throw BookDataError("expected snapshot or BEGIN");
        DepthSnapshot snapshot{instrument_.symbol, number(row), {}, {}, {}};
        snapshot.observation.rx_utc_ns = number(row);
        identity(row, snapshot.observation);
        levels(row, snapshot.bids, snapshot.asks);
        snapshots_.push_back(std::move(snapshot));
    }
    throw BookDataError("missing BEGIN");
}

std::optional<DepthUpdate> PreparedReplayReader::next() {
    if (ended_) return std::nullopt;
    std::string line, kind;
    if (!std::getline(input_, line)) throw BookDataError("truncated replay data");
    std::istringstream row(line);
    row >> kind;
    if (kind == "END") {
        if (number(row) != records_) throw BookDataError("event count mismatch");
        end_line(row);
        if (std::getline(input_, line) || input_.bad()) {
            throw BookDataError("trailing replay data");
        }
        ended_ = true;
        return std::nullopt;
    }
    if (kind != "D") throw BookDataError("expected depth update or END");
    DepthUpdate event{instrument_.symbol, number(row), number(row), {}, {}, {}};
    event.observation.rx_utc_ns = number(row);
    event.observation.rx_mono_ns = number(row);
    event.observation.exchange_time_ms = number(row);
    identity(row, event.observation);
    levels(row, event.bids, event.asks);
    if (records_ == std::numeric_limits<std::uint64_t>::max()) {
        throw BookDataError("event count overflow");
    }
    ++records_;
    return event;
}
} // namespace counterback
