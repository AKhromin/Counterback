#pragma once

#include "counterback/historical_book.hpp"

#include <iosfwd>

namespace counterback {
struct Instrument {
    std::string symbol, tick_size, lot_size;
};

// Header snapshots are source-list metadata, never a strategy-visible initial book.
// Depth records are read one at a time; the stream must outlive this reader.
class PreparedReplayReader {
public:
    explicit PreparedReplayReader(std::istream& input);
    const Instrument& instrument() const { return instrument_; }
    const std::vector<DepthSnapshot>& snapshots() const { return snapshots_; }
    std::optional<DepthUpdate> next();
    std::uint64_t records_read() const { return records_; }
private:
    std::istream& input_;
    Instrument instrument_;
    std::vector<DepthSnapshot> snapshots_;
    std::uint64_t records_ = 0;
    bool ended_ = false;
};
} // namespace counterback
