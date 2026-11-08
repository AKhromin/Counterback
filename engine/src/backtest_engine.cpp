#include "counterback/backtest_engine.hpp"

#include <ostream>

namespace counterback {
Timestamp delayed_time_ns(Timestamp time, Timestamp delay) {
    if (delay > std::numeric_limits<Timestamp>::max() - time) {
        throw std::overflow_error("timestamp overflow");
    }
    return time + delay;
}

ScheduleKey schedule_key(Timestamp time, EventPriority priority, std::uint64_t sequence,
                         std::optional<ScheduleKey> current) {
    if (priority > EventPriority::End) throw std::invalid_argument("invalid event priority");
    std::uint64_t round = 0;
    if (current) {
        if (time < current->time_ns) throw std::invalid_argument("cannot schedule into the past");
        if (sequence <= current->insertion_sequence) {
            throw std::invalid_argument("insertion sequence must increase");
        }
        if (time == current->time_ns) {
            round = current->causal_round;
            if (priority <= current->priority) {
                if (round == std::numeric_limits<std::uint64_t>::max()) {
                    throw std::overflow_error("causal round overflow");
                }
                ++round;
            }
        }
    }
    return {time, round, priority, sequence};
}
void SimulationClock::advance_to(Timestamp time) {
    if (time < now_) throw std::invalid_argument("simulation clock cannot rewind");
    now_ = time;
}

std::uint64_t EventQueue::push(Timestamp time, EventPriority priority, EventPayload payload) {
    if (sequence_ == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("event ID overflow");
    }
    const auto key = schedule_key(time, priority, sequence_ + 1, current_);
    const auto parent = current_ ? std::make_optional(current_->insertion_sequence) : std::nullopt;
    queue_.push({key.insertion_sequence, parent, key, std::move(payload)});
    return ++sequence_;
}
std::uint64_t EventQueue::push_market(MarketEvent event) {
    if (sequence_ == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("event ID overflow");
    }
    const ScheduleKey key{event.available_at_ns, 0, EventPriority::Market, sequence_ + 1};
    if (current_ && key < *current_) throw std::invalid_argument("market tape scheduled into the past");
    queue_.push({key.insertion_sequence, std::nullopt, key, std::move(event)});
    return ++sequence_;
}
EventEnvelope EventQueue::pop_next() {
    if (empty()) throw std::out_of_range("empty event queue");
    auto event = queue_.top();
    queue_.pop();
    current_ = event.key;
    return event;
}

namespace {
void optional_number(std::ostream& out, std::optional<std::uint64_t> value) {
    if (value) out << *value; else out << "null";
}
void price(std::ostream& out, std::optional<Price> value) {
    if (value) out << *value; else out << "null";
}
void level(std::ostream& out, std::optional<PriceLevel> value) {
    if (value) out << '[' << value->price << ',' << value->quantity << ']'; else out << "null";
}
// Identity strings from files are untrusted; emit valid JSON even for control bytes.
void json_string(std::ostream& out, const std::string& value) {
    out << '"';
    for (const unsigned char ch : value) {
        if (ch == '"' || ch == '\\') out << '\\' << ch;
        else if (ch < 0x20 || ch >= 0x7f) {
            const char* hex = "0123456789abcdef";
            out << "\\u00" << hex[ch >> 4] << hex[ch & 15];
        } else out << ch;
    }
    out << '"';
}
const char* event_kind(const EventEnvelope& event) {
    if (std::holds_alternative<MarketEvent>(event.payload)) return "market";
    if (std::holds_alternative<Order>(event.payload)) return "order_arrival";
    if (std::holds_alternative<CancelRequest>(event.payload)) return "cancel_arrival";
    if (std::holds_alternative<EndEvent>(event.payload)) return "end";
    if (std::holds_alternative<StartEvent>(event.payload)) return "start";
    return "callback";
}
void atoms(std::ostream& out, Money value) { out << '"' << money_string(value) << '"'; }
void exact(std::ostream& out, const Exact& value) {
    out << "{\"numerator\":"; atoms(out, value.numerator());
    out << ",\"denominator\":"; atoms(out, value.denominator()); out << '}';
}
void outcome_json(std::ostream& out, const ExecutionOutcome& outcome) {
    const auto& order = outcome.order;
    out << "{\"order_id\":" << order.id << ",\"side\":";
    json_string(out, order.side == OrderSide::Buy ? "buy" : order.side == OrderSide::Sell ? "sell" : "invalid");
    out << ",\"order_type\":";
    json_string(out, order.type == OrderType::Market ? "market" : order.type == OrderType::Limit ? "limit" : "invalid");
    out << ",\"price_ticks\":" << order.price << ",\"requested_lots\":" << order.quantity
        << ",\"time_ns\":" << outcome.time_ns << ",\"status\":\"" << execution_status_name(outcome.status)
        << "\",\"reason\":";
    json_string(out, outcome.reason);
    out << ",\"executed_lots\":" << outcome.executed << ",\"remaining_lots\":" << outcome.remaining
        << ",\"fills\":[";
    bool comma = false;
    for (const auto& fill : outcome.fills) {
        if (comma) out << ',';
        comma = true;
        out << "{\"fill_id\":" << fill.id << ",\"order_id\":" << fill.order_id
            << ",\"time_ns\":" << fill.time_ns << ",\"price_ticks\":" << fill.price
            << ",\"quantity_lots\":" << fill.quantity << ",\"notional_atoms\":";
        atoms(out, fill.notional_atoms);
        out << ",\"fee_atoms\":"; atoms(out, fill.fee_atoms);
        out << ",\"symbol\":"; json_string(out, fill.symbol);
        out << ",\"side\":\"" << (fill.side == OrderSide::Buy ? "buy" : "sell")
            << "\",\"liquidity_role\":\"modeled_taker\",\"model\":\"historical_visible_depth_v1\"}";
    }
    out << "]}";
}
void account_json(std::ostream& out, const PortfolioSnapshot& account) {
    out << "{\"quote_decimals\":" << account.quote_decimals << ",\"cash_atoms\":";
    atoms(out, account.state.cash_atoms);
    out << ",\"inventory_lots\":" << account.state.inventory_lots << ",\"basis_atoms\":";
    exact(out, account.state.basis_atoms);
    out << ",\"realized_gross_atoms\":"; exact(out, account.state.realized_gross_atoms);
    out << ",\"fees_atoms\":"; atoms(out, account.state.fees_atoms);
    out << ",\"valuation_valid\":" << (account.valuation_valid ? "true" : "false") << ",\"mark_bid_ticks\":";
    price(out, account.mark_bid);
    out << ",\"inventory_value_atoms\":";
    if (account.inventory_value_atoms) atoms(out, *account.inventory_value_atoms); else out << "null";
    out << ",\"equity_atoms\":";
    if (account.equity_atoms) atoms(out, *account.equity_atoms); else out << "null";
    out << ",\"unrealized_gross_atoms\":";
    if (account.unrealized_gross_atoms) exact(out, *account.unrealized_gross_atoms); else out << "null";
    out << '}';
}
} // namespace

void write_journal_record(std::ostream& out, const JournalRecord& record) {
    const auto& event = record.event;
    out << "{\"kind\":\"runtime_event\",\"schema_version\":1,\"event_id\":" << event.id << ",\"parent_id\":";
    optional_number(out, event.parent_id);
    out << ",\"time_ns\":" << event.key.time_ns << ",\"causal_round\":" << event.key.causal_round
        << ",\"priority\":" << static_cast<unsigned>(event.key.priority)
        << ",\"insertion_sequence\":" << event.key.insertion_sequence << ",\"event_kind\":\""
        << event_kind(event) << "\",\"status\":";
    json_string(out, record.status);
    out << ",\"ready\":" << (record.ready ? "true" : "false") << ",\"source_id\":";
    const auto* market = std::get_if<MarketEvent>(&event.payload);
    optional_number(out, market ? std::make_optional(market->source_id) : std::nullopt);
    if (market) {
        out << ",\"snapshot_receive_ns\":" << market->snapshot_receive_ns
            << ",\"replaces_book\":" << (market->replaces_book ? "true" : "false");
        if (const auto* depth = std::get_if<DepthUpdate>(&market->payload)) {
            out << ",\"first_update_id\":" << depth->first_update_id
                << ",\"final_update_id\":" << depth->final_update_id
                << ",\"receive_ns\":" << depth->observation.rx_utc_ns
                << ",\"receive_mono_ns\":" << depth->observation.rx_mono_ns
                << ",\"exchange_time_ms\":" << depth->observation.exchange_time_ms << ",\"recorder_id\":";
            json_string(out, depth->observation.recorder_id);
            out << ",\"run_id\":";
            json_string(out, depth->observation.run_id);
        }
    }
    out << ",\"symbol\":";
    json_string(out, record.view.instrument().symbol);
    out << ",\"visible_at_ns\":" << record.view.visible_at_ns() << ",\"last_update_id\":";
    optional_number(out, record.view.last_update_id());
    out << ",\"validity\":\"" << validity_name(record.view.validity())
        << "\",\"bid_trusted\":" << (record.view.bid_trusted() ? "true" : "false")
        << ",\"ask_trusted\":" << (record.view.ask_trusted() ? "true" : "false")
        << ",\"bid_boundary\":";
    price(out, record.view.bid_boundary());
    out << ",\"ask_boundary\":";
    price(out, record.view.ask_boundary());
    out << ",\"best_bid\":";
    level(out, record.view.best_bid());
    out << ",\"best_ask\":";
    level(out, record.view.best_ask());
    if (record.execution) {
        const auto status = record.execution->status == OrderStatus::Filled ? "filled" :
                            record.execution->status == OrderStatus::Resting ? "resting" : "expired";
        out << ",\"order_id\":" << record.execution->order_id
            << ",\"execution_status\":\"" << status << '"'
            << ",\"executed_lots\":" << record.execution->executed
            << ",\"remaining_lots\":" << record.execution->remaining;
    }
    if (const auto* order = std::get_if<Order>(&event.payload)) {
        out << ",\"request\":{\"order_id\":" << order->id << ",\"price_ticks\":" << order->price
            << ",\"quantity_lots\":" << order->quantity << '}';
    }
    if (record.outcome) { out << ",\"outcome\":"; outcome_json(out, *record.outcome); }
    if (record.account) { out << ",\"account\":"; account_json(out, *record.account); }
    if (!record.terminated_orders.empty()) {
        out << ",\"terminated_orders\":[";
        bool comma = false;
        for (const auto& outcome : record.terminated_orders) {
            if (comma) out << ',';
            comma = true;
            outcome_json(out, outcome);
        }
        out << ']';
    }
    if (record.validity_from) {
        out << ",\"validity_transition\":{\"from\":\"" << validity_name(*record.validity_from)
            << "\",\"to\":\"" << validity_name(record.view.validity()) << "\"}";
    }
    out << "}\n";
}

BacktestEngine::BacktestEngine(MarketModel& market, RuntimeConfig config, JournalSink journal)
    : market_(market), config_(config), journal_(std::move(journal)) {
    if (config_.start_ns >= config_.end_ns) throw std::invalid_argument("run requires start < end");
    if (config_.account) {
        if (!market_.capabilities().historical_l2) throw std::invalid_argument("WP3 accounting requires historical L2");
        portfolio_.emplace(market_.view().instrument(), *config_.account);
    }
}
const Portfolio& BacktestEngine::portfolio() const {
    if (!portfolio_) throw std::logic_error("accounting is not enabled for this run");
    return *portfolio_;
}
void BacktestEngine::prime_market() {
    auto next = market_.next_event();
    if (next && next->available_at_ns < config_.end_ns) queue_.push_market(std::move(*next));
}
std::uint64_t BacktestEngine::schedule(Timestamp time, EventPriority priority, RuntimeCallback callback) {
    if (finished_) throw std::logic_error("run has finished");
    if (!callback || priority == EventPriority::Market || priority == EventPriority::End) {
        throw std::invalid_argument("invalid scheduled callback");
    }
    if (time >= config_.end_ns) throw std::invalid_argument("callback outside half-open run interval");
    return queue_.push(time, priority, std::move(callback));
}
std::uint64_t BacktestEngine::submit_after(Order order, Timestamp delay) {
    if (finished_ || !ready_) throw std::logic_error("order submission requires an active trusted runtime");
    if (!market_.capabilities().synthetic_orders && !portfolio_) {
        throw std::logic_error("historical orders require an explicit account configuration");
    }
    const auto id = queue_.push(delayed_time_ns(now(), delay), EventPriority::OrderArrival, order);
    if (portfolio_) pending_orders_.emplace(id, order);
    return id;
}
std::uint64_t BacktestEngine::cancel_after(OrderId id, Timestamp delay) {
    if (finished_ || !ready_) throw std::logic_error("cancellation requires an active trusted runtime");
    if (!market_.capabilities().synthetic_orders) throw std::logic_error("unsupported backend cancellation");
    return queue_.push(delayed_time_ns(now(), delay), EventPriority::CancelArrival, CancelRequest{id});
}

RuntimeResult BacktestEngine::run(RuntimeCallback strategy) {
    if (used_) throw std::logic_error("BacktestEngine is single-use; construct fresh state for each run");
    used_ = true;
    try {
        queue_.push(config_.end_ns, EventPriority::End, EndEvent{});
        queue_.push(config_.start_ns, EventPriority::Strategy, StartEvent{});
        prime_market();
        while (!queue_.empty()) {
            auto event = queue_.pop_next();
            clock_.advance_to(event.key.time_ns);
            std::string status;
            std::optional<ExecutionReport> execution;
            std::optional<ExecutionOutcome> outcome;
            std::vector<ExecutionOutcome> terminated;
            const auto previous_validity = market_.view().validity();
            bool market_tick = false;
            ready_ = now() >= config_.start_ns && now() < config_.end_ns &&
                     market_.view().top_trusted() && trusted_updates_ >= config_.warmup_trusted_updates;
            if (!std::holds_alternative<MarketEvent>(event.payload)) {
                result_.ever_ready = result_.ever_ready || ready_;
            }
            if (now() >= config_.end_ns && !std::holds_alternative<EndEvent>(event.payload)) {
                status = "expired_at_end";
                if (portfolio_ && std::holds_alternative<Order>(event.payload)) {
                    outcome = historical_execution_.terminate(std::get<Order>(event.payload), now(), status);
                    pending_orders_.erase(event.id);
                }
            } else if (std::holds_alternative<StartEvent>(event.payload)) {
                status = "start";
            } else if (std::holds_alternative<EndEvent>(event.payload)) {
                ready_ = false;
                status = "end";
                finished_ = true;
                result_.reason = result_.ever_ready ? "end" : "no_trusted_ready_state";
            } else if (const auto* market = std::get_if<MarketEvent>(&event.payload)) {
                status = market_.advance(*market, now());
                if (portfolio_) historical_execution_.observe(*market, status);
                market_tick = true;
                if (status == "applied" || status == "submitted" || status == "cancelled") {
                    ++result_.applied_updates;
                    if (market_.view().top_trusted()) ++trusted_updates_;
                }
            } else if (const auto* order = std::get_if<Order>(&event.payload)) {
                if (portfolio_) {
                    pending_orders_.erase(event.id);
                    outcome = historical_execution_.execute(*order, now(), market_.view(), *portfolio_);
                    status = execution_status_name(outcome->status);
                } else {
                    execution = market_.submit(*order, now());
                    status = "submitted";
                }
            } else if (const auto* cancel = std::get_if<CancelRequest>(&event.payload)) {
                status = market_.cancel(cancel->id, now()) ? "cancelled" : "cancel_miss";
            } else {
                auto callback = std::get<RuntimeCallback>(event.payload);
                callback(*this, market_.view());
                if (event.key.priority == EventPriority::Strategy) ++result_.strategy_callbacks;
                status = "callback";
            }
            const auto current_view = market_.view();
            if (!finished_) {
                // The historical baseline halts permanently after its first trading-ready state.
                if (market_.capabilities().historical_l2 && result_.ever_ready && !current_view.top_trusted()) {
                    result_.halted = true;
                    result_.reason = "required_market_trust_lost";
                    ready_ = false;
                    finished_ = true;
                } else {
                    ready_ = now() >= config_.start_ns && now() < config_.end_ns && current_view.top_trusted() &&
                             trusted_updates_ >= config_.warmup_trusted_updates;
                    result_.ever_ready = result_.ever_ready || ready_;
                }
            }
            if (finished_ && portfolio_) {
                for (const auto& pending : pending_orders_) {
                    terminated.push_back(historical_execution_.terminate(pending.second, now(),
                                          result_.halted ? "expired_on_halt" : "expired_at_end"));
                }
                pending_orders_.clear();
            }
            ++result_.dispatched;
            if (portfolio_) result_.account = portfolio_->snapshot(current_view);
            if (journal_) {
                journal_({event, status, current_view, ready_, execution, outcome,
                          result_.account,
                          std::move(terminated), previous_validity == current_view.validity() ? std::nullopt :
                                                std::make_optional(previous_validity)});
            }
            if (finished_) break;
            if (market_tick) {
                // All same-time source updates are queued in round zero ahead of callbacks.
                if (strategy && status != "unanchored" && status != "gap" && status != "structural_error") {
                    schedule(now(), EventPriority::Strategy, strategy);
                }
                prime_market();
            }
        }
        result_.stopped_at_ns = now();
        result_.pending_events = queue_.size();
        finished_ = true;
        return result_;
    } catch (...) {
        finished_ = true;
        ready_ = false;
        throw;
    }
}
} // namespace counterback
