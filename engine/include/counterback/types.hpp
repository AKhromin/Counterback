#pragma once

#include <cstdint>
#include <vector>

namespace counterback {

// Instrument-specific integer ticks and lots; conversion belongs at the boundary.
using OrderId = std::uint64_t;
using Price = std::int64_t;
using Quantity = std::int64_t;

enum class OrderSide { Buy, Sell };
enum class OrderType { Limit, Market };
enum class OrderStatus { Filled, Resting, Expired };

struct Order {
    OrderId id;
    OrderSide side;
    OrderType type;
    Price price; // Positive for limits, exactly zero for markets.
    Quantity quantity;
};

struct RestingOrder {
    OrderId id;
    OrderSide side;
    Price price;
    Quantity remaining;
};

struct Trade {
    OrderId maker_id;
    OrderId taker_id;
    OrderSide taker_side;
    Price price;
    Quantity quantity;
};

struct ExecutionReport {
    OrderId order_id;
    OrderStatus status;
    Quantity executed;
    Quantity remaining; // Unfilled: resting for limits, expired for markets.
    std::vector<Trade> trades;
};

} // namespace counterback
