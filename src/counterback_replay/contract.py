"""WP1 policy primitives, not a trading event loop or portfolio implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from fractions import Fraction
from typing import Any

from .export import MAX_ID, decimal, unsigned

CONTRACT_VERSION = "counterback-v1-contract-1"
AVAILABILITY_VERSION = "merged-receive-prefix-bridge-1"


class EventPriority(IntEnum):
    MARKET = 0
    ORDER_ARRIVAL = 1
    CANCEL_ARRIVAL = 2
    NOTIFICATION = 3
    STRATEGY = 4
    VALUATION = 5
    END = 6


@dataclass(frozen=True, order=True)
class ScheduleKey:
    time_ns: int
    causal_round: int
    priority: EventPriority
    insertion_sequence: int

    def __post_init__(self) -> None:
        unsigned(self.time_ns)
        unsigned(self.causal_round)
        unsigned(self.insertion_sequence)
        if not isinstance(self.priority, EventPriority):
            raise ValueError("invalid event priority")


def schedule_key(
    time_ns: int, priority: EventPriority, sequence: int, current: ScheduleKey | None = None
) -> ScheduleKey:
    """Same-time child work cannot be inserted before its triggering event."""
    unsigned(time_ns)
    unsigned(sequence)
    priority = EventPriority(priority)
    if current:
        if time_ns < current.time_ns:
            raise ValueError("cannot schedule into the past")
        if sequence <= current.insertion_sequence:
            raise ValueError("insertion sequence must increase")
    round_id = 0
    if current and time_ns == current.time_ns:
        round_id = current.causal_round + int(priority <= current.priority)
    return ScheduleKey(time_ns, round_id, priority, sequence)


def available_at_ns(previous_ns: int, receive_ns: int, snapshot_receive_ns: int = 0) -> int:
    """For unique sequence-ordered events, gate visibility on all observations used."""
    return max(unsigned(previous_ns), unsigned(receive_ns), unsigned(snapshot_receive_ns))


def delayed_time_ns(time_ns: int, delay_ns: int) -> int:
    result = unsigned(time_ns) + unsigned(delay_ns)
    if result > MAX_ID:
        raise ValueError("timestamp overflow")
    return result


def quote_atoms(
    price_ticks: int, quantity_lots: int, tick_size: str, lot_size: str, decimals: int = 8
) -> int:
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 18:
        raise ValueError("quote precision must be between 0 and 18")
    if not 0 < unsigned(price_ticks) <= (1 << 63) - 1:
        raise ValueError("price must be positive int64")
    if not 0 < unsigned(quantity_lots) <= (1 << 63) - 1:
        raise ValueError("quantity must be positive int64")
    tick, lot = Fraction(decimal(tick_size)), Fraction(decimal(lot_size))
    if tick <= 0 or lot <= 0:
        raise ValueError("instrument steps must be positive")
    value = price_ticks * quantity_lots * tick * lot * 10**decimals
    if value.denominator != 1 or value.numerator > (1 << 127) - 1:
        raise ValueError("notional cannot be represented in checked quote atoms")
    return int(value.numerator)


def fee_atoms(notional_atoms: int, rate: str) -> int:
    if (
        isinstance(notional_atoms, bool)
        or not isinstance(notional_atoms, int)
        or not 0 <= notional_atoms <= (1 << 127) - 1
    ):
        raise ValueError("invalid notional")
    fraction = Fraction(decimal(rate))
    if not 0 <= fraction <= 1:
        raise ValueError("fee rate must be between zero and one")
    value = notional_atoms * fraction
    return (value.numerator + value.denominator - 1) // value.denominator


@dataclass(frozen=True)
class SimulationPolicy:
    version: str = CONTRACT_VERSION
    availability: str = AVAILABILITY_VERSION
    account: str = "cash-funded-spot-long-only"
    execution: str = "visible-depth-taker-with-crossing-only-limits"
    replenishment: str = "reset-consumption-only-on-new-absolute-level-observation"
    invalid_data: str = "wait-for-initial-anchor-then-halt-on-required-trust-loss"
    valuation: str = "best-bid-mark-for-long-inventory-no-assumed-liquidation"
    end_of_run: str = "expire-pending-actions-cancel-local-remainders-mark-open-position"
    quote_decimals: int = 8
    order_latency_ns: int = 0
    notification_latency_ns: int = 0
    fee_rate: str = "0.001"
    fee_rounding: str = "cumulative-per-order-ceiling-with-incremental-fill-charges"
    warmup_trusted_updates: int = 0

    def __post_init__(self) -> None:
        # Semantic identifiers are fixed for this version; experiments can vary numeric inputs.
        fixed = {
            "version": CONTRACT_VERSION,
            "availability": AVAILABILITY_VERSION,
            "account": "cash-funded-spot-long-only",
            "execution": "visible-depth-taker-with-crossing-only-limits",
            "replenishment": "reset-consumption-only-on-new-absolute-level-observation",
            "invalid_data": "wait-for-initial-anchor-then-halt-on-required-trust-loss",
            "valuation": "best-bid-mark-for-long-inventory-no-assumed-liquidation",
            "end_of_run": "expire-pending-actions-cancel-local-remainders-mark-open-position",
            "fee_rounding": "cumulative-per-order-ceiling-with-incremental-fill-charges",
        }
        for name, expected in fixed.items():
            if getattr(self, name) != expected:
                raise ValueError(f"unsupported policy: {name}")
        unsigned(self.order_latency_ns)
        unsigned(self.notification_latency_ns)
        unsigned(self.warmup_trusted_updates)
        if (
            isinstance(self.quote_decimals, bool)
            or not isinstance(self.quote_decimals, int)
            or not 0 <= self.quote_decimals <= 18
        ):
            raise ValueError("invalid quote precision")
        fee_atoms(1, self.fee_rate)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "fee_rate_source": "benchmark assumption, not a verified Binance/account fee",
            "latency_source": "synthetic configured delay, not measured network latency",
            "event_priority": {item.name.lower(): int(item) for item in EventPriority},
            "runtime_implemented": False,
        }
