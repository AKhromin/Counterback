#pragma once

#include "counterback/matching_engine.hpp"
#include "counterback/prepared_replay.hpp"
#include "counterback/replay_engine.hpp"

#include <memory>
#include <variant>

namespace counterback {
using Timestamp = std::uint64_t;

struct MarketCapabilities {
    bool historical_l2 = false;
    bool synthetic_orders = false;
    bool public_trades = false;
};

enum class MarketValidity { Unanchored, Trusted, HorizonLost, Invalid, Empty };
const char* validity_name(MarketValidity validity);

// Values owned by a const shared snapshot. Retaining a view cannot reveal later updates.
struct MarketState {
    Instrument instrument;
    Timestamp visible_at_ns = 0;
    std::optional<std::uint64_t> last_update_id;
    MarketValidity validity = MarketValidity::Unanchored;
    std::optional<Price> bid_boundary, ask_boundary;
    bool bid_trusted = false, ask_trusted = false;
    std::vector<PriceLevel> bids, asks; // Best observed first, including unknown outer levels.
};

class MarketView {
public:
    explicit MarketView(MarketState state);
    const Instrument& instrument() const { return state_->instrument; }
    Timestamp visible_at_ns() const { return state_->visible_at_ns; }
    std::optional<std::uint64_t> last_update_id() const { return state_->last_update_id; }
    MarketValidity validity() const { return state_->validity; }
    bool top_trusted() const { return state_->bid_trusted && state_->ask_trusted; }
    bool bid_trusted() const { return state_->bid_trusted; }
    bool ask_trusted() const { return state_->ask_trusted; }
    std::optional<Price> bid_boundary() const { return state_->bid_boundary; }
    std::optional<Price> ask_boundary() const { return state_->ask_boundary; }
    std::optional<PriceLevel> best_bid() const;
    std::optional<PriceLevel> best_ask() const;
    const std::vector<PriceLevel>& bids() const { return state_->bids; }
    const std::vector<PriceLevel>& asks() const { return state_->asks; }
    // nullopt means unknown/unavailable; zero means observed absent inside known depth.
    std::optional<Quantity> quantity_at(OrderSide side, Price price) const;
private:
    std::shared_ptr<const MarketState> state_;
};

struct CancelRequest { OrderId id; };
using MarketPayload = std::variant<DepthUpdate, Order, CancelRequest>;
struct MarketEvent {
    std::uint64_t source_id;
    Timestamp available_at_ns;
    Timestamp snapshot_receive_ns;
    MarketPayload payload;
};

class MarketModel {
public:
    virtual ~MarketModel() = default;
    virtual MarketCapabilities capabilities() const = 0;
    // At most one pending source event. Reading it must not mutate the visible book.
    virtual std::optional<MarketEvent> next_event() = 0;
    virtual std::string advance(const MarketEvent& event, Timestamp now) = 0;
    virtual MarketView view() const = 0;
    virtual ExecutionReport submit(const Order&, Timestamp);
    virtual bool cancel(OrderId, Timestamp);
};

class ReplayMarket final : public MarketModel {
public:
    explicit ReplayMarket(std::istream& prepared_input);
    MarketCapabilities capabilities() const override { return {true, false, false}; }
    std::optional<MarketEvent> next_event() override;
    std::string advance(const MarketEvent& event, Timestamp now) override;
    MarketView view() const override;
    // Diagnostic reconstruction output, outside the strategy interface.
    void write_checkpoint(std::ostream& out, bool summary = false) const;
    const ReplayStatistics& statistics() const { return replay_.statistics(); }
private:
    PreparedReplayReader reader_;
    ReplayEngine replay_;
    std::optional<MarketEvent> pending_;
    Timestamp visible_ns_ = 0, prefix_ns_ = 0;
    std::string status_ = "unanchored";
    mutable std::optional<MarketView> cached_view_;
};

// Explicit order-level fixture tape. These are synthetic participant orders, not L2.
struct SyntheticInput {
    Timestamp time_ns;
    std::variant<Order, CancelRequest> action;
};
class SyntheticMarket final : public MarketModel {
public:
    SyntheticMarket(Instrument instrument, std::vector<SyntheticInput> tape = {});
    MarketCapabilities capabilities() const override { return {false, true, false}; }
    std::optional<MarketEvent> next_event() override;
    std::string advance(const MarketEvent& event, Timestamp now) override;
    MarketView view() const override;
    ExecutionReport submit(const Order& order, Timestamp now) override;
    bool cancel(OrderId id, Timestamp now) override;
private:
    Instrument instrument_;
    std::vector<SyntheticInput> tape_;
    std::size_t index_ = 0;
    bool pending_ = false;
    Timestamp visible_ns_ = 0;
    MatchingEngine matcher_;
};
} // namespace counterback
