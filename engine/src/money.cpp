#include "counterback/money.hpp"

#include <algorithm>
#include <limits>

namespace counterback {
namespace {
__extension__ typedef unsigned __int128 Unsigned;
Money magnitude(Money value) { return value < 0 ? checked_sub(0, value) : value; }
Money gcd(Money a, Money b) {
    a = magnitude(a); b = magnitude(b);
    while (b) { const auto r = a % b; a = b; b = r; }
    return a;
}
Money power10(unsigned count) {
    Money result = 1;
    for (unsigned i = 0; i < count; ++i) result = checked_mul(result, 10);
    return result;
}
} // namespace

Money checked_add(Money a, Money b) {
    Money result;
    if (__builtin_add_overflow(a, b, &result)) throw std::overflow_error("quote arithmetic overflow");
    return result;
}
Money checked_sub(Money a, Money b) {
    Money result;
    if (__builtin_sub_overflow(a, b, &result)) throw std::overflow_error("quote arithmetic overflow");
    return result;
}
Money checked_mul(Money a, Money b) {
    Money result;
    if (__builtin_mul_overflow(a, b, &result)) throw std::overflow_error("quote arithmetic overflow");
    return result;
}
std::string money_string(Money value) {
    const bool negative = value < 0;
    Unsigned rest = negative ? static_cast<Unsigned>(-(value + 1)) + 1 : static_cast<Unsigned>(value);
    std::string result;
    do { result.push_back(static_cast<char>('0' + rest % 10)); rest /= 10; } while (rest);
    if (negative) result.push_back('-');
    std::reverse(result.begin(), result.end());
    return result;
}
Money parse_money(const std::string& value) {
    if (value.empty()) throw std::invalid_argument("missing quote atoms");
    Money result = 0;
    for (char ch : value) {
        if (ch < '0' || ch > '9') throw std::invalid_argument("quote atoms must be nonnegative integers");
        result = checked_add(checked_mul(result, 10), ch - '0');
    }
    return result;
}

Exact::Exact(Money n, Money d) {
    if (d <= 0) throw std::invalid_argument("exact denominator must be positive");
    const auto factor = gcd(n, d);
    numerator_ = n / factor;
    denominator_ = d / factor;
}
Exact Exact::decimal(const std::string& text) {
    // Parse finite decimals (including exponent spelling) without binary floating point.
    if (text.empty()) throw std::invalid_argument("missing decimal");
    std::size_t i = 0;
    const bool negative = text[i] == '-';
    if (text[i] == '+' || negative) ++i;
    std::string digits;
    bool dot = false, seen = false;
    int scale = 0;
    for (; i < text.size() && text[i] != 'e' && text[i] != 'E'; ++i) {
        const char ch = text[i];
        if (ch == '.' && !dot) { dot = true; continue; }
        if (ch < '0' || ch > '9') throw std::invalid_argument("invalid finite decimal");
        seen = true;
        digits += ch;
        if (dot && ++scale > 1000) throw std::overflow_error("decimal precision overflow");
    }
    if (!seen) throw std::invalid_argument("invalid finite decimal");
    if (i < text.size()) {
        ++i;
        bool minus = false;
        if (i < text.size() && (text[i] == '-' || text[i] == '+')) minus = text[i++] == '-';
        if (i == text.size()) throw std::invalid_argument("missing decimal exponent");
        int exponent = 0;
        for (; i < text.size(); ++i) {
            if (text[i] < '0' || text[i] > '9') throw std::invalid_argument("invalid decimal exponent");
            exponent = exponent * 10 + text[i] - '0';
            if (exponent > 1000) throw std::overflow_error("decimal exponent overflow");
        }
        scale += minus ? exponent : -exponent;
    }
    const auto first = digits.find_first_not_of('0');
    if (first == std::string::npos) return {};
    digits.erase(0, first);
    while (!digits.empty() && digits.back() == '0') { digits.pop_back(); --scale; }
    Money n = parse_money(digits);
    if (scale < 0) n = checked_mul(n, power10(static_cast<unsigned>(-scale)));
    return Exact(negative ? checked_sub(0, n) : n,
                 scale > 0 ? power10(static_cast<unsigned>(scale)) : 1);
}
Exact Exact::operator+(const Exact& other) const {
    const auto common = gcd(denominator_, other.denominator_);
    return Exact(checked_add(checked_mul(numerator_, other.denominator_ / common),
                             checked_mul(other.numerator_, denominator_ / common)),
                 checked_mul(denominator_, other.denominator_ / common));
}
Exact Exact::operator-(const Exact& other) const {
    return *this + Exact(checked_sub(0, other.numerator_), other.denominator_);
}
Exact Exact::operator*(const Exact& other) const {
    const auto left = gcd(numerator_, other.denominator_);
    const auto right = gcd(other.numerator_, denominator_);
    return Exact(checked_mul(numerator_ / left, other.numerator_ / right),
                 checked_mul(denominator_ / right, other.denominator_ / left));
}
Exact Exact::operator/(const Exact& other) const {
    if (!other.numerator_) throw std::invalid_argument("exact division by zero");
    return *this * Exact(other.numerator_ < 0 ? -other.denominator_ : other.denominator_,
                         magnitude(other.numerator_));
}
bool Exact::operator==(const Exact& other) const {
    return numerator_ == other.numerator_ && denominator_ == other.denominator_;
}

MoneyGrid::MoneyGrid(Instrument instrument, unsigned decimals)
    : instrument_(std::move(instrument)), decimals_(decimals) {
    if (decimals > 18 || instrument_.symbol.empty()) throw std::invalid_argument("invalid money grid");
    const auto tick = Exact::decimal(instrument_.tick_size), lot = Exact::decimal(instrument_.lot_size);
    if (tick.numerator() <= 0 || lot.numerator() <= 0) throw std::invalid_argument("instrument steps must be positive");
    atoms_per_tick_lot_ = tick * lot * Exact(power10(decimals));
}
Money MoneyGrid::notional(Price price, Quantity quantity) const {
    if (price <= 0 || quantity <= 0) throw std::invalid_argument("notional requires positive ticks/lots");
    const auto value = atoms_per_tick_lot_ * Exact(price) * Exact(quantity);
    if (value.denominator() != 1) throw std::invalid_argument("notional is not exact in quote atoms");
    return value.numerator();
}
FeeModel::FeeModel(const std::string& rate) : rate_(Exact::decimal(rate)) {
    if (rate_.numerator() < 0 || rate_.numerator() > rate_.denominator()) {
        throw std::invalid_argument("fee rate must be between zero and one");
    }
}
Money FeeModel::cumulative_fee(Money notional) const {
    if (notional < 0) throw std::invalid_argument("negative fee notional");
    Money product;
    if (!__builtin_mul_overflow(notional, rate_.numerator(), &product)) {
        return checked_add(product / rate_.denominator(), product % rate_.denominator() != 0);
    }
    // Exact multiply/divide fallback. Quotient stays <= notional; unsigned remainders
    // use at most 128 bits, even when the naive signed product would overflow.
    Unsigned quotient = 0, remainder = 0;
    const auto divisor = static_cast<Unsigned>(rate_.denominator());
    for (int bit = 126; bit >= 0; --bit) {
        remainder *= 2;
        quotient = quotient * 2 + remainder / divisor;
        remainder %= divisor;
        if ((static_cast<Unsigned>(notional) >> bit) & 1) {
            remainder += static_cast<Unsigned>(rate_.numerator());
            quotient += remainder / divisor;
            remainder %= divisor;
        }
    }
    return checked_add(static_cast<Money>(quotient), remainder != 0);
}
} // namespace counterback
