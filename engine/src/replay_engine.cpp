#include "counterback/replay_engine.hpp"

#include <algorithm>
#include <limits>
#include <ostream>
#include <tuple>

namespace counterback {
namespace {
void interval(std::vector<UpdateInterval>& intervals, std::uint64_t first, std::uint64_t last) {
    if (intervals.empty() || (first > intervals.back().second &&
                            first - intervals.back().second > 1)) {
        intervals.emplace_back(first, last);
    } else {
        intervals.back().second = std::max(last, intervals.back().second);
    }
}

void intervals_json(std::ostream& out, const std::vector<UpdateInterval>& intervals) {
    out << '[';
    bool comma = false;
    for (auto [first, last] : intervals) {
        if (comma) {
            out << ',';
        }

        out << '[' << first << ',' << last << ']';
        comma = true;
    }
    out << ']';
}

void level_json(std::ostream& out, std::optional<PriceLevel> level) {
    if (level) {
        out << '[' << level->price << ',' << level->quantity << ']';
    }

    else out << "null";
}

void levels_json(std::ostream& out, const HistoricalBook::Levels& levels, bool reverse) {
    out << '[';
    bool comma = false;
    auto emit = [&](const auto& level) {
        if (comma) {
            out << ',';
        }

        out << '[' << level.first << ',' << level.second << ']';
        comma = true;
    };
    
    if (reverse) {
        for (auto it = levels.rbegin(); it != levels.rend(); ++it) emit(*it);
    } else {
        for (const auto& level : levels) emit(level);
    }

    out << ']';
}
} // namespace

ReplayEngine::ReplayEngine(std::string symbol, std::vector<DepthSnapshot> snapshots)
    : symbol_(std::move(symbol)), snapshots_(std::move(snapshots)) {
    for (const auto& snapshot : snapshots_) {
        if (snapshot.symbol != symbol_) throw BookDataError("snapshot symbol does not match replay");
    }
}

std::string ReplayEngine::apply(const DepthUpdate& event) {
    if (event.symbol != symbol_) {
        throw BookDataError("event symbol does not match replay");
    }

    if (event.first_update_id > event.final_update_id) {
        throw BookDataError("invalid update range");
    }

    if (previous_ && std::tie(event.first_update_id, event.final_update_id, event.observation.rx_utc_ns) <
                         std::tie(previous_->first_update_id, previous_->final_update_id,
                                  previous_->observation.rx_utc_ns)) {
        throw BookDataError("depth stream is not sorted by U, u, rx_utc");
    }

    ++stats_.events;
    
    if (previous_ && event.first_update_id == previous_->first_update_id &&
        event.final_update_id == previous_->final_update_id) {
        if (event.bids != previous_->bids || event.asks != previous_->asks) {
            throw BookDataError("conflicting duplicate depth event");
        }
        previous_ = event;
        ++stats_.duplicates;
        return status_ = "duplicate";
    }

    previous_ = event;
    if (stats_.last_depth_update_id && event.first_update_id > *stats_.last_depth_update_id &&
        event.first_update_id - *stats_.last_depth_update_id > 1) {
        ++stats_.sequence_gaps;
        interval(stats_.gaps, *stats_.last_depth_update_id + 1, event.first_update_id - 1);
        interval(stats_.invalid_intervals, *stats_.last_depth_update_id + 1, event.first_update_id - 1);
    }
    
    stats_.last_depth_update_id = std::max(stats_.last_depth_update_id.value_or(0), event.final_update_id);
    
    const DepthSnapshot* bridge = nullptr;
    
    while (snapshot_index_ < snapshots_.size() &&
           snapshots_[snapshot_index_].last_update_id < event.final_update_id) {
        const auto& candidate = snapshots_[snapshot_index_++];
        
        if (event.first_update_id <= candidate.last_update_id + 1 &&
            candidate.last_update_id + 1 <= event.final_update_id) {
                bridge = &candidate;
        }
    }
    
    if (bridge) {
        try {
            book_.emplace(*bridge);
            ++stats_.segments;
        } catch (const BookDataError& error) {
            book_.reset();
            stats_.structural_errors.emplace_back(error.what());
            interval(stats_.invalid_intervals, event.first_update_id, event.final_update_id);
            return status_ = "structural_error";
        }
    }
    
    if (!book_) {
        ++stats_.unanchored;
        interval(stats_.invalid_intervals, event.first_update_id, event.final_update_id);
        return status_ = "unanchored";
    }

    if (event.final_update_id <= book_->last_update_id()) {
        ++stats_.stale;
        return status_ = "stale";
    }
   
    if (event.first_update_id > book_->last_update_id() &&
        event.first_update_id - book_->last_update_id() > 1) {
        book_.reset();
        interval(stats_.invalid_intervals, event.first_update_id, event.final_update_id);
        return status_ = "gap";
    }
   
    try {
        const auto result = book_->apply(event);
        stats_.unseen_removals += result.unseen_removals;
        stats_.outer_unseen_removals += result.outer_unseen_removals;
    } catch (const BookDataError& error) {
        stats_.structural_errors.push_back("at " + std::to_string(event.first_update_id) + "-" +
                                          std::to_string(event.final_update_id) + ": " + error.what());
        book_.reset();
        interval(stats_.invalid_intervals, event.first_update_id, event.final_update_id);
        return status_ = "structural_error";
    }
    ++stats_.applied;
    interval(book_->top_trusted() ? stats_.trusted_intervals : stats_.invalid_intervals,
             event.first_update_id, event.final_update_id);
    return status_ = "applied";
}

void ReplayEngine::write_checkpoint(std::ostream& out, bool summary) const {
    out << "{\"kind\":\"" << (summary ? "summary" : "checkpoint") << "\",\"event\":" << stats_.events
        << ",\"status\":\"" << status_ << "\",\"last_update_id\":";
    
    if (book_) {
        out << book_->last_update_id(); 
    } else {
        out << "null";
    }

    out << ",\"bid_trusted\":" << (book_ && book_->bid_trusted() ? "true" : "false")
        << ",\"ask_trusted\":" << (book_ && book_->ask_trusted() ? "true" : "false")
        << ",\"bid_boundary\":";
    
    if (book_) {
        out << book_->bid_boundary(); 
    } else {
        out << "null";
    } 
    
    out << ",\"ask_boundary\":";
    
    if (book_) {
        out << book_->ask_boundary(); 
    } else {
        out << "null";
    }
    
    out << ",\"best_bid\":";
    
    level_json(out, book_ ? book_->best_bid() : std::nullopt);
    
    out << ",\"best_ask\":";
    
    level_json(out, book_ ? book_->best_ask() : std::nullopt);
    
    out << ",\"spread_ticks\":";
    
    if (book_ && book_->top_trusted()) {
        out << book_->best_ask()->price - book_->best_bid()->price;
    } else {
        out << "null";
    }
    
    out << ",\"bids\":";
    
    if (book_) {
        levels_json(out, book_->bids(), true); 
    } else {
        out << "[]";
    }
    
    out << ",\"asks\":";
    
    if (book_) {
        levels_json(out, book_->asks(), false); 
    } else {
        out << "[]";
    }
    
    if (summary) {
        out << ",\"applied\":" << stats_.applied << ",\"duplicates\":" << stats_.duplicates
            << ",\"stale\":" << stats_.stale << ",\"unanchored\":" << stats_.unanchored
            << ",\"segments\":" << stats_.segments << ",\"sequence_gaps\":" << stats_.sequence_gaps
            << ",\"unseen_removals\":" << stats_.unseen_removals
            << ",\"outer_unseen_removals\":" << stats_.outer_unseen_removals
            << ",\"last_depth_update_id\":";
        
        if (stats_.last_depth_update_id) {
            out << *stats_.last_depth_update_id; 
        } else {
            out << "null";
        }
        
        out << ",\"trusted_intervals\":"; intervals_json(out, stats_.trusted_intervals);
        out << ",\"invalid_intervals\":"; intervals_json(out, stats_.invalid_intervals);
        out << ",\"gaps\":"; intervals_json(out, stats_.gaps);
        out << ",\"structural_errors\":[";
        
        for (std::size_t i = 0; i < stats_.structural_errors.size(); ++i) {
            if (i) {
                out << ',';
            }
            
            // All BookDataError messages above are fixed strings or integer IDs.
            out << '"' << stats_.structural_errors[i] << '"';
        }
        out << ']';
    }
    out << "}\n";
}
} // namespace counterback
