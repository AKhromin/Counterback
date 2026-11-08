#pragma once

#include "counterback/execution.hpp"

namespace counterback {
struct AccountConfig {
    Money cash_atoms = static_cast<Money>(10000) * 100000000;
    Quantity inventory_lots = 0;
    std::optional<Exact> initial_basis_atoms; // Total gross basis, required for nonzero inventory.
    unsigned quote_decimals = 8;
    std::string fee_rate = "0.001"; // Benchmark assumption, not an exchange/account fee claim.
};
struct PortfolioState {
    Money cash_atoms = 0;
    Quantity inventory_lots = 0;
    Exact basis_atoms;
    Exact realized_gross_atoms;
    Money fees_atoms = 0;
};
struct PortfolioSnapshot {
    PortfolioState state;
    unsigned quote_decimals;
    bool valuation_valid;
    std::optional<Price> mark_bid;
    std::optional<Money> inventory_value_atoms, equity_atoms;
    std::optional<Exact> unrealized_gross_atoms;
};
class AccountConstraintError : public std::invalid_argument {
public:
    using std::invalid_argument::invalid_argument;
};

class Portfolio {
public:
    Portfolio(Instrument instrument, const AccountConfig& config);
    const PortfolioState& state() const { return state_; }
    const MoneyGrid& grid() const { return grid_; }
    const FeeModel& fees() const { return fees_; }
    // Validate the complete order before committing any fill/fee. Replays are errors.
    void apply(const ExecutionOutcome& outcome);
    PortfolioSnapshot snapshot(const MarketView& view) const;
private:
    MoneyGrid grid_;
    FeeModel fees_;
    PortfolioState state_;
    std::set<OrderId> applied_orders_;
    std::set<std::uint64_t> applied_fills_;
};
class ValuationPolicy {
public:
    static PortfolioSnapshot mark(const PortfolioState& state, const MoneyGrid& grid, const MarketView& view);
};
} // namespace counterback
