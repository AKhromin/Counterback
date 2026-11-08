#include "counterback/execution.hpp"
#include "counterback/portfolio.hpp"

#include <algorithm>
#include <limits>

namespace counterback {
const char* execution_status_name(ExecutionStatus status) {
    switch (status) {
    case ExecutionStatus::Filled: return "filled";
    case ExecutionStatus::Expired: return "expired";
    case ExecutionStatus::Rejected: return "rejected";
    }
    throw std::invalid_argument("invalid execution status");
}
void LiquidityBudget::observe(const DepthUpdate& update, bool replaces_book) {
    if (replaces_book) consumed_.clear();
    for (const auto side : {OrderSide::Buy, OrderSide::Sell}) {
        for (const auto& level : side == OrderSide::Buy ? update.bids : update.asks) {
            consumed_.erase({side, level.price});
        }
    }
}
Quantity LiquidityBudget::available(OrderSide side, const PriceLevel& level) const {
    if ((side != OrderSide::Buy && side != OrderSide::Sell) || level.price <= 0 || level.quantity <= 0) {
        throw BookDataError("invalid budget level");
    }
    const auto found = consumed_.find({side, level.price});
    const auto consumed = found == consumed_.end() ? 0 : found->second;
    if (consumed > level.quantity) throw BookDataError("liquidity budget exceeds observed quantity");
    return level.quantity - consumed;
}
void LiquidityBudget::consume(const FillRecord& fill, const PriceLevel& observed) {
    if (fill.quantity <= 0 || fill.price <= 0 || (fill.side != OrderSide::Buy && fill.side != OrderSide::Sell)) {
        throw std::invalid_argument("invalid liquidity consumption");
    }
    const auto side = fill.side == OrderSide::Buy ? OrderSide::Sell : OrderSide::Buy;
    if (observed.price != fill.price || fill.quantity > available(side, observed)) {
        throw BookDataError("fill exceeds available observed liquidity");
    }
    auto& consumed = consumed_[{side, fill.price}];
    if (fill.quantity > std::numeric_limits<Quantity>::max() - consumed) throw std::overflow_error("liquidity overflow");
    consumed += fill.quantity;
}
void MarketOrderExecution::observe(const MarketEvent& event, const std::string& status) {
    if (status == "applied") budget_.observe(std::get<DepthUpdate>(event.payload), event.replaces_book);
}
ExecutionOutcome MarketOrderExecution::terminate(const Order& order, Timestamp now, const std::string& reason) {
    if (!order.id || !used_orders_.insert(order.id).second) {
        return {order, now, ExecutionStatus::Rejected, "duplicate_or_invalid_order_id", 0, order.quantity, {}};
    }
    return {order, now, ExecutionStatus::Expired, reason, 0, order.quantity, {}};
}
ExecutionOutcome MarketOrderExecution::execute(const Order& order, Timestamp now, const MarketView& view,
                                              Portfolio& portfolio) {
    ExecutionOutcome outcome{order, now, ExecutionStatus::Rejected, "", 0, order.quantity, {}};
    if (!order.id || !used_orders_.insert(order.id).second) {
        outcome.reason = "duplicate_or_invalid_order_id"; return outcome;
    }
    if (order.quantity <= 0 || order.price != 0 || (order.side != OrderSide::Buy && order.side != OrderSide::Sell)) {
        outcome.reason = "invalid_order"; return outcome;
    }
    if (order.type != OrderType::Market) { outcome.reason = "unsupported_order_type"; return outcome; }
    if (view.validity() != MarketValidity::Trusted || !view.top_trusted() || !view.bid_boundary() ||
        !view.ask_boundary() || !view.best_bid() || !view.best_ask() || view.visible_at_ns() > now) {
        outcome.reason = "market_untrusted"; return outcome;
    }
    if (view.best_bid()->price >= view.best_ask()->price) throw BookDataError("crossed execution book");
    const auto& instrument = portfolio.grid().instrument();
    if (instrument.symbol != view.instrument().symbol || instrument.tick_size != view.instrument().tick_size ||
        instrument.lot_size != view.instrument().lot_size) throw std::invalid_argument("execution instrument mismatch");
    if (order.side == OrderSide::Sell && order.quantity > portfolio.state().inventory_lots) {
        outcome.reason = "insufficient_inventory"; return outcome;
    }
    Money cumulative = 0, previous_fee = 0;
    auto last_id = last_fill_id_;
    auto next_budget = budget_;
    const auto book_side = order.side == OrderSide::Buy ? OrderSide::Sell : OrderSide::Buy;
    std::optional<Price> previous_price;
    const auto& levels = book_side == OrderSide::Buy ? view.bids() : view.asks();
    for (const auto& level : levels) {
        if (level.price <= 0 || level.quantity <= 0 || (previous_price &&
            (book_side == OrderSide::Buy ? level.price >= *previous_price : level.price <= *previous_price))) {
            throw BookDataError("execution depth must be positive, unique and best-first");
        }
        previous_price = level.price;
        if (book_side == OrderSide::Buy ? level.price < *view.bid_boundary() : level.price > *view.ask_boundary()) break;
        const auto available = next_budget.available(book_side, level);
        const auto quantity = std::min(available, outcome.remaining);
        if (!quantity) continue;
        if (last_id == std::numeric_limits<std::uint64_t>::max()) throw std::overflow_error("fill ID overflow");
        const auto notional = portfolio.grid().notional(level.price, quantity);
        cumulative = checked_add(cumulative, notional);
        const auto fee = portfolio.fees().cumulative_fee(cumulative);
        FillRecord fill{++last_id, order.id, now, order.side, level.price, quantity, notional,
                        checked_sub(fee, previous_fee), instrument.symbol};
        previous_fee = fee;
        next_budget.consume(fill, level);
        outcome.fills.push_back(fill);
        outcome.executed += quantity;
        outcome.remaining -= quantity;
        if (!outcome.remaining) break;
    }
    outcome.status = outcome.remaining ? ExecutionStatus::Expired : ExecutionStatus::Filled;
    outcome.reason = outcome.remaining ? "insufficient_known_liquidity" : "filled";
    try {
        portfolio.apply(outcome); // Complete dry-run, then a single authoritative commit.
    } catch (const AccountConstraintError& error) {
        outcome.status = ExecutionStatus::Rejected;
        outcome.reason = error.what();
        outcome.executed = 0;
        outcome.remaining = order.quantity;
        outcome.fills.clear();
        return outcome;
    }
    budget_ = std::move(next_budget);
    last_fill_id_ = last_id;
    return outcome;
}
} // namespace counterback
