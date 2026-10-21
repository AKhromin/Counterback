#pragma once

#include "counterback/historical_book.hpp"

#include <iosfwd>
#include <utility>

namespace counterback {
using UpdateInterval = std::pair<std::uint64_t, std::uint64_t>;

struct ReplayStatistics {
    std::uint64_t events = 0, applied = 0, duplicates = 0, stale = 0, unanchored = 0;
    std::uint64_t segments = 0, sequence_gaps = 0;
    std::uint64_t unseen_removals = 0, outer_unseen_removals = 0;
    std::optional<std::uint64_t> last_depth_update_id;
    std::vector<UpdateInterval> trusted_intervals, invalid_intervals, gaps;
    std::vector<std::string> structural_errors;
};

class ReplayEngine {
public:
    // Snapshot order is the Python verifier's source-list order; see replay docs.
    ReplayEngine(std::string symbol, std::vector<DepthSnapshot> snapshots);
    std::string apply(const DepthUpdate& event);
    
    const std::optional<HistoricalBook>& book() const { 
        return book_; 
    }
    
    const ReplayStatistics& statistics() const { 
        return stats_; 
    }
    
    // Stable JSON, full observed depth, best prices and snapshot-horizon validity.
    void write_checkpoint(std::ostream& out, bool summary = false) const;

private:
    std::string symbol_, status_ = "unanchored";
    std::vector<DepthSnapshot> snapshots_;
    std::size_t snapshot_index_ = 0;
    std::optional<DepthUpdate> previous_;
    std::optional<HistoricalBook> book_;
    ReplayStatistics stats_;
};
} // namespace counterback
