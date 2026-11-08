#pragma once

#include "counterback/market_model.hpp"
#include "counterback/money.hpp"

#include <map>
#include <set>

namespace counterback {
enum class ExecutionStatus { Filled, Expired, Rejected };
const char* execution_status_name(ExecutionStatus status);

struct FillRecord {
    std::uint64_t id;
    OrderId order_id;
    Timestamp time_ns;
    OrderSide side;
    Price price;
    Quantity quantity;
    Money notional_atoms;
    Money fee_atoms;
    std::string symbol;
    // All WP3 fills are modeled taker-like historical executions, not observed trades.
};
struct ExecutionOutcome {
    Order order;
    Timestamp time_ns;
    ExecutionStatus status;
    std::string reason;
    Quantity executed = 0, remaining = 0;
    std::vector<FillRecord> fills;
};

// Separate from the exogenous book: strategies never rewrite the recorded path.
class LiquidityBudget {
public:
    void observe(const DepthUpdate& update, bool replaces_book);
    Quantity available(OrderSide book_side, const PriceLevel& level) const;
    void consume(const FillRecord& fill, const PriceLevel& observed);
private:
    std::map<std::pair<OrderSide, Price>, Quantity> consumed_;
};

class Portfolio;
class MarketOrderExecution {
public:
    void observe(const MarketEvent& event, const std::string& status);
    ExecutionOutcome execute(const Order& order, Timestamp now, const MarketView& view, Portfolio& portfolio);
    ExecutionOutcome terminate(const Order& order, Timestamp now, const std::string& reason);
private:
    LiquidityBudget budget_;
    std::set<OrderId> used_orders_;
    std::uint64_t last_fill_id_ = 0;
};
} // namespace counterback
