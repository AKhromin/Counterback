from __future__ import annotations

import json
import os
import random
import subprocess
from dataclasses import replace
from decimal import localcontext
from pathlib import Path

import pytest

from counterback_replay.export import (
    MAX_ID,
    Instrument,
    ReferenceReplay,
    compare,
    exact_units,
    export_events,
    export_recording,
    unsigned,
)
from depth_recorder.models import DepthEvent, Snapshot
from depth_recorder.verify import deduplicate_depth, reconstruct


@pytest.fixture
def binary() -> Path:
    path = Path(os.environ.get("COUNTERBACK_REPLAY_BINARY", "build/engine/counterback_replay"))
    if not path.is_file():
        if "COUNTERBACK_REPLAY_BINARY" in os.environ:
            pytest.fail(f"configured C++ replay executable does not exist: {path}")
        pytest.skip(
            "C++ replay executable missing: build with CMake or set COUNTERBACK_REPLAY_BINARY"
        )
    return path.resolve()


def snapshot(last: int = 100) -> Snapshot:
    return Snapshot(
        "BTCUSDT", last, (("100", "1"), ("99", "1")), (("101", "1"), ("102", "1")), "a", "r", last
    )


def event(first: int, final: int | None = None, *, bids=(), asks=()) -> DepthEvent:
    return DepthEvent(
        "BTCUSDT",
        first,
        first if final is None else final,
        tuple(bids),
        tuple(asks),
        "a",
        "r",
        first * 100,
        first,
        {"E": first, "U": first, "u": first if final is None else final},
    )


def differential(
    tmp_path: Path,
    binary: Path,
    snapshots: list[Snapshot],
    events: list[DepthEvent],
    *,
    every: int = 1,
) -> dict:
    summary = export_events(
        tmp_path,
        Instrument("BTCUSDT", "0.01", "0.00001"),
        snapshots,
        events,
        checkpoint_every=every,
    )
    result = subprocess.run(
        [str(binary), str(tmp_path / "events.l2"), str(every)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (2 if summary["structural_errors"] else 0), result.stderr
    actual = tmp_path / "actual.ndjson"
    actual.write_text(result.stdout)
    comparison = compare(tmp_path / "expected.ndjson", actual)
    reference = reconstruct(iter(deduplicate_depth(events)), iter([]), snapshots)
    for key in (
        "segments",
        "sequence_gaps",
        "unseen_removals",
        "outer_unseen_removals",
        "structural_errors",
        "last_depth_update_id",
        "trusted_intervals",
    ):
        # JSON uses lists; reconstruct returns tuples for intervals.
        assert comparison[key] == json.loads(json.dumps(reference[key]))
    return comparison


def test_exact_decimal_conversion_has_no_context_rounding() -> None:
    with localcontext() as context:
        context.prec = 3
        assert exact_units("123456789.12345678", "0.00000001") == 12345678912345678
        assert exact_units("0.0075", "0.0025") == 3
        assert exact_units("1.50", "0.25") == 6
        assert exact_units("0.00000000", "0.00001") == 0
        assert exact_units(str((1 << 63) - 1), "1") == (1 << 63) - 1
    for text in ("NaN", "Infinity", "-1", "1e-8", "1.001", str(1 << 63)):
        with pytest.raises(ValueError):
            exact_units(text, "1")
    with pytest.raises(ValueError):
        Instrument("BTCUSDT", "0", "1")
    with pytest.raises(ValueError):
        exact_units("1", "0")
    assert unsigned(MAX_ID) == MAX_ID
    for value in (-1, MAX_ID + 1, True):
        with pytest.raises(ValueError):
            unsigned(value)


def test_synthetic_state_matches_horizon_book_after_every_event(
    tmp_path: Path, binary: Path
) -> None:
    bridge = event(99, 102, bids=(("100", "2"), ("98", "0")))
    events = [
        event(90),
        bridge,
        replace(bridge, recorder_id="b", rx_utc=bridge.rx_utc + 1),
        event(100, 101, bids=(("100", "90"),)),
        event(101, 103, bids=(("100", "0"), ("99", "0"), ("98", "4"))),
        event(102, 104, bids=(("99", "3"),), asks=(("101", "0"),)),
        event(107),
        event(108),
        event(111, 112),
        event(112, 113, bids=(("103", "1"),)),
        event(114),
        event(121),
        event(122, asks=(("101", "0"), ("102", "0"), ("103", "7"))),
        event(123, asks=(("102", "3"),)),
    ]
    summary = differential(tmp_path, binary, [snapshot(), snapshot(110), snapshot(120)], events)
    assert summary["duplicates"] == 1
    assert summary["stale"] == 1
    assert summary["sequence_gaps"] == 4
    assert summary["segments"] == 3
    assert summary["best_bid"] == [10000, 100000]
    assert summary["best_ask"] == [10200, 300000]
    rows = [json.loads(line) for line in (tmp_path / "actual.ndjson").read_text().splitlines()]
    assert rows[4]["best_bid"] is None and rows[4]["bids"] == [[9800, 400000]]
    assert rows[6]["last_update_id"] is None
    assert rows[12]["best_ask"] is None


def test_fixed_seed_overlaps_gaps_periodic_snapshots(tmp_path: Path, binary: Path) -> None:
    rng = random.Random(20261021)
    events = []
    first = 100
    for index in range(2000):
        first += 4 if index % 100 == 0 else 1
        bids = tuple((str(rng.randrange(95, 101)), str(rng.randrange(4))) for _ in range(3))
        asks = tuple((str(rng.randrange(101, 107)), str(rng.randrange(4))) for _ in range(3))
        update = event(first, first + rng.randrange(3), bids=bids, asks=asks)
        events.append(update)
        if index % 10 == 0:
            events.append(replace(update, recorder_id="b", rx_utc=update.rx_utc + 1))
    snapshots = [snapshot(i) for i in range(100, first, 200)]
    summary = differential(tmp_path, binary, snapshots, events)
    assert summary["event"] == 2200 and summary["duplicates"] == 200
    # Repeat the same serialized input to establish deterministic output.
    repeat = subprocess.run(
        [str(binary), str(tmp_path / "events.l2")], capture_output=True, text=True, check=True
    )
    assert repeat.stdout == (tmp_path / "actual.ndjson").read_text()


def test_snapshot_source_order_matches_verifier(tmp_path: Path, binary: Path) -> None:
    snapshots = [snapshot(100), snapshot(110), snapshot(99), snapshot(105)]
    differential(tmp_path, binary, snapshots, [event(101), event(106), event(111), event(112)])


def test_uint64_boundaries(tmp_path: Path, binary: Path) -> None:
    s = replace(snapshot(), last_update_id=MAX_ID - 1)
    e = replace(event(101), first_update_id=MAX_ID, final_update_id=MAX_ID)
    differential(tmp_path, binary, [s], [e, replace(e, rx_utc=e.rx_utc + 1)])


def test_reference_rejects_raw_conflicts_and_unsorted_inputs(tmp_path: Path) -> None:
    instrument = Instrument("BTCUSDT", "1", "1")
    ref = ReferenceReplay(instrument, [snapshot()])
    ref.apply(event(101, bids=(("100", "1"),)))
    with pytest.raises(ValueError, match="conflicting"):
        ref.apply(event(101, bids=(("100.0", "1.0"),)))
    with pytest.raises(ValueError, match="sorted"):
        ref.apply(event(100))
    with pytest.raises(ValueError, match="scope"):
        ReferenceReplay(instrument, []).apply(replace(event(1), symbol="ETHUSDT"))
    with pytest.raises(ValueError, match="positive"):
        export_events(tmp_path, instrument, [], [], checkpoint_every=0)


@pytest.mark.parametrize(
    "suffix,message",
    [
        ("", "missing BEGIN"),
        ("BEGIN\n", "truncated"),
        ("BEGIN\nEND 1\n", "event count"),
        ("BEGIN\nEND 0\nD 1\n", "trailing"),
        ("BEGIN\nD -1 2 0 0 0 a r 0 0\nEND 1\n", "unsigned"),
        ("BEGIN\nD 1 2 0 0 0 a r 1 0 0 1\nEND 1\n", "int64"),
        ("BEGIN\nD 2 1 0 0 0 a r 0 0\nEND 1\n", "range"),
        ("BEGIN\nD 1 1 0 0 0 a r 0 0 extra\nEND 1\n", "trailing"),
        ("BEGIN\nD 1 1 0 0 0 a r 1 0 9223372036854775808 1\nEND 1\n", "int64"),
        ("BEGIN\nD 1 1 0 0 0 a r 0 0\nD 1 1 0 0 0 a r 1 0 100 2\nEND 2\n", "conflicting"),
        ("BEGIN\nD 2 2 0 0 0 a r 0 0\nD 1 1 0 0 0 a r 0 0\nEND 2\n", "sorted"),
    ],
)
def test_adapter_rejects_malformed_input(
    tmp_path: Path, binary: Path, suffix: str, message: str
) -> None:
    path = tmp_path / "invalid.l2"
    path.write_text("COUNTERBACK_L2 1 BTCUSDT 0.01 0.00001\n" + suffix)
    result = subprocess.run([str(binary), str(path)], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert message in result.stderr


def test_compare_rejects_incomplete_or_different_output(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.write_text('{"kind":"summary","event":1}\n')
    right.write_text('{"kind":"summary","event":2}\n')
    with pytest.raises(ValueError, match="differing fields"):
        compare(left, right)
    right.write_text("")
    with pytest.raises(ValueError, match="count"):
        compare(left, right)
    left.write_text("")
    with pytest.raises(ValueError, match="summary"):
        compare(left, right)


def test_export_refuses_unverified_report(tmp_path: Path) -> None:
    from depth_recorder.verify import InputSource

    report = tmp_path / "report.json"
    report.write_text('{"status":"review"}')
    with pytest.raises(ValueError, match="passing verification"):
        export_recording("2026-09-14", [InputSource("a", tmp_path)], report, tmp_path / "out")


def recorded_day(tmp_path: Path) -> tuple[list, Path]:
    """Tiny synthetic 24-hour capture, using the real zstd/manifest writers."""
    from depth_recorder.jsonutil import make_envelope
    from depth_recorder.models import WriteRecord
    from depth_recorder.storage import StorageManager
    from depth_recorder.verify import InputSource, verify_day

    day, start = "2026-09-14", 1789344000000000000
    inputs = []
    for recorder in ("a", "b"):
        root = tmp_path / recorder
        storage = StorageManager(root, recorder, "run", "config", ("BTCUSDT",), 3, "test")

        def envelope(data: dict, timestamp: int, recorder: str = recorder) -> bytes:
            return make_envelope(
                json.dumps(data),
                recorder_id=recorder,
                run_id="run",
                rx_utc=timestamp,
                rx_mono=timestamp - start,
            )

        snap = {
            "lastUpdateId": 100,
            "bids": [["100", "1"], ["99", "1"]],
            "asks": [["101", "1"], ["102", "1"]],
        }
        info = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
                    ],
                }
            ]
        }
        for kind, payload in (("snapshot", snap), ("exchange-info", info)):
            raw = json.dumps(payload).encode()
            storage.write_http_payload(
                "BTCUSDT", kind, raw, envelope(payload, start + 1), start + 1
            )
        for hour in range(24):
            timestamp = start + (hour * 3600 + 1) * 1_000_000_000
            if hour == 23:
                timestamp = start + 86399 * 1_000_000_000
            depth = {"s": "BTCUSDT", "U": 101 + hour, "u": 101 + hour, "b": [], "a": []}
            ticker = {"s": "BTCUSDT", "u": 101 + hour, "b": "100", "B": "1", "a": "101", "A": "1"}
            for stream, payload in (("depth", depth), ("bookTicker", ticker)):
                storage.append(
                    WriteRecord(
                        "BTCUSDT",
                        stream,
                        timestamp,
                        timestamp - start,
                        envelope(payload, timestamp),
                        101 + hour if stream == "depth" else None,
                        101 + hour if stream == "depth" else None,
                        "run",
                    )
                )
        storage.finalize_day(day)
        inputs.append(InputSource(recorder, root))
    reports = verify_day(day, inputs, tmp_path / "verified")
    assert json.loads(reports[0].read_text())["status"] == "pass"
    return inputs, reports[0]


def test_compressed_export_is_verified_deterministic_and_matches_cpp(
    tmp_path: Path,
    binary: Path,
) -> None:
    inputs, report = recorded_day(tmp_path)
    first, second = tmp_path / "export1", tmp_path / "export2"
    summary = export_recording("2026-09-14", inputs, report, first, checkpoint_every=3)
    export_recording("2026-09-14", inputs, report, second, checkpoint_every=3)
    assert summary["applied"] == 24 and summary["duplicates"] == 24
    assert (first / "events.l2").read_bytes() == (second / "events.l2").read_bytes()
    assert (first / "evidence.json").read_bytes() == (second / "evidence.json").read_bytes()
    result = subprocess.run(
        [str(binary), str(first / "events.l2"), "3"], capture_output=True, text=True, check=True
    )
    (first / "actual.ndjson").write_text(result.stdout)
    assert compare(first / "expected.ndjson", first / "actual.ndjson")["checkpoints_compared"] == 16
    with pytest.raises(FileExistsError):
        export_recording("2026-09-14", inputs, report, first)
    with pytest.raises(ValueError, match="identities/order"):
        export_recording("2026-09-14", inputs[::-1], report, tmp_path / "reordered")


@pytest.mark.parametrize("failure", ["checksum", "unlisted", "metadata", "report", "grid"])
def test_compressed_export_rejects_bad_evidence(tmp_path: Path, failure: str) -> None:
    import zstandard as zstd

    from depth_recorder.storage import sha256_file

    inputs, report_path = recorded_day(tmp_path)
    directory = inputs[0].root / "btcusdt/2026-09-14"
    manifest_path = directory / "manifest-2026-09-14.json"
    manifest = json.loads(manifest_path.read_text())
    path = next(directory.glob("exchange-info-*.zst"))
    if failure == "checksum":
        path.write_bytes(path.read_bytes() + b"broken")
    elif failure == "unlisted":
        manifest["files"] = [item for item in manifest["files"] if item["name"] != path.name]
    elif failure in ("metadata", "grid"):
        payload = json.loads(zstd.ZstdDecompressor().decompress(path.read_bytes()))
        if failure == "metadata":
            payload["raw"]["symbols"][0]["filters"][0]["tickSize"] = "0.02"
        else:
            # Both recorders agree on a step that cannot represent the fixture's price 101.
            for source in inputs:
                other = next((source.root / "btcusdt/2026-09-14").glob("exchange-info-*.zst"))
                obj = json.loads(zstd.ZstdDecompressor().decompress(other.read_bytes()))
                obj["raw"]["symbols"][0]["filters"][0]["tickSize"] = "2"
                other.write_bytes(zstd.ZstdCompressor().compress(json.dumps(obj).encode()))
                mp = other.parent / "manifest-2026-09-14.json"
                updated = json.loads(mp.read_text())
                for item in updated["files"]:
                    if item["name"] == other.name:
                        item.update(sha256=sha256_file(other), bytes=other.stat().st_size)
                mp.write_text(json.dumps(updated))
        if failure == "metadata":
            path.write_bytes(zstd.ZstdCompressor().compress(json.dumps(payload).encode()))
            for item in manifest["files"]:
                if item["name"] == path.name:
                    item.update(sha256=sha256_file(path), bytes=path.stat().st_size)
    else:
        report = json.loads(report_path.read_text())
        report["reconstruction"]["segments"] = 999
        report_path.write_text(json.dumps(report))
    if failure in ("unlisted", "metadata"):
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        export_recording("2026-09-14", inputs, report_path, tmp_path / "rejected")
    assert not (tmp_path / "rejected/evidence.json").exists()
