#pragma once

#include "counterback/types.hpp"

#include <map>
#include <optional>
#include <stdexcept>
#include <string>

namespace counterback {

struct PriceLevel {
    Price price;
    Quantity quantity;
    bool operator==(const PriceLevel& other) const {
        return price == other.price && quantity == other.quantity;
    }
};

struct Observation {
    std::uint64_t rx_utc_ns = 0;
    std::uint64_t rx_mono_ns = 0; // Comparable only within recorder_id + run_id.
    std::uint64_t exchange_time_ms = 0; // Zero means unavailable (e.g. REST snapshot).
    std::string recorder_id;
    std::string run_id;
};

struct DepthSnapshot {
    std::string symbol;
    std::uint64_t last_update_id;
    std::vector<PriceLevel> bids;
    std::vector<PriceLevel> asks;
    Observation observation;
};

struct DepthUpdate {
    std::string symbol;
    std::uint64_t first_update_id;
    std::uint64_t final_update_id;
    std::vector<PriceLevel> bids;
    std::vector<PriceLevel> asks;
    Observation observation;
};

class BookDataError : public std::invalid_argument {
public:
    using std::invalid_argument::invalid_argument;
};

struct DepthApplyResult {
    std::uint64_t unseen_removals = 0;
    std::uint64_t outer_unseen_removals = 0;
};

// Aggregated external L2 state, with no order IDs, fills or FIFO assumptions.
// On BookDataError during apply, discard this book (ReplayEngine does so).
class HistoricalBook {
public:
    using Levels = std::map<Price, Quantity>;
    explicit HistoricalBook(const DepthSnapshot& snapshot);
    DepthApplyResult apply(const DepthUpdate& event);
    std::uint64_t last_update_id() const { return last_update_id_; }
    Price bid_boundary() const { return bid_boundary_; }
    Price ask_boundary() const { return ask_boundary_; }
    bool bid_trusted() const;
    bool ask_trusted() const;
    bool top_trusted() const { return bid_trusted() && ask_trusted(); }
    std::optional<PriceLevel> best_bid() const;
    std::optional<PriceLevel> best_ask() const;
    // Observed levels only; absence outside the snapshot horizon means unknown.
    const Levels& bids() const { return bids_; }
    const Levels& asks() const { return asks_; }

private:
    std::string symbol_;
    std::uint64_t last_update_id_;
    Levels bids_, asks_;
    Price bid_boundary_, ask_boundary_;
    static Levels load(const std::vector<PriceLevel>& levels);
    void apply_side(Levels& side, const std::vector<PriceLevel>& updates,
                    bool bid, DepthApplyResult& result);
};

} // namespace counterback
