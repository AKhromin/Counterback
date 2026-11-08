#pragma once

#include "counterback/prepared_replay.hpp"

#include <string>

namespace counterback {
// GCC/Clang on the supported Linux/macOS builds. Never narrow ledger values to double/int64.
#if defined(__SIZEOF_INT128__)
__extension__ typedef __int128 Money;
#else
#error "Counterback accounting requires checked 128-bit integer support"
#endif

Money checked_add(Money a, Money b);
Money checked_sub(Money a, Money b);
Money checked_mul(Money a, Money b);
std::string money_string(Money value);
Money parse_money(const std::string& value); // Nonnegative integer quote atoms.

// Reduced, checked rational for acquisition basis and P&L attribution, never cash rounding.
class Exact {
public:
    Exact() = default;
    Exact(Money n, Money d = 1);
    Money numerator() const { return numerator_; }
    Money denominator() const { return denominator_; }
    static Exact decimal(const std::string& value);
    Exact operator+(const Exact& other) const;
    Exact operator-(const Exact& other) const;
    Exact operator*(const Exact& other) const;
    Exact operator/(const Exact& other) const;
    bool operator==(const Exact& other) const;
private:
    Money numerator_ = 0;
    Money denominator_ = 1;
};

class MoneyGrid {
public:
    MoneyGrid(Instrument instrument, unsigned quote_decimals = 8);
    Money notional(Price price, Quantity quantity) const;
    const Instrument& instrument() const { return instrument_; }
    unsigned quote_decimals() const { return decimals_; }
private:
    Instrument instrument_;
    unsigned decimals_;
    Exact atoms_per_tick_lot_;
};

class FeeModel {
public:
    explicit FeeModel(const std::string& rate = "0.001");
    Money cumulative_fee(Money notional) const;
    const Exact& rate() const { return rate_; }
private:
    Exact rate_;
};
} // namespace counterback
