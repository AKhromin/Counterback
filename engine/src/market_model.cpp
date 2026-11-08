#include "counterback/market_model.hpp"

#include <algorithm>
#include <limits>

namespace counterback {
const char* validity_name(MarketValidity validity) {
    switch (validity) {
    case MarketValidity::Unanchored: return "unanchored";
    case MarketValidity::Trusted: return "trusted";
    case MarketValidity::HorizonLost: return "horizon_lost";
    case MarketValidity::Invalid: return "invalid";
    case MarketValidity::Empty: return "empty";
    }
    throw std::invalid_argument("invalid market validity");
}

MarketView::MarketView(MarketState state)
    : state_(std::make_shared<const MarketState>(std::move(state))) {}

std::optional<PriceLevel> MarketView::best_bid() const {
    if (!state_->bid_trusted || state_->bids.empty()) return std::nullopt;
    return state_->bids.front();
}
std::optional<PriceLevel> MarketView::best_ask() const {
    if (!state_->ask_trusted || state_->asks.empty()) return std::nullopt;
    return state_->asks.front();
}
std::optional<Quantity> MarketView::quantity_at(OrderSide side, Price price) const {
    if (side != OrderSide::Buy && side != OrderSide::Sell) {
        throw std::invalid_argument("invalid book side");
    }
    if (price <= 0) throw std::invalid_argument("price must be positive");
    const auto boundary = side == OrderSide::Buy ? state_->bid_boundary : state_->ask_boundary;
    if (state_->validity == MarketValidity::Unanchored || state_->validity == MarketValidity::Invalid ||
        (boundary && (side == OrderSide::Buy ? price < *boundary : price > *boundary))) {
        return std::nullopt;
    }
    const auto& levels = side == OrderSide::Buy ? state_->bids : state_->asks;
    for (const auto& level : levels) if (level.price == price) return level.quantity;
    return 0;
}

ExecutionReport MarketModel::submit(const Order&, Timestamp) {
    throw std::logic_error("backend does not support order-level execution; use historical execution policy");
}
bool MarketModel::cancel(OrderId, Timestamp) {
    throw std::logic_error("backend does not support order-level cancellation");
}

ReplayMarket::ReplayMarket(std::istream& input)
    : reader_(input), replay_(reader_.instrument().symbol, reader_.snapshots()) {}

std::optional<MarketEvent> ReplayMarket::next_event() {
    if (pending_) throw std::logic_error("advance pending market event before reading another");
    while (auto depth = reader_.next()) {
        const auto timing = replay_.timing(*depth);
        if (timing.ignored) {
            replay_.apply(*depth); // Counts/provenance only. No state, visibility or replenishment.
            continue;
        }
        const auto time = std::max({prefix_ns_, depth->observation.rx_utc_ns, timing.snapshot_receive_ns});
        pending_ = MarketEvent{reader_.records_read(), time, timing.snapshot_receive_ns, std::move(*depth),
                               timing.replaces_book};
        return pending_;
    }
    return std::nullopt;
}

std::string ReplayMarket::advance(const MarketEvent& event, Timestamp now) {
    if (!pending_ || event.source_id != pending_->source_id || now != pending_->available_at_ns) {
        throw std::logic_error("unexpected replay market dispatch");
    }
    // Apply our owned pending payload, never trust a caller-supplied replacement.
    status_ = replay_.apply(std::get<DepthUpdate>(pending_->payload));
    prefix_ns_ = visible_ns_ = now;
    pending_.reset();
    cached_view_.reset();
    return status_;
}

MarketView ReplayMarket::view() const {
    if (cached_view_) return *cached_view_;
    MarketState state;
    state.instrument = reader_.instrument();
    state.visible_at_ns = visible_ns_;
    if (replay_.book()) {
        const auto& book = *replay_.book();
        state.last_update_id = book.last_update_id();
        state.bid_boundary = book.bid_boundary();
        state.ask_boundary = book.ask_boundary();
        state.bid_trusted = book.bid_trusted();
        state.ask_trusted = book.ask_trusted();
        state.validity = book.top_trusted() ? MarketValidity::Trusted : MarketValidity::HorizonLost;
        for (auto it = book.bids().rbegin(); it != book.bids().rend(); ++it) {
            state.bids.push_back({it->first, it->second});
        }
        for (const auto& level : book.asks()) state.asks.push_back({level.first, level.second});
    } else {
        state.validity = status_ == "gap" || status_ == "structural_error"
            ? MarketValidity::Invalid : MarketValidity::Unanchored;
    }
    cached_view_.emplace(std::move(state));
    return *cached_view_;
}

void ReplayMarket::write_checkpoint(std::ostream& out, bool summary) const {
    replay_.write_checkpoint(out, summary);
}

SyntheticMarket::SyntheticMarket(Instrument instrument, std::vector<SyntheticInput> tape)
    : instrument_(std::move(instrument)), tape_(std::move(tape)) {
    for (std::size_t i = 1; i < tape_.size(); ++i) {
        if (tape_[i].time_ns < tape_[i - 1].time_ns) {
            throw std::invalid_argument("synthetic tape must be sorted by time");
        }
    }
}
std::optional<MarketEvent> SyntheticMarket::next_event() {
    if (pending_) throw std::logic_error("advance pending synthetic event before reading another");
    if (index_ == tape_.size()) return std::nullopt;
    pending_ = true;
    return std::visit([&](const auto& action) {
        return MarketEvent{index_ + 1, tape_[index_].time_ns, 0, action};
    }, tape_[index_].action);
}
std::string SyntheticMarket::advance(const MarketEvent& event, Timestamp now) {
    if (!pending_ || event.source_id != index_ + 1 || now != tape_[index_].time_ns) {
        throw std::logic_error("unexpected synthetic market dispatch");
    }
    const auto& action = tape_[index_].action;
    const std::string status = std::holds_alternative<Order>(action)
        ? (submit(std::get<Order>(action), now), "submitted")
        : (cancel(std::get<CancelRequest>(action).id, now) ? "cancelled" : "cancel_miss");
    ++index_;
    pending_ = false;
    return status;
}
ExecutionReport SyntheticMarket::submit(const Order& order, Timestamp now) {
    if (now < visible_ns_) throw std::invalid_argument("synthetic market cannot rewind");
    auto report = matcher_.submit(order);
    visible_ns_ = now;
    return report;
}
bool SyntheticMarket::cancel(OrderId id, Timestamp now) {
    if (now < visible_ns_) throw std::invalid_argument("synthetic market cannot rewind");
    const bool cancelled = matcher_.cancel(id);
    visible_ns_ = now;
    return cancelled;
}
MarketView SyntheticMarket::view() const {
    MarketState state;
    state.instrument = instrument_;
    state.visible_at_ns = visible_ns_;
    for (const auto side : {OrderSide::Buy, OrderSide::Sell}) {
        auto& levels = side == OrderSide::Buy ? state.bids : state.asks;
        for (const auto& order : matcher_.orders(side)) {
            if (levels.empty() || levels.back().price != order.price) {
                levels.push_back({order.price, order.remaining});
            } else {
                if (order.remaining > std::numeric_limits<Quantity>::max() - levels.back().quantity) {
                    throw std::overflow_error("synthetic aggregate depth overflow");
                }
                levels.back().quantity += order.remaining;
            }
        }
    }
    state.bid_trusted = !state.bids.empty();
    state.ask_trusted = !state.asks.empty();
    state.validity = state.bid_trusted && state.ask_trusted ? MarketValidity::Trusted : MarketValidity::Empty;
    return MarketView(std::move(state));
}
} // namespace counterback
