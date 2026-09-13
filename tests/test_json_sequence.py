import pytest

from depth_recorder.jsonutil import make_envelope, parse_envelope
from depth_recorder.sequence import SequenceTracker


def test_envelope_preserves_raw_bytes() -> None:
    raw = '{ "stream" : "btcusdt@trade", "data" : {"p":"1.00"} }'
    encoded = make_envelope(raw, recorder_id="a", run_id="run-1", rx_utc=10, rx_mono=20)
    assert b'"raw":' + raw.encode() in encoded
    parsed = parse_envelope(encoded)
    assert parsed["run_id"] == "run-1"
    assert parsed["raw"]["data"]["p"] == "1.00"


def test_envelope_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        make_envelope("[]", recorder_id="a", run_id="r", rx_utc=1, rx_mono=2)


def test_sequence_contiguous_overlap_duplicate_and_gap() -> None:
    tracker = SequenceTracker()
    assert tracker.observe("BTCUSDT", 100, 105) is None
    assert tracker.observe("BTCUSDT", 105, 108) is None
    assert tracker.observe("BTCUSDT", 105, 108) is None
    gap = tracker.observe("BTCUSDT", 111, 115)
    assert gap is not None
    assert gap.missing_span == 2
    assert tracker.last_update_id("BTCUSDT") == 115


def test_sequence_is_symbol_local() -> None:
    tracker = SequenceTracker()
    tracker.observe("BTCUSDT", 100, 100)
    assert tracker.observe("ETHUSDT", 900, 900) is None


def test_sequence_rejects_reverse_range() -> None:
    with pytest.raises(ValueError):
        SequenceTracker().observe("BTCUSDT", 2, 1)
