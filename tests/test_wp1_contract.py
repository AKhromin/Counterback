from dataclasses import replace

import pytest

from counterback_replay.contract import (
    EventPriority,
    ScheduleKey,
    SimulationPolicy,
    available_at_ns,
    delayed_time_ns,
    fee_atoms,
    quote_atoms,
    schedule_key,
)


def test_market_visibility_waits_for_snapshot_and_never_moves_backwards() -> None:
    # Update-ID ordering does not imply receive-time ordering; snapshot arrived later.
    first = available_at_ns(0, 100, 150)
    second = available_at_ns(first, 120)
    assert first == second == 150
    assert available_at_ns(second, 170) == 170
    with pytest.raises(ValueError):
        available_at_ns(0, -1)


def test_same_time_order_cancel_and_zero_latency_callback_are_causal() -> None:
    market = schedule_key(100, EventPriority.MARKET, 1)
    arrival = schedule_key(100, EventPriority.ORDER_ARRIVAL, 2, market)
    cancel = schedule_key(100, EventPriority.CANCEL_ARRIVAL, 3, market)
    callback = schedule_key(100, EventPriority.STRATEGY, 4, market)
    child_order = schedule_key(100, EventPriority.ORDER_ARRIVAL, 5, callback)
    notification = schedule_key(100, EventPriority.NOTIFICATION, 6, child_order)
    assert market < arrival < cancel < callback < child_order < notification
    assert child_order.causal_round == 1
    assert schedule_key(101, EventPriority.ORDER_ARRIVAL, 7, notification).causal_round == 0
    with pytest.raises(ValueError, match="past"):
        schedule_key(99, EventPriority.MARKET, 8, notification)
    with pytest.raises(ValueError, match="sequence"):
        schedule_key(101, EventPriority.MARKET, 1, notification)
    with pytest.raises(ValueError):
        ScheduleKey(0, -1, EventPriority.MARKET, 0)


def test_delay_is_simulated_and_checked() -> None:
    assert delayed_time_ns(100, 25) == 125
    assert delayed_time_ns(125, 10) == 135
    with pytest.raises(ValueError, match="overflow"):
        delayed_time_ns((1 << 64) - 1, 1)
    with pytest.raises(ValueError):
        delayed_time_ns(1, -1)


def test_quote_notional_and_fees_are_exact_with_declared_rounding() -> None:
    # 100 USDT/BTC * 0.2 BTC = 20 USDT; synthetic 0.1% fee = 0.02 USDT.
    assert quote_atoms(10000, 20000, "0.01", "0.00001") == 2_000_000_000
    assert fee_atoms(2_000_000_000, "0.001") == 2_000_000
    assert fee_atoms(1, "0.001") == 1  # Round positive fee up to one quote atom.
    assert fee_atoms(1, "0") == 0
    with pytest.raises(ValueError, match="represented"):
        quote_atoms(1, 1, "0.000000001", "0.000000001")
    for invalid in (True, -1, 1 << 127):
        with pytest.raises(ValueError):
            fee_atoms(invalid, "0.001")
    for price, quantity in ((0, 1), (1, 0), ((1 << 63), 1)):
        with pytest.raises(ValueError):
            quote_atoms(price, quantity, "0.01", "0.00001")


def test_policy_is_explicit_and_does_not_claim_runtime_implementation() -> None:
    policy = SimulationPolicy()
    assert policy.to_dict()["runtime_implemented"] is False
    assert "assumption" in policy.to_dict()["fee_rate_source"]
    for values in (
        dict(fee_rate="NaN"),
        dict(fee_rate="1.1"),
        dict(order_latency_ns=-1),
        dict(account="margin"),
        dict(quote_decimals=19),
    ):
        with pytest.raises(ValueError):
            replace(policy, **values)
