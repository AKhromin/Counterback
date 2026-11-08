#include "counterback/prepared_replay.hpp"
#include "counterback/replay_engine.hpp"

#include <charconv>
#include <fstream>
#include <iostream>
#include <string_view>

using namespace counterback;
int main(int argc, char** argv) {
    try {
        if (argc < 2 || argc > 3) throw BookDataError("usage: counterback_replay INPUT [CHECKPOINT_EVERY]");
        std::uint64_t every = 1;
        if (argc == 3) {
            const std::string_view text(argv[2]);
            const auto result = std::from_chars(text.data(), text.data() + text.size(), every);
            if (result.ec != std::errc{} || result.ptr != text.data() + text.size() || every == 0) {
                throw BookDataError("checkpoint interval must be positive uint64");
            }
        }
        std::ifstream file(argv[1]);
        PreparedReplayReader reader(file);
        ReplayEngine replay(reader.instrument().symbol, reader.snapshots());
        while (auto event = reader.next()) {
            replay.apply(*event);
            if (replay.statistics().events % every == 0) replay.write_checkpoint(std::cout);
        }
        replay.write_checkpoint(std::cout, true);
        return replay.statistics().structural_errors.empty() ? 0 : 2;
    } catch (const std::exception& error) {
        std::cerr << "replay failed: " << error.what() << '\n';
        return 1;
    }
}
