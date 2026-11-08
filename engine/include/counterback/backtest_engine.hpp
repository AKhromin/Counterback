#pragma once

#include "counterback/market_model.hpp"

#include <functional>
#include <limits>
#include <queue>
#include <tuple>

namespace counterback {
enum class EventPriority : unsigned {
    Market = 0, OrderArrival = 1, CancelArrival = 2, Notification = 3,
    Strategy = 4, Valuation = 5, End = 6
};

struct ScheduleKey {
    Timestamp time_ns;
    std::uint64_t causal_round;
    EventPriority priority;
    std::uint64_t insertion_sequence;
    bool operator<(const ScheduleKey& other) const {
        return std::tie(time_ns, causal_round, priority, insertion_sequence) <
               std::tie(other.time_ns, other.causal_round, other.priority, other.insertion_sequence);
    }
};
Timestamp delayed_time_ns(Timestamp time, Timestamp delay);
ScheduleKey schedule_key(Timestamp time, EventPriority priority, std::uint64_t sequence,
                         std::optional<ScheduleKey> current = std::nullopt);

class SimulationClock {
public:
    Timestamp now() const { return now_; }
    void advance_to(Timestamp time);
private:
    Timestamp now_ = 0;
};

class BacktestEngine;
using RuntimeCallback = std::function<void(BacktestEngine&, const MarketView&)>;
struct EndEvent {};
struct StartEvent {};
using EventPayload = std::variant<MarketEvent, Order, CancelRequest, RuntimeCallback, StartEvent, EndEvent>;

struct EventEnvelope {
    std::uint64_t id;
    std::optional<std::uint64_t> parent_id;
    ScheduleKey key;
    EventPayload payload;
};

class EventQueue {
public:
    // Child scheduling follows WP1 causal rounds; IDs equal global insertion sequence.
    std::uint64_t push(Timestamp time, EventPriority priority, EventPayload payload);
    // Only exogenous source events may bypass child rounds, preserving market tape ties.
    std::uint64_t push_market(MarketEvent event);
    EventEnvelope pop_next();
    bool empty() const { return queue_.empty(); }
    std::size_t size() const { return queue_.size(); }
    const std::optional<ScheduleKey>& current() const { return current_; }
private:
    struct Later {
        bool operator()(const EventEnvelope& a, const EventEnvelope& b) const { return b.key < a.key; }
    };
    std::priority_queue<EventEnvelope, std::vector<EventEnvelope>, Later> queue_;
    std::optional<ScheduleKey> current_;
    std::uint64_t sequence_ = 0;
};

struct RuntimeConfig {
    Timestamp start_ns = 0;
    Timestamp end_ns = std::numeric_limits<Timestamp>::max(); // Exclusive.
    std::uint64_t warmup_trusted_updates = 0;
};

struct JournalRecord {
    EventEnvelope event;
    std::string status;
    MarketView view;
    bool ready;
    std::optional<ExecutionReport> execution;
};
void write_journal_record(std::ostream& out, const JournalRecord& record);
using JournalSink = std::function<void(const JournalRecord&)>;

struct RuntimeResult {
    bool halted = false;
    bool ever_ready = false;
    Timestamp stopped_at_ns = 0;
    std::uint64_t dispatched = 0, applied_updates = 0, strategy_callbacks = 0;
    std::size_t pending_events = 0;
    std::string reason;
};

// One engine/backend instance per run. Accounting and historical order execution are WP3.
class BacktestEngine {
public:
    BacktestEngine(MarketModel& market, RuntimeConfig config, JournalSink journal = {});
    RuntimeResult run(RuntimeCallback strategy = {});
    Timestamp now() const { return clock_.now(); }
    MarketView view() const { return market_.view(); }
    bool ready() const { return ready_; }
    const RuntimeResult& result() const { return result_; }
    // Timers/callbacks are useful for scripted causality fixtures before Python bindings.
    std::uint64_t schedule(Timestamp time, EventPriority priority, RuntimeCallback callback);
    std::uint64_t submit_after(Order order, Timestamp delay_ns = 0);
    std::uint64_t cancel_after(OrderId id, Timestamp delay_ns = 0);
private:
    void prime_market();
    MarketModel& market_;
    RuntimeConfig config_;
    JournalSink journal_;
    EventQueue queue_;
    SimulationClock clock_;
    RuntimeResult result_;
    bool used_ = false, finished_ = false, ready_ = false;
    std::uint64_t trusted_updates_ = 0;
};
} // namespace counterback
