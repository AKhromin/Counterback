#include "counterback/matching_engine.hpp"

#include <iostream>

int main() {
    using namespace counterback;
    
    MatchingEngine engine;
    engine.submit({1, OrderSide::Sell, OrderType::Limit, 100, 3});
    engine.submit({2, OrderSide::Sell, OrderType::Limit, 101, 4});
    
    const auto report = engine.submit({3, OrderSide::Buy, OrderType::Market, 0, 5});
    
    for (const auto& trade : report.trades) {
        std::cout << "maker=" << trade.maker_id << " taker=" << trade.taker_id
                  << " price_ticks=" << trade.price << " quantity_lots=" << trade.quantity << '\n';
    }
    
    std::cout << "executed_lots=" << report.executed << " unfilled_lots=" << report.remaining
              << " open_orders=" << engine.open_order_count() << '\n';
}
