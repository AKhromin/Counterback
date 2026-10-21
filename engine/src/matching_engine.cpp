#include "counterback/matching_engine.hpp"

#include <algorithm>
#include <iterator>
#include <stdexcept>

namespace counterback {

ExecutionReport MatchingEngine::submit(const Order& order) {
    if (order.id == 0 || used_ids_.count(order.id) != 0) {
        throw std::invalid_argument("order ID must be nonzero and unique for this engine");
    }
    
    if (order.side != OrderSide::Buy && order.side != OrderSide::Sell) {
        throw std::invalid_argument("invalid order side");
    }
    
    if (order.type != OrderType::Limit && order.type != OrderType::Market) {
        throw std::invalid_argument("invalid order type");
    }
    
    if (order.quantity <= 0 || (order.type == OrderType::Limit && order.price <= 0) ||
        (order.type == OrderType::Market && order.price != 0)) {
        throw std::invalid_argument("invalid quantity or price");
    }
    
    used_ids_.insert(order.id);
    ExecutionReport report{order.id, OrderStatus::Filled, 0, order.quantity, {}};
    
    if (order.side == OrderSide::Buy) {
        match(asks_, order, report);
    } else {
        match(bids_, order, report);
    }
    
    if (report.remaining > 0) {
        if (order.type == OrderType::Market) {
            report.status = OrderStatus::Expired;
        } else {
            report.status = OrderStatus::Resting;
            if (order.side == OrderSide::Buy) {
                rest(bids_, order, report.remaining);
            } else {
                rest(asks_, order, report.remaining);
            }
        }
    }
    
    return report;
}

template <typename Levels>
void MatchingEngine::match(Levels& levels, const Order& order, ExecutionReport& report) {
    while (report.remaining > 0 && !levels.empty()) {
        auto level = levels.begin();
        const auto price = level->first;
        
        if (order.type == OrderType::Limit &&
            (order.side == OrderSide::Buy ? price > order.price : price < order.price)) {
            break;
        }
        
        auto& queue = level->second;
        auto& maker = queue.front();
        const auto quantity = std::min(report.remaining, maker.remaining);
        report.trades.push_back({maker.id, order.id, order.side, price, quantity});
        maker.remaining -= quantity;
        report.remaining -= quantity;
        report.executed += quantity;
        
        if (maker.remaining == 0) {
            open_.erase(maker.id);
            queue.pop_front();
        }
        
        if (queue.empty()) {
            levels.erase(level);
        }
    }
}

template <typename Levels>
void MatchingEngine::rest(Levels& levels, const Order& order, Quantity remaining) {
    auto& queue = levels[order.price];
    queue.push_back({order.id, order.side, order.price, remaining});
    open_.emplace(order.id, Location{order.side, order.price, std::prev(queue.end())});
}

bool MatchingEngine::cancel(OrderId id) {
    const auto found = open_.find(id);
    
    if (found == open_.end()) {
        return false;
    }
    
    const auto& location = found->second;
    
    if (location.side == OrderSide::Buy) {
        auto level = bids_.find(location.price);
        level->second.erase(location.position);
        if (level->second.empty()) {
            bids_.erase(level);
        }
    } else {
        auto level = asks_.find(location.price);
        level->second.erase(location.position);
        if (level->second.empty()) {
            asks_.erase(level);
        }
    }
    
    open_.erase(found);
    return true;
}

std::optional<RestingOrder> MatchingEngine::find(OrderId id) const {
    const auto found = open_.find(id);
    
    if (found == open_.end()) {
        return std::nullopt;
    }
    
    return *found->second.position;
}

std::optional<Price> MatchingEngine::best_bid() const {
    return bids_.empty() ? std::nullopt : std::optional<Price>{bids_.begin()->first};
}

std::optional<Price> MatchingEngine::best_ask() const {
    return asks_.empty() ? std::nullopt : std::optional<Price>{asks_.begin()->first};
}

std::vector<RestingOrder> MatchingEngine::orders(OrderSide side) const {
    std::vector<RestingOrder> result;
    if (side == OrderSide::Buy) {
        for (const auto& level : bids_) {
            result.insert(result.end(), level.second.begin(), level.second.end());
        }
    } else if (side == OrderSide::Sell) {
        for (const auto& level : asks_) {
            result.insert(result.end(), level.second.begin(), level.second.end());
        }
    } else {
        throw std::invalid_argument("invalid order side");
    }
    return result;
}

} // namespace counterback
