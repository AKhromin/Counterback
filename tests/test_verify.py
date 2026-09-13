import pytest

from depth_recorder.models import DepthEvent, Snapshot, TickerEvent
from depth_recorder.verify import (
    coverage_summary,
    deduplicate_depth,
    deduplicate_tickers,
    merge_intervals,
    merge_sorted,
    reconstruct,
    verify_day,
)


def depth(first: int, last: int, payload: int = 1, recorder: str = "a") -> DepthEvent:
    return DepthEvent(
        "BTCUSDT",
        first,
        last,
        (),
        (),
        recorder,
        "run-1",
        first,
        first,
        {"s": "BTCUSDT", "U": first, "u": last, "payload": payload},
    )


def test_interval_union_and_coverage() -> None:
    assert merge_intervals([(5, 6), (1, 2), (3, 4), (8, 8)]) == [(1, 6), (8, 8)]
    summary = coverage_summary([(1, 2), (4, 5)], (1, 5))
    assert summary["ratio"] == 0.8
    assert summary["missing"] == [[3, 3]]
    assert coverage_summary([])["ratio"] == 0
    with pytest.raises(ValueError, match="invalid"):
        merge_intervals([(2, 1)])


def test_merge_sorted_handles_empty_iterators() -> None:
    assert list(merge_sorted([iter([]), iter([2, 4]), iter([1, 3])], lambda value: (value,))) == [
        1,
        2,
        3,
        4,
    ]


def test_exact_deduplication() -> None:
    events = list(deduplicate_depth([depth(1, 2, recorder="a"), depth(1, 2, recorder="b")]))
    assert len(events) == 1


def test_conflicting_duplicate_is_corruption() -> None:
    first = depth(1, 2, 1)
    conflicting = DepthEvent(
        "BTCUSDT",
        1,
        2,
        (("100", "2"),),
        (),
        "b",
        "run-2",
        2,
        2,
        {"U": 1, "u": 2},
    )
    with pytest.raises(ValueError, match="conflicting"):
        list(deduplicate_depth([first, conflicting]))


def test_duplicate_depth_ignores_exchange_event_time() -> None:
    first = DepthEvent(
        "BTCUSDT",
        1,
        2,
        (("100", "1"),),
        (("101", "2"),),
        "a",
        "run-a",
        1,
        1,
        {"E": 1000, "U": 1, "u": 2},
    )
    second = DepthEvent(
        "BTCUSDT",
        1,
        2,
        (("100", "1"),),
        (("101", "2"),),
        "b",
        "run-b",
        2,
        2,
        {"E": 1001, "U": 1, "u": 2},
    )
    assert list(deduplicate_depth([first, second])) == [first]


def test_reconstruction_reports_ticker_mismatch_and_resynchronizes() -> None:
    snapshots = [
        Snapshot("BTCUSDT", 100, (("100", "1"),), (("101", "1"),), "a", "r", 1),
        Snapshot("BTCUSDT", 104, (("100", "1"),), (("101", "1"),), "a", "r", 5),
    ]
    events = iter(
        [
            DepthEvent("BTCUSDT", 101, 101, (), (), "a", "r", 2, 2, {"U": 101, "u": 101}),
            DepthEvent("BTCUSDT", 105, 105, (), (), "a", "r", 6, 6, {"U": 105, "u": 105}),
        ]
    )
    tickers = iter([TickerEvent("BTCUSDT", 101, "99", "1", "101", "1", "a", "r", 3)])
    report = reconstruct(events, tickers, snapshots)
    assert report["segments"] == 2
    assert report["sequence_gaps"] == 1
    assert report["ticker_mismatches"] == 1


def test_ticker_deduplication_and_conflict() -> None:
    ticker = TickerEvent("BTCUSDT", 1, "1", "1", "2", "1", "a", "r", 1)
    duplicate = TickerEvent("BTCUSDT", 1, "1", "1", "2", "1", "b", "x", 2)
    assert list(deduplicate_tickers([ticker, duplicate])) == [ticker]
    conflict = TickerEvent("BTCUSDT", 1, "1", "2", "2", "1", "b", "x", 2)
    with pytest.raises(ValueError, match="conflicting"):
        list(deduplicate_tickers([ticker, conflict]))


def test_reconstruction_handles_missing_anchor_and_untrusted_top() -> None:
    no_anchor = reconstruct(iter([depth(101, 101)]), iter([]), [])
    assert no_anchor["segments"] == 0
    snapshot = Snapshot("BTCUSDT", 100, (("100", "1"),), (("101", "1"),), "a", "r", 1)
    remove_bid = DepthEvent(
        "BTCUSDT",
        101,
        101,
        (("100", "0"),),
        (),
        "a",
        "r",
        2,
        2,
        {"U": 101, "u": 101},
    )
    ticker = TickerEvent("BTCUSDT", 101, "100", "1", "101", "1", "a", "r", 2)
    report = reconstruct(iter([remove_bid]), iter([ticker]), [snapshot])
    assert report["ticker_skipped_untrusted"] == 1
    assert report["trusted_intervals"] == []


def test_reconstruction_reanchors_from_periodic_snapshot() -> None:
    snapshots = [
        Snapshot("BTCUSDT", 100, (("100", "1"),), (("101", "1"),), "a", "r", 1),
        Snapshot("BTCUSDT", 102, (("99", "2"),), (("102", "3"),), "a", "r", 3),
    ]
    events = iter(
        [
            DepthEvent(
                "BTCUSDT",
                101,
                101,
                (("100", "0"),),
                (("101", "0"),),
                "a",
                "r",
                2,
                2,
                {"U": 101, "u": 101},
            ),
            DepthEvent(
                "BTCUSDT", 103, 103, (), (), "a", "r", 4, 4, {"U": 103, "u": 103}
            ),
        ]
    )
    tickers = iter([TickerEvent("BTCUSDT", 103, "99", "2", "102", "3", "a", "r", 4)])
    report = reconstruct(events, tickers, snapshots)
    assert report["segments"] == 2
    assert report["sequence_gaps"] == 1
    assert report["ticker_checks"] == 1
    assert report["ticker_mismatches"] == 0
    assert report["trusted_intervals"] == [(103, 103)]


def test_verify_day_validates_inputs(tmp_path) -> None:
    with pytest.raises(ValueError, match="at least"):
        verify_day("2026-09-01", [], tmp_path)
    source = __import__("depth_recorder.verify", fromlist=["InputSource"]).InputSource(
        "a", tmp_path
    )
    with pytest.raises(ValueError, match="unique"):
        verify_day("2026-09-01", [source, source], tmp_path / "out")
    with pytest.raises(ValueError, match="no symbols"):
        verify_day("2026-09-01", [source], tmp_path / "out")
