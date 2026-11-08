"""WP2 acceptance against the independent Python reconstruction/time contracts."""

from __future__ import annotations

import json
import os
import random
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from counterback_replay.contract import EventPriority, ScheduleKey, available_at_ns, schedule_key
from counterback_replay.export import MAX_ID, Instrument, ReferenceReplay, export_events
from depth_recorder.models import DepthEvent, Snapshot


@pytest.fixture
def runtime_binary() -> Path:
    value = os.environ.get("COUNTERBACK_RUNTIME_BINARY", "build/engine/counterback_runtime")
    binary = Path(value)
    if not binary.is_file():
        if "COUNTERBACK_RUNTIME_BINARY" in os.environ:
            pytest.fail(f"configured runtime executable is missing: {binary}")
        pytest.skip("build the C++ runtime first or set COUNTERBACK_RUNTIME_BINARY")
    return binary.resolve()


def snapshot(last: int, receive: int) -> Snapshot:
    return Snapshot(
        "BTCUSDT",
        last,
        (("100", "2"), ("99", "1")),
        (("101", "3"), ("102", "1")),
        "a",
        "r",
        receive,
    )


def event(first: int, receive: int, *, final: int | None = None, bids=(), asks=()) -> DepthEvent:
    return DepthEvent(
        "BTCUSDT",
        first,
        first if final is None else final,
        tuple(bids),
        tuple(asks),
        "a",
        "r",
        receive,
        first,
        {"E": 7},
    )


def run(binary: Path, path: Path, start: int = 0, end: int = MAX_ID) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(binary), str(path), str(start), str(end)], capture_output=True, text=True, check=False
    )


def assert_reference(
    tmp_path: Path, binary: Path, snapshots: list[Snapshot], events: list[DepthEvent]
) -> None:
    instrument = Instrument("BTCUSDT", "1", "1")
    export_events(tmp_path, instrument, snapshots, events)
    # Keep the entire fixture in warmup so reference reconstruction can cross invalid segments.
    # This tests adapter agreement; separate tests verify the active runtime's halt policy.
    result = run(binary, tmp_path / "events.l2", MAX_ID - 1)
    assert result.returncode == 0, result.stderr
    assert run(binary, tmp_path / "events.l2", MAX_ID - 1).stdout == result.stdout
    rows = [json.loads(row) for row in result.stdout.splitlines()]
    checkpoints = {r["event"]: r for r in rows if r["kind"] == "checkpoint"}
    market_rows = {
        r["source_id"]: r
        for r in rows
        if r["kind"] == "runtime_event" and r["event_kind"] == "market"
    }
    reference = ReferenceReplay(instrument, snapshots)
    prefix = 0
    for source_id, update in enumerate(events, 1):
        before = reference.index
        reference.apply(update)
        if reference.status in ("duplicate", "stale"):
            assert source_id not in market_rows
            continue
        # Snapshot selection is independently evaluated in Python source-list order.
        bridge_receive = 0
        for selected in snapshots[before : reference.index]:
            if update.first_update_id <= selected.last_update_id + 1 <= update.final_update_id:
                bridge_receive = selected.rx_utc
        prefix = available_at_ns(prefix, update.rx_utc, bridge_receive)
        actual = market_rows[source_id]
        assert actual["time_ns"] == prefix
        assert actual["snapshot_receive_ns"] == bridge_receive
        assert actual["receive_ns"] == update.rx_utc
        assert actual["exchange_time_ms"] == 7
        assert checkpoints[source_id] == reference.checkpoint()
        for key in ("last_update_id", "best_bid", "best_ask", "bid_trusted", "ask_trusted"):
            assert actual[key] == reference.checkpoint()[key]
    assert next(r for r in rows if r["kind"] == "summary") == reference.checkpoint(summary=True)
    journal = [r for r in rows if r["kind"] == "runtime_event"]
    keys = [
        ScheduleKey(
            r["time_ns"], r["causal_round"], EventPriority(r["priority"]), r["insertion_sequence"]
        )
        for r in journal
    ]
    assert keys == sorted(keys)
    by_id = {r["event_id"]: r for r in journal}
    assert len(by_id) == len(journal)
    for row, key in zip(journal, keys, strict=True):
        if row["parent_id"] is not None:
            parent = by_id[row["parent_id"]]
            expected = schedule_key(
                key.time_ns,
                key.priority,
                key.insertion_sequence,
                ScheduleKey(
                    parent["time_ns"],
                    parent["causal_round"],
                    EventPriority(parent["priority"]),
                    parent["event_id"],
                ),
            )
            assert key == expected


def test_delayed_snapshots_ties_duplicates_stale_source_order_and_recovery(
    tmp_path: Path, runtime_binary: Path
) -> None:
    snapshots = [snapshot(100, 150), snapshot(110, 300), snapshot(99, 900), snapshot(120, 1000)]
    updates = [
        event(90, 50),
        event(99, 100, final=102),
        event(99, 9999, final=102),
        event(100, 99999, final=101),
        event(103, 120),
        event(104, 140, bids=(("100", "0"), ("99", "0"))),
        event(105, 160),
        event(111, 200),
        event(112, 180),
        event(121, 400),
    ]
    assert_reference(tmp_path, runtime_binary, snapshots, updates)


def test_seeded_reference_at_every_visible_state(tmp_path: Path, runtime_binary: Path) -> None:
    rng = random.Random(20261107)
    updates = []
    first = 100
    for i in range(1000):
        first += 4 if i % 43 == 0 else 1
        update = event(
            first,
            rng.randrange(10000),
            final=first + rng.randrange(3),
            bids=((str(rng.randrange(95, 101)), str(rng.randrange(4))),),
            asks=((str(rng.randrange(101, 107)), str(rng.randrange(4))),),
        )
        updates.append(update)
        if i % 10 == 0:
            updates.append(replace(update, rx_utc=update.rx_utc + 10000, recorder_id="b"))
    assert_reference(
        tmp_path, runtime_binary, [snapshot(i, i * 7) for i in range(100, first, 50)], updates
    )


@pytest.mark.parametrize("kind", ["gap", "horizon", "crossed"])
def test_active_runtime_halts_and_does_not_resume(
    tmp_path: Path, runtime_binary: Path, kind: str
) -> None:
    invalid = {
        "gap": event(105, 200),
        "horizon": event(102, 200, bids=(("100", "0"), ("99", "0"))),
        "crossed": event(102, 200, asks=(("100", "2"),)),
    }[kind]
    export_events(
        tmp_path,
        Instrument("BTCUSDT", "1", "1"),
        [snapshot(100, 150), snapshot(110, 250)],
        [event(101, 100), invalid, event(111, 300)],
    )
    result = run(runtime_binary, tmp_path / "events.l2")
    assert result.returncode == 2, result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    summary = rows[-1]
    assert summary["halted"] and summary["stopped_at_ns"] == 200
    assert summary["reason"] == "required_market_trust_lost"
    market_rows = [r for r in rows if r.get("event_kind") == "market"]
    assert len(market_rows) == 2 and not market_rows[-1]["ready"]
    assert all(r.get("time_ns", 0) <= 200 for r in rows)


def test_end_excludes_delayed_book(tmp_path: Path, runtime_binary: Path) -> None:
    export_events(
        tmp_path, Instrument("BTCUSDT", "1", "1"), [snapshot(100, 150)], [event(101, 100)]
    )
    result = run(runtime_binary, tmp_path / "events.l2", end=150)
    assert result.returncode == 0
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert rows[-1]["applied_updates"] == 0 and not rows[-1]["ever_ready"]
    assert all(r.get("best_bid") is None for r in rows)


@pytest.mark.parametrize(
    "contents",
    [
        "COUNTERBACK_L2 1 BTCUSDT 1 1\nBEGIN\n",
        "COUNTERBACK_L2 1 BTCUSDT 1 1\nBEGIN\nEND 1\n",
        "COUNTERBACK_L2 1 BTCUSDT 1 1\nBEGIN\nEND 0\ntrailing\n",
        "COUNTERBACK_L2 1 BTCUSDT 1 1\nBEGIN\nD -1 2 0 0 0 a r 0 0\nEND 1\n",
    ],
)
def test_runtime_uses_strict_stream_reader(
    tmp_path: Path, runtime_binary: Path, contents: str
) -> None:
    source = tmp_path / "bad.l2"
    source.write_text(contents)
    result = run(runtime_binary, source)
    assert result.returncode == 1 and "runtime failed" in result.stderr
