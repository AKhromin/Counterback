"""Market-order vertical slice: independent integer/Fraction ledger and CLI acceptance."""

from __future__ import annotations

import json
import os
import random
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from counterback_replay.contract import fee_atoms, quote_atoms


@pytest.fixture
def trading_binary() -> Path:
    name = "COUNTERBACK_TRADING_BINARY"
    binary = Path(os.environ.get(name, "build/engine/counterback_market_orders"))
    if not binary.is_file():
        if name in os.environ:
            pytest.fail(f"configured trading executable is missing: {binary}")
        pytest.skip("build the C++ market-order executable first or set COUNTERBACK_TRADING_BINARY")
    return binary.resolve()


def run(
    binary: Path,
    tmp_path: Path,
    tape: str,
    orders: str,
    *,
    cash: int = 10000,
    rate: str = "0.001",
    decimals: int = 0,
    end: int = 100,
    initial: tuple[int, int] | None = None,
) -> subprocess.CompletedProcess[str]:
    source, script = tmp_path / "events.l2", tmp_path / "orders.txt"
    source.write_text(tape)
    script.write_text(orders)
    args = [str(binary), str(source), str(script), "0", str(end), str(cash), rate, str(decimals)]
    if initial is not None:
        args.extend(map(str, initial))
    return subprocess.run(args, capture_output=True, text=True, check=False)


def exact(value: dict[str, str]) -> Fraction:
    return Fraction(int(value["numerator"]), int(value["denominator"]))


def rows(result: subprocess.CompletedProcess[str]) -> list[dict]:
    return [json.loads(line) for line in result.stdout.splitlines()]


@pytest.mark.parametrize(
    ("tick", "lot", "decimals", "rate"),
    [("1", "1", 0, "0"), ("1", "1", 0, "0.001"), ("0.01", "0.00001", 8, "0.1")],
)
def test_seeded_orders_against_independent_ledger(
    trading_binary: Path, tmp_path: Path, tick: str, lot: str, decimals: int, rate: str
) -> None:
    rng = random.Random(20261108)
    factor = quote_atoms(1, 1, tick, lot, decimals)
    cash, inventory, basis, realized, fees = (
        20000 * factor,
        5,
        Fraction(500 * factor),
        Fraction(0),
        0,
    )
    initial_cash, initial_basis = cash, basis
    bids, asks = {100: 5, 99: 4, 90: 100, 89: 1000}, {101: 5, 102: 4, 115: 100, 116: 1000}
    remaining: dict[tuple[str, int], int] = {}
    initial_bids = {p: q for p, q in bids.items() if p >= 90}
    initial_asks = {p: q for p, q in asks.items() if p <= 115}
    actions = []
    source = []
    scripts = []
    sequence = 0
    # One independent event list combines exogenous absolute observations and arrivals.
    for step in range(120):
        when = (step + 1) * 10
        changes = (
            [("buy", 89, 1000), ("sell", 116, 1000)]
            if step == 0
            else [(rng.choice(["buy", "sell"]), rng.randrange(95, 101), rng.randrange(7))]
        )
        if changes and changes[0][0] == "sell":
            changes[0] = ("sell", rng.randrange(101, 111), changes[0][2])
        bid_changes = [(p, q) for side, p, q in changes if side == "buy"]
        ask_changes = [(p, q) for side, p, q in changes if side == "sell"]
        levels = " ".join(str(v) for pair in bid_changes + ask_changes for v in pair)
        source.append(
            f"D {101 + step} {101 + step} {when} 0 0 a r "
            f"{len(bid_changes)} {len(ask_changes)} {levels}\n"
        )
        actions.append((when, 0, sequence, changes))
        sequence += 1
        for child in range(2):
            order_id = 2 * step + child + 1
            if step % 17 == 0 and child == 1:
                order_id -= 1  # A duplicate ID must not reapply fees/fills.
            side = rng.choice(["buy", "sell"])
            quantity = rng.randrange(0, 25)
            delay = rng.choice([0, 0, 9, 12])
            scripts.append(f"{when + 1} {delay} {order_id} {side} {quantity}\n")
            actions.append((when + 1 + delay, 1, sequence, (order_id, side, quantity)))
            sequence += 1
    snapshot = " ".join(
        str(v) for pair in list(initial_bids.items()) + list(initial_asks.items()) for v in pair
    )
    tape = (
        f"COUNTERBACK_L2 1 BTCUSDT {tick} {lot}\nS 100 0 a r 3 3 {snapshot}\nBEGIN\n"
        + "".join(source)
        + f"END {len(source)}\n"
    )
    kwargs = dict(
        cash=cash, rate=rate, decimals=decimals, end=1300, initial=(inventory, int(basis))
    )
    result = run(trading_binary, tmp_path, tape, "".join(scripts), **kwargs)
    assert result.returncode == 0, result.stderr
    assert run(trading_binary, tmp_path, tape, "".join(scripts), **kwargs).stdout == result.stdout
    output = rows(result)
    actual = iter(row for row in output if row.get("outcome"))
    seen = set()
    fill_id = 0
    for time, kind, _, action in sorted(actions):
        if kind == 0:
            for side, price, quantity in action:
                book = bids if side == "buy" else asks
                if quantity:
                    book[price] = quantity
                else:
                    book.pop(price, None)
                remaining.pop((side, price), None)
            continue
        order_id, side, quantity = action
        row = next(actual)
        outcome = row["outcome"]
        assert outcome["order_id"] == order_id and outcome["side"] == side
        assert outcome["requested_lots"] == quantity and outcome["time_ns"] == time
        reason = ""
        fills = []
        if order_id in seen:
            reason = "duplicate_or_invalid_order_id"
        elif quantity <= 0:
            reason = "invalid_order"
        elif side == "sell" and quantity > inventory:
            reason = "insufficient_inventory"
        else:
            opposite = "sell" if side == "buy" else "buy"
            book = asks if side == "buy" else bids
            left = quantity
            total = old_fee = 0
            for price in sorted(book, reverse=side == "sell"):
                if (side == "buy" and price > 115) or (side == "sell" and price < 90):
                    break
                available = remaining.get((opposite, price), book[price])
                lots = min(left, available)
                if not lots:
                    continue
                notional = quote_atoms(price, lots, tick, lot, decimals)
                total += notional
                new_fee = fee_atoms(total, rate)
                fills.append((price, lots, notional, new_fee - old_fee))
                old_fee = new_fee
                left -= lots
                if not left:
                    break
            if side == "buy" and total + old_fee > cash:
                reason = "insufficient_cash"
                fills = []
        seen.add(order_id)
        executed = sum(f[1] for f in fills)
        expected_reason = reason or (
            "filled" if executed == quantity else "insufficient_known_liquidity"
        )
        assert outcome["reason"] == expected_reason, (time, order_id, fills, outcome)
        expected_status = "rejected" if reason else "filled" if executed == quantity else "expired"
        assert outcome["status"] == expected_status
        assert outcome["executed_lots"] == sum(f[1] for f in fills)
        assert outcome["remaining_lots"] == quantity - outcome["executed_lots"]
        assert len(outcome["fills"]) == len(fills)
        for actual_fill, (price, lots, notional, fee) in zip(outcome["fills"], fills, strict=True):
            fill_id += 1
            assert actual_fill == dict(
                fill_id=fill_id,
                order_id=order_id,
                time_ns=time,
                price_ticks=price,
                quantity_lots=lots,
                notional_atoms=str(notional),
                fee_atoms=str(fee),
                side=side,
                liquidity_role="modeled_taker",
                model="historical_visible_depth_v1",
                symbol="BTCUSDT",
            )
            opposite = "sell" if side == "buy" else "buy"
            book = asks if side == "buy" else bids
            remaining[opposite, price] = remaining.get((opposite, price), book[price]) - lots
            if side == "buy":
                cash -= notional + fee
                inventory += lots
                basis += notional
            else:
                removed = basis * lots / inventory
                cash += notional - fee
                realized += notional - removed
                basis -= removed
                inventory -= lots
            fees += fee
        account = row["account"]
        value = inventory * max(bids) * factor
        assert int(account["cash_atoms"]) == cash and account["inventory_lots"] == inventory
        assert exact(account["basis_atoms"]) == basis
        assert exact(account["realized_gross_atoms"]) == realized
        assert int(account["fees_atoms"]) == fees
        assert account["valuation_valid"] and account["mark_bid_ticks"] == max(bids)
        assert int(account["equity_atoms"]) == cash + value
        unrealized = exact(account["unrealized_gross_atoms"])
        assert unrealized == value - basis
        assert cash + value == initial_cash + initial_basis + realized + unrealized - fees
    assert next(actual, None) is None
    terminal = next(row for row in output if row.get("status") == "end")["account"]
    assert int(terminal["cash_atoms"]) == cash and terminal["inventory_lots"] == inventory


@pytest.mark.parametrize(
    "invalid",
    [
        "D 105 105 20 0 0 a r 0 0\n",
        "D 102 102 20 0 0 a r 0 2 101 0 102 0\n",
        "D 102 102 20 0 0 a r 0 1 100 2\n",
    ],
)
def test_halt_preserves_holdings_and_expires_pending(
    trading_binary: Path, tmp_path: Path, invalid: str
) -> None:
    tape = (
        "COUNTERBACK_L2 1 BTCUSDT 1 1\nS 100 0 a r 2 2 100 5 99 2 101 2 102 3\nBEGIN\n"
        "D 101 101 10 0 0 a r 0 0\n" + invalid + "END 2\n"
    )
    result = run(trading_binary, tmp_path, tape, "15 0 1 buy 1\n15 10 2 sell 1\n")
    assert result.returncode == 2, result.stderr
    output = rows(result)
    terminal = output[-2]
    assert output[-1]["halted"] and output[-1]["stopped_at_ns"] == 20
    assert terminal["account"]["cash_atoms"] == "9898"
    assert terminal["account"]["inventory_lots"] == 1
    assert terminal["account"]["equity_atoms"] is None
    assert not terminal["account"]["valuation_valid"]
    assert terminal["validity_transition"]["from"] == "trusted"
    assert terminal["terminated_orders"][0]["reason"] == "expired_on_halt"
    assert sum(len(r.get("outcome", {}).get("fills", [])) for r in output) == 1


def test_large_notional_and_fee_remain_exact(trading_binary: Path, tmp_path: Path) -> None:
    price, quantity = 2**63 - 1, 2**63 - 1
    cash = 2**127 - 1
    tape = (
        f"COUNTERBACK_L2 1 BTCUSDT 1 1\nS 100 0 a r 1 1 1 1 {price} {quantity}\nBEGIN\n"
        "D 101 101 10 0 0 a r 0 0\nEND 1\n"
    )
    result = run(
        trading_binary, tmp_path, tape, f"15 0 1 buy {quantity}\n", cash=cash, rate="0.999"
    )
    assert result.returncode == 0, result.stderr
    trade = next(r for r in rows(result) if r.get("outcome"))
    notional = quote_atoms(price, quantity, "1", "1", 0)
    fee = fee_atoms(notional, "0.999")
    assert trade["outcome"]["fills"][0]["notional_atoms"] == str(notional)
    assert trade["outcome"]["fills"][0]["fee_atoms"] == str(fee)
    assert int(trade["account"]["cash_atoms"]) == cash - notional - fee


@pytest.mark.parametrize(
    ("tick", "lot", "decimals", "cash", "rate", "error"),
    [
        ("0.000000001", "0.000000001", 8, 10000, "0", "notional is not exact"),
        ("100000000000000000000000000000000000000", "1", 0, 10000, "0", "overflow"),
        ("1", "1", 0, 2**127, "0", "overflow"),
        ("1", "1", 0, 10000, "NaN", "decimal"),
        ("1", "1", 0, 10000, "1.1", "fee rate"),
    ],
)
def test_invalid_money_fails_explicitly(
    trading_binary: Path,
    tmp_path: Path,
    tick: str,
    lot: str,
    decimals: int,
    cash: int,
    rate: str,
    error: str,
) -> None:
    tape = (
        f"COUNTERBACK_L2 1 BTCUSDT {tick} {lot}\nS 100 0 a r 1 1 100 5 101 5\nBEGIN\n"
        "D 101 101 10 0 0 a r 0 0\nEND 1\n"
    )
    result = run(
        trading_binary, tmp_path, tape, "15 0 1 buy 1\n", cash=cash, rate=rate, decimals=decimals
    )
    assert result.returncode == 1 and error in result.stderr
    assert not any(r.get("outcome", {}).get("fills") for r in rows(result))
