#pragma once

#include "counterback/types.hpp"

#include <functional>
#include <list>
#include <map>
#include <optional>
#include <unordered_map>
#include <unordered_set>

namespace counterback {

// Single-instrument, single-threaded synthetic order-level book. Submission order
// establishes FIFO priority. External L2 updates must never enter this book as orders.
class MatchingEngine {
public:
    MatchingEngine() = default;
    MatchingEngine(const MatchingEngine&) = delete;
    MatchingEngine& operator=(const MatchingEngine&) = delete;
    MatchingEngine(MatchingEngine&&) = delete;
    MatchingEngine& operator=(MatchingEngine&&) = delete;

    // Throws invalid_argument before mutation for malformed or previously used IDs.
    ExecutionReport submit(const Order& order);
    bool cancel(OrderId id);
    std::optional<RestingOrder> find(OrderId id) const;
    std::optional<Price> best_bid() const;
    std::optional<Price> best_ask() const;
    std::size_t open_order_count() const noexcept { return open_.size(); }

    // Best price first, then FIFO within each price. Returns a value snapshot.
    std::vector<RestingOrder> orders(OrderSide side) const;

private:
    using Queue = std::list<RestingOrder>;
    using Bids = std::map<Price, Queue, std::greater<Price>>;
    using Asks = std::map<Price, Queue>;
    
    struct Location {
        OrderSide side;
        Price price;
        Queue::iterator position;
    };

    template <typename Levels>
    void match(Levels& levels, const Order& order, ExecutionReport& report);
    template <typename Levels>
    void rest(Levels& levels, const Order& order, Quantity remaining);

    Bids bids_;
    Asks asks_;
    std::unordered_map<OrderId, Location> open_;
    std::unordered_set<OrderId> used_ids_;
};

} // namespace counterback
