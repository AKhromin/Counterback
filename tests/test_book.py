from decimal import Decimal

import pytest

from depth_recorder.book import BookDataError, HorizonBook
from depth_recorder.models import DepthEvent, Snapshot, TickerEvent


def snapshot() -> Snapshot:
    return Snapshot(
        symbol="BTCUSDT",
        last_update_id=100,
        bids=(("100", "1"), ("99", "1")),
        asks=(("101", "1"), ("102", "1")),
        recorder_id="a",
        run_id="r1",
        rx_utc=1,
    )


def event(
    first: int = 101,
    final: int = 101,
    bids: tuple[tuple[str, str], ...] = (),
    asks: tuple[tuple[str, str], ...] = (),
) -> DepthEvent:
    return DepthEvent(
        "BTCUSDT", first, final, bids, asks, "a", "r1", 2, 2, {"U": first, "u": final}
    )


def test_absolute_updates_and_idempotent_removal() -> None:
    book = HorizonBook(snapshot())
    result = book.apply(event(bids=(("100", "2"), ("98", "0"))))
    assert book.best_bid == (Decimal("100"), Decimal("2"))
    assert result.unseen_removals == 1
    assert result.outer_unseen_removals == 1


def test_unknown_horizon_is_not_zero_or_corrupt() -> None:
    book = HorizonBook(snapshot())
    result = book.apply(event(bids=(("98", "0"),), asks=(("103", "0"),)))
    assert result.outer_unseen_removals == 2
    assert book.top_trusted


def test_horizon_exhaustion_makes_top_indeterminate() -> None:
    book = HorizonBook(snapshot())
    book.apply(event(bids=(("100", "0"), ("99", "0"), ("98", "4"))))
    assert not book.bid_trusted
    assert book.best_bid is None


def test_removed_best_level_can_be_readded() -> None:
    book = HorizonBook(snapshot())
    book.apply(event(bids=(("100", "0"),), asks=(("101", "0"),)))
    assert book.best_bid == (Decimal("99"), Decimal("1"))
    assert book.best_ask == (Decimal("102"), Decimal("1"))
    book.apply(event(102, 102, bids=(("100", "3"),), asks=(("101", "4"),)))
    assert book.best_bid == (Decimal("100"), Decimal("3"))
    assert book.best_ask == (Decimal("101"), Decimal("4"))


def test_ticker_match_only_when_trusted() -> None:
    book = HorizonBook(snapshot())
    ticker = TickerEvent("BTCUSDT", 101, "100", "1", "101", "1", "a", "r1", 3)
    assert book.matches_ticker(ticker) is True
    book.apply(event(bids=(("100", "0"), ("99", "0"))))
    assert book.matches_ticker(ticker) is None


def test_crossed_and_negative_books_are_errors() -> None:
    book = HorizonBook(snapshot())
    with pytest.raises(BookDataError, match="crossed"):
        book.apply(event(bids=(("103", "1"),)))
    with pytest.raises(BookDataError, match="non-negative"):
        HorizonBook(snapshot()).apply(event(bids=(("100", "-1"),)))


def test_rejects_invalid_snapshot_and_event_scope() -> None:
    empty = Snapshot("BTCUSDT", 1, (), (), "a", "r", 1)
    with pytest.raises(BookDataError, match="both sides"):
        HorizonBook(empty)
    book = HorizonBook(snapshot())
    wrong = DepthEvent("ETHUSDT", 101, 101, (), (), "a", "r", 1, 1, {})
    with pytest.raises(BookDataError, match="symbol"):
        book.apply(wrong)
    with pytest.raises(BookDataError, match="sequence gap"):
        book.apply(event(105, 105))


def test_rejects_non_finite_and_non_positive_prices() -> None:
    malformed = Snapshot("BTCUSDT", 1, (("NaN", "1"),), (("2", "1"),), "a", "r", 1)
    with pytest.raises(BookDataError, match="non-finite"):
        HorizonBook(malformed)
    zero = Snapshot("BTCUSDT", 1, (("0", "1"),), (("2", "1"),), "a", "r", 1)
    with pytest.raises(BookDataError, match="positive"):
        HorizonBook(zero)
