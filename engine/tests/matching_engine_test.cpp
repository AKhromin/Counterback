#include "counterback/matching_engine.hpp"

#include <algorithm>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>

using namespace counterback;

// Checks remain active in Release builds (unlike assert).
void check(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void equal_trade(const Trade& actual, const Trade& expected) {
    check(actual.maker_id == expected.maker_id && actual.taker_id == expected.taker_id &&
              actual.taker_side == expected.taker_side && actual.price == expected.price &&
              actual.quantity == expected.quantity,
          "trade differs");
}

void matching_cases() {
    MatchingEngine engine;
    check(!engine.best_bid() && !engine.best_ask() && !engine.find(1), "empty book");
    check(!engine.cancel(1), "unknown cancellation");
    engine.submit({1, OrderSide::Sell, OrderType::Limit, 102, 7});
    engine.submit({2, OrderSide::Sell, OrderType::Limit, 101, 3});
    engine.submit({3, OrderSide::Sell, OrderType::Limit, 101, 4});
    auto report = engine.submit({4, OrderSide::Buy, OrderType::Limit, 101, 5});
    check(report.status == OrderStatus::Filled && report.executed == 5 && report.remaining == 0,
          "filled limit status");
    check(report.trades.size() == 2, "two makers");
    equal_trade(report.trades[0], {2, 4, OrderSide::Buy, 101, 3});
    equal_trade(report.trades[1], {3, 4, OrderSide::Buy, 101, 2});
    check(engine.find(3)->remaining == 2 && !engine.find(2), "partial maker and removal");
    report = engine.submit({5, OrderSide::Buy, OrderType::Limit, 101, 5});
    check(report.status == OrderStatus::Resting && report.executed == 2 && report.remaining == 3,
          "limit remainder rests without crossing worse price");
    check(engine.best_bid() == 101 && engine.best_ask() == 102, "best prices");
    report = engine.submit({6, OrderSide::Sell, OrderType::Limit, 99, 1});
    equal_trade(report.trades.at(0), {5, 6, OrderSide::Sell, 101, 1});
    check(engine.find(5)->remaining == 2, "sell executes at maker price");
    check(engine.cancel(5) && !engine.cancel(5) && !engine.best_bid(), "cancel last bid");
    report = engine.submit({7, OrderSide::Buy, OrderType::Market, 0, 10});
    check(report.status == OrderStatus::Expired && report.executed == 7 && report.remaining == 3,
          "market expires after exhausting liquidity");
    check(engine.open_order_count() == 0 && !engine.find(7) && !engine.best_ask(), "empty again");
    report = engine.submit({8, OrderSide::Sell, OrderType::Market, 0, 2});
    check(report.status == OrderStatus::Expired && report.executed == 0 && report.trades.empty(),
          "market against empty book");
}

void cancellation_and_priority() {
    MatchingEngine engine;
    for (OrderId id = 1; id <= 4; ++id) {
        engine.submit({id, OrderSide::Buy, OrderType::Limit, 100, 2});
    }
    check(engine.cancel(2) && engine.cancel(4), "cancel middle and tail");
    auto report = engine.submit({5, OrderSide::Sell, OrderType::Market, 0, 1});
    equal_trade(report.trades.at(0), {1, 5, OrderSide::Sell, 100, 1});
    engine.submit({6, OrderSide::Buy, OrderType::Limit, 100, 2});
    report = engine.submit({7, OrderSide::Sell, OrderType::Market, 0, 5});
    check(report.trades.size() == 3 && report.status == OrderStatus::Filled, "FIFO after cancel");
    equal_trade(report.trades[0], {1, 7, OrderSide::Sell, 100, 1});
    equal_trade(report.trades[1], {3, 7, OrderSide::Sell, 100, 2});
    equal_trade(report.trades[2], {6, 7, OrderSide::Sell, 100, 2});

    engine.submit({8, OrderSide::Sell, OrderType::Limit, 102, 1});
    engine.submit({9, OrderSide::Sell, OrderType::Limit, 101, 1});
    check(engine.cancel(9) && engine.best_ask() == 102, "cancel best ask reveals next");
    check(engine.cancel(8) && !engine.best_ask(), "cancel last ask");
}

void invalid_orders() {
    MatchingEngine engine;
    engine.submit({1, OrderSide::Buy, OrderType::Limit, 100, 3});
    const Order bad[] = {
        {0, OrderSide::Buy, OrderType::Limit, 100, 1},
        {1, OrderSide::Sell, OrderType::Market, 0, 1},
        {2, OrderSide::Buy, OrderType::Limit, 0, 1},
        {2, OrderSide::Buy, OrderType::Limit, -1, 1},
        {2, OrderSide::Buy, OrderType::Limit, 100, 0},
        {2, OrderSide::Buy, OrderType::Limit, 100, -1},
        {2, OrderSide::Buy, OrderType::Market, 100, 1},
        {2, static_cast<OrderSide>(99), OrderType::Limit, 100, 1},
        {2, OrderSide::Buy, static_cast<OrderType>(99), 100, 1},
    };
    for (const auto& order : bad) {
        bool rejected = false;
        try { engine.submit(order); } catch (const std::invalid_argument&) { rejected = true; }
        check(rejected && engine.open_order_count() == 1 && engine.find(1)->remaining == 3,
              "invalid request must leave book unchanged");
    }
    // Invalid submissions do not reserve their IDs; successful IDs are never reused.
    engine.submit({2, OrderSide::Sell, OrderType::Limit, 101, 1});
    check(engine.cancel(2), "cancel accepted ID");
    bool rejected = false;
    try { engine.submit({2, OrderSide::Buy, OrderType::Limit, 99, 1}); }
    catch (const std::invalid_argument&) { rejected = true; }
    check(rejected, "cancelled ID cannot be reused");
    engine.submit({3, OrderSide::Sell, OrderType::Market, 0, 3});
    rejected = false;
    try { engine.submit({3, OrderSide::Buy, OrderType::Limit, 99, 1}); }
    catch (const std::invalid_argument&) { rejected = true; }
    check(rejected, "filled ID cannot be reused");
    engine.submit({4, OrderSide::Sell, OrderType::Market, 0, 1});
    rejected = false;
    try { engine.submit({4, OrderSide::Buy, OrderType::Limit, 99, 1}); }
    catch (const std::invalid_argument&) { rejected = true; }
    check(rejected, "expired ID cannot be reused");
}

void integer_boundaries() {
    MatchingEngine engine;
    const auto maximum = std::numeric_limits<Quantity>::max();
    engine.submit({1, OrderSide::Sell, OrderType::Limit, maximum, maximum - 1});
    engine.submit({2, OrderSide::Sell, OrderType::Limit, maximum, 1});
    const auto report = engine.submit({3, OrderSide::Buy, OrderType::Limit, maximum, maximum});
    check(report.executed == maximum && report.remaining == 0 && engine.open_order_count() == 0,
          "integer boundaries without notional multiplication or aggregate overflow");
}

// Intentionally slow independent oracle: scan a flat arrival-ordered vector for
// each next maker. No price maps, order indices or FIFO lists from the engine.
struct ReferenceBook {
    std::vector<RestingOrder> open;

    bool cancel(OrderId id) {
        const auto found = std::find_if(open.begin(), open.end(),
                                       [id](const auto& order) { return order.id == id; });
        if (found == open.end()) { return false; }
        open.erase(found);
        return true;
    }

    ExecutionReport submit(const Order& order) {
        ExecutionReport result{order.id, OrderStatus::Filled, 0, order.quantity, {}};
        while (result.remaining > 0) {
            auto best = open.end();
            for (auto it = open.begin(); it != open.end(); ++it) {
                if (it->side == order.side) { continue; }
                const bool buy = order.side == OrderSide::Buy;
                if (order.type == OrderType::Limit &&
                    (buy ? it->price > order.price : it->price < order.price)) { continue; }
                if (best == open.end() || (buy ? it->price < best->price : it->price > best->price)) {
                    best = it;
                }
            }
            if (best == open.end()) { break; }
            const auto quantity = std::min(result.remaining, best->remaining);
            result.trades.push_back({best->id, order.id, order.side, best->price, quantity});
            result.executed += quantity;
            result.remaining -= quantity;
            best->remaining -= quantity;
            if (best->remaining == 0) { open.erase(best); }
        }
        if (result.remaining > 0) {
            result.status = order.type == OrderType::Limit ? OrderStatus::Resting : OrderStatus::Expired;
            if (order.type == OrderType::Limit) {
                open.push_back({order.id, order.side, order.price, result.remaining});
            }
        }
        return result;
    }
};

void reference_stream() {
    MatchingEngine first;
    MatchingEngine second;
    ReferenceBook reference;
    std::mt19937 random(20261021);
    for (OrderId id = 1; id <= 10000; ++id) {
        if (random() % 4 == 0) {
            const OrderId target = 1 + random() % id;
            const bool expected = reference.cancel(target);
            check(first.cancel(target) == expected && second.cancel(target) == expected,
                  "reference cancellation");
        } else {
            const auto side = random() % 2 ? OrderSide::Buy : OrderSide::Sell;
            const auto type = random() % 5 ? OrderType::Limit : OrderType::Market;
            const Price price = type == OrderType::Market ? 0 : 95 + random() % 11;
            const Order order{id, side, type, price, 1 + static_cast<Quantity>(random() % 20)};
            const auto expected = reference.submit(order);
            for (MatchingEngine* engine : {&first, &second}) {
                const auto actual = engine->submit(order);
                check(actual.order_id == expected.order_id && actual.status == expected.status &&
                          actual.executed == expected.executed && actual.remaining == expected.remaining &&
                          actual.trades.size() == expected.trades.size(), "reference report");
                Quantity filled = 0;
                for (std::size_t i = 0; i < actual.trades.size(); ++i) {
                    equal_trade(actual.trades[i], expected.trades[i]);
                    check(actual.trades[i].quantity > 0, "positive fills");
                    filled += actual.trades[i].quantity;
                }
                check(filled == actual.executed && filled + actual.remaining == order.quantity,
                      "incoming quantity conservation");
            }
        }
        for (MatchingEngine* engine : {&first, &second}) {
            check(engine->open_order_count() == reference.open.size(), "reference order count");
            for (const auto side : {OrderSide::Buy, OrderSide::Sell}) {
                std::vector<RestingOrder> expected;
                for (const auto& order : reference.open) {
                    if (order.side == side) { expected.push_back(order); }
                }
                std::stable_sort(expected.begin(), expected.end(), [side](const auto& a, const auto& b) {
                    return side == OrderSide::Buy ? a.price > b.price : a.price < b.price;
                });
                const auto actual = engine->orders(side);
                check(actual.size() == expected.size(), "reference side count");
                for (std::size_t i = 0; i < actual.size(); ++i) {
                    check(actual[i].id == expected[i].id && actual[i].price == expected[i].price &&
                              actual[i].side == expected[i].side && actual[i].remaining == expected[i].remaining,
                          "reference remaining book and priority");
                    check(engine->find(actual[i].id)->remaining == actual[i].remaining, "index integrity");
                }
                const auto best = side == OrderSide::Buy ? engine->best_bid() : engine->best_ask();
                check(expected.empty() ? !best : best == expected.front().price, "reference best price");
            }
            if (engine->best_bid() && engine->best_ask()) {
                check(*engine->best_bid() < *engine->best_ask(), "book never crosses or locks");
            }
        }
    }
}

int main() {
    const std::pair<const char*, void (*)()> tests[] = {
        {"matching and market expiry", matching_cases},
        {"cancellation and FIFO priority", cancellation_and_priority},
        {"validation and ID lifecycle", invalid_orders},
        {"integer boundaries", integer_boundaries},
        {"10000-event reference and deterministic replay", reference_stream},
    };
    for (const auto& test : tests) {
        try {
            test.second();
            std::cout << "PASS " << test.first << '\n';
        } catch (const std::exception& error) {
            std::cerr << "FAIL " << test.first << ": " << error.what() << '\n';
            return 1;
        }
    }
}
