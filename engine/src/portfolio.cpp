#include "counterback/portfolio.hpp"

#include <limits>

namespace counterback {
Portfolio::Portfolio(Instrument instrument, const AccountConfig& config)
    : grid_(std::move(instrument), config.quote_decimals), fees_(config.fee_rate) {
    if (config.cash_atoms < 0 || config.inventory_lots < 0 ||
        (config.inventory_lots && !config.initial_basis_atoms) ||
        (config.initial_basis_atoms && config.initial_basis_atoms->numerator() < 0) ||
        (!config.inventory_lots && config.initial_basis_atoms && config.initial_basis_atoms->numerator())) {
        throw std::invalid_argument("invalid starting spot balances/basis");
    }
    state_.cash_atoms = config.cash_atoms;
    state_.inventory_lots = config.inventory_lots;
    state_.basis_atoms = config.initial_basis_atoms.value_or(Exact{});
}

void Portfolio::apply(const ExecutionOutcome& outcome) {
    const auto& order = outcome.order;
    if (!order.id || order.quantity <= 0 || order.price != 0 || order.type != OrderType::Market ||
        (order.side != OrderSide::Buy && order.side != OrderSide::Sell) ||
        applied_orders_.count(order.id) || outcome.status == ExecutionStatus::Rejected) {
        throw std::invalid_argument("invalid or repeated account outcome");
    }
    if (order.side == OrderSide::Sell && order.quantity > state_.inventory_lots) {
        throw AccountConstraintError("insufficient_inventory");
    }
    auto next = state_;
    Money cumulative = 0, fee = 0;
    Quantity executed = 0;
    std::set<std::uint64_t> fill_ids;
    for (const auto& fill : outcome.fills) {
        if (!fill.id || applied_fills_.count(fill.id) || !fill_ids.insert(fill.id).second ||
            fill.order_id != order.id || fill.symbol != grid_.instrument().symbol ||
            fill.side != order.side || fill.time_ns != outcome.time_ns ||
            fill.quantity <= 0 || fill.quantity > order.quantity - executed ||
            fill.notional_atoms != grid_.notional(fill.price, fill.quantity)) {
            throw std::invalid_argument("invalid or repeated fill");
        }
        cumulative = checked_add(cumulative, fill.notional_atoms);
        const auto total_fee = fees_.cumulative_fee(cumulative);
        if (fill.fee_atoms != checked_sub(total_fee, fee)) throw std::invalid_argument("incorrect fill fee");
        fee = total_fee;
        executed += fill.quantity;
        if (order.side == OrderSide::Buy) {
            const auto debit = checked_add(fill.notional_atoms, fill.fee_atoms);
            if (debit > next.cash_atoms) throw AccountConstraintError("insufficient_cash");
            if (fill.quantity > std::numeric_limits<Quantity>::max() - next.inventory_lots) {
                throw std::overflow_error("inventory overflow");
            }
            next.cash_atoms = checked_sub(next.cash_atoms, debit);
            next.inventory_lots += fill.quantity;
            next.basis_atoms = next.basis_atoms + Exact(fill.notional_atoms);
        } else {
            const auto sold_basis = next.basis_atoms * Exact(fill.quantity, next.inventory_lots);
            next.realized_gross_atoms = next.realized_gross_atoms + Exact(fill.notional_atoms) - sold_basis;
            next.basis_atoms = next.basis_atoms - sold_basis;
            next.inventory_lots -= fill.quantity;
            next.cash_atoms = checked_add(next.cash_atoms, checked_sub(fill.notional_atoms, fill.fee_atoms));
        }
        next.fees_atoms = checked_add(next.fees_atoms, fill.fee_atoms);
    }
    if (outcome.executed != executed || outcome.remaining != order.quantity - executed ||
        (outcome.remaining == 0 ? outcome.status != ExecutionStatus::Filled : outcome.status != ExecutionStatus::Expired)) {
        throw std::invalid_argument("outcome quantities/status do not reconcile");
    }
    // Domain errors and arithmetic failures above leave the account unchanged.
    applied_orders_.insert(order.id);
    applied_fills_.insert(fill_ids.begin(), fill_ids.end());
    state_ = next;
}

PortfolioSnapshot ValuationPolicy::mark(const PortfolioState& state, const MoneyGrid& grid, const MarketView& view) {
    const auto& instrument = grid.instrument();
    if (instrument.symbol != view.instrument().symbol || instrument.tick_size != view.instrument().tick_size ||
        instrument.lot_size != view.instrument().lot_size) throw std::invalid_argument("valuation instrument mismatch");
    PortfolioSnapshot result{state, grid.quote_decimals(), false, {}, {}, {}, {}};
    if (view.validity() != MarketValidity::Trusted || !view.top_trusted() || !view.best_bid() || !view.best_ask()) return result;
    if (view.best_bid()->price >= view.best_ask()->price) throw BookDataError("crossed valuation book");
    result.mark_bid = view.best_bid()->price;
    result.inventory_value_atoms = state.inventory_lots ? grid.notional(*result.mark_bid, state.inventory_lots) : 0;
    result.equity_atoms = checked_add(state.cash_atoms, *result.inventory_value_atoms);
    result.unrealized_gross_atoms = Exact(*result.inventory_value_atoms) - state.basis_atoms;
    result.valuation_valid = true;
    return result;
}
PortfolioSnapshot Portfolio::snapshot(const MarketView& view) const {
    return ValuationPolicy::mark(state_, grid_, view);
}
} // namespace counterback
