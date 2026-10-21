#include "counterback/historical_book.hpp"

namespace counterback {
namespace {
    void validate(const PriceLevel& level) {
        if (level.price <= 0 || level.quantity < 0) {
            throw BookDataError("prices must be positive and quantities non-negative");
        }
    }
} // namespace

HistoricalBook::Levels HistoricalBook::load(const std::vector<PriceLevel>& levels) {
    Levels result;
    for (const auto& level : levels) {
        validate(level);
        // Match HorizonBook: zero snapshot entries do not erase earlier entries.
        
        if (level.quantity != 0) {
            result[level.price] = level.quantity;
        }
    }
    return result;
}

HistoricalBook::HistoricalBook(const DepthSnapshot& snapshot)
    : symbol_(snapshot.symbol), last_update_id_(snapshot.last_update_id),
      bids_(load(snapshot.bids)), asks_(load(snapshot.asks)) {
    
        if (bids_.empty() || asks_.empty()) { 
        throw BookDataError("snapshot must contain both sides");
    }

    bid_boundary_ = bids_.begin()->first;
    ask_boundary_ = asks_.rbegin()->first;
}

bool HistoricalBook::bid_trusted() const {
    return !bids_.empty() && bids_.rbegin()->first >= bid_boundary_;
}

bool HistoricalBook::ask_trusted() const {
    return !asks_.empty() && asks_.begin()->first <= ask_boundary_;
}

std::optional<PriceLevel> HistoricalBook::best_bid() const {
    if (!bid_trusted()) {
        return std::nullopt;
    }

    return PriceLevel{bids_.rbegin()->first, bids_.rbegin()->second};
}

std::optional<PriceLevel> HistoricalBook::best_ask() const {
    if (!ask_trusted()) { 
        return std::nullopt;
    }

    return PriceLevel{asks_.begin()->first, asks_.begin()->second};
}

void HistoricalBook::apply_side(Levels& side, const std::vector<PriceLevel>& updates,
                                bool bid, DepthApplyResult& result) {
    for (const auto& level : updates) {
        validate(level);
        if (level.quantity == 0) {
            if (side.erase(level.price) == 0) {
                ++result.unseen_removals;
                if (bid ? level.price < bid_boundary_ : level.price > ask_boundary_) {
                    ++result.outer_unseen_removals;
                }
            }
        } else {
            side[level.price] = level.quantity;
        }
    }
}

DepthApplyResult HistoricalBook::apply(const DepthUpdate& event) {
    if (event.symbol != symbol_) {
        throw BookDataError("event symbol does not match book");
    }

    if (event.first_update_id > event.final_update_id) {
        throw BookDataError("invalid update range");
    }

    if (event.final_update_id <= last_update_id_) {
        return {};
    }

    // Subtraction avoids overflow at UINT64_MAX.
    if (event.first_update_id > last_update_id_ && event.first_update_id - last_update_id_ > 1) {
        throw BookDataError("sequence gap");
    }

    DepthApplyResult result;
    
    apply_side(bids_, event.bids, true, result);
    apply_side(asks_, event.asks, false, result);
    
    last_update_id_ = event.final_update_id;
    
    if (top_trusted() && best_bid()->price >= best_ask()->price) {
        throw BookDataError("crossed or locked reconstructed book");
    }
    return result;
}
} // namespace counterback
