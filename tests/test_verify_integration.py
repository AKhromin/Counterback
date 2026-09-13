from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from depth_recorder.jsonutil import make_envelope
from depth_recorder.models import WriteRecord
from depth_recorder.storage import StorageManager
from depth_recorder.verify import InputSource, parse_input, verify_day, verify_manifest

DAY = "2026-09-01"
START = int(datetime(2026, 9, 1, 0, 0, 1, tzinfo=UTC).timestamp() * 1e9)


def manager(root: Path, recorder: str) -> StorageManager:
    return StorageManager(root, recorder, f"run-{recorder}", "config-hash", ("BTCUSDT",), 3, "test")


def add_stream(
    storage: StorageManager,
    recorder: str,
    stream: str,
    data: dict[str, object],
    timestamp: int,
) -> None:
    stream_name = "bookTicker" if stream == "bookTicker" else stream
    suffix = "depth@100ms" if stream == "depth" else stream_name
    raw = json.dumps({"stream": f"btcusdt@{suffix}", "data": data}, separators=(",", ":"))
    envelope = make_envelope(
        raw,
        recorder_id=recorder,
        run_id=f"run-{recorder}",
        rx_utc=timestamp,
        rx_mono=timestamp - START + 10,
    )
    storage.append(
        WriteRecord(
            symbol="BTCUSDT",
            stream=stream,
            rx_utc=timestamp,
            rx_mono=timestamp - START + 10,
            envelope=envelope,
            first_update_id=int(data["U"]) if "U" in data else None,
            final_update_id=int(data["u"]) if stream == "depth" else None,
            run_id=f"run-{recorder}",
        )
    )


def add_snapshot(storage: StorageManager, recorder: str) -> None:
    data = {
        "lastUpdateId": 100,
        "bids": [["100", "1"], ["99", "1"]],
        "asks": [["101", "1"], ["102", "1"]],
    }
    raw = json.dumps(data, separators=(",", ":")).encode()
    envelope = make_envelope(
        raw,
        recorder_id=recorder,
        run_id=f"run-{recorder}",
        rx_utc=START,
        rx_mono=1,
    )
    storage.write_http_payload("BTCUSDT", "snapshot", raw, envelope, START)


def build_inputs(tmp_path: Path) -> list[InputSource]:
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    a, b = manager(root_a, "a"), manager(root_b, "b")
    add_snapshot(a, "a")
    add_stream(
        a,
        "a",
        "depth",
        {"s": "BTCUSDT", "U": 101, "u": 101, "b": [["100", "2"]], "a": []},
        START + 1,
    )
    add_stream(
        a,
        "a",
        "bookTicker",
        {"s": "BTCUSDT", "u": 101, "b": "100", "B": "2", "a": "101", "A": "1"},
        START + 2,
    )
    add_stream(
        b,
        "b",
        "depth",
        {"s": "BTCUSDT", "U": 102, "u": 102, "b": [], "a": []},
        START + 3,
    )
    add_stream(
        a,
        "a",
        "depth",
        {"s": "BTCUSDT", "U": 103, "u": 103, "b": [], "a": [["101", "2"]]},
        START + 4,
    )
    add_stream(
        b,
        "b",
        "bookTicker",
        {"s": "BTCUSDT", "u": 103, "b": "100", "B": "2", "a": "101", "A": "2"},
        START + 5,
    )
    a.append_ledger("process_start", rx_utc=START, rx_mono=1)
    b.append_ledger("process_start", rx_utc=START, rx_mono=1)
    a.finalize_day(DAY)
    b.finalize_day(DAY)
    return [InputSource("a", root_a), InputSource("b", root_b)]


def test_verify_day_merges_complementary_recorders(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    output = tmp_path / "out"
    reports = verify_day(DAY, inputs, output)
    report = json.loads(reports[0].read_text())
    assert report["status"] == "review"
    assert report["quality_issues"] == ["wall-clock coverage is incomplete"]
    assert report["joint_update_id_coverage"]["ratio"] == 1.0
    assert report["per_recorder_coverage"]["a"]["missing"] == [[102, 102]]
    assert report["reconstruction"]["segments"] == 1
    assert report["reconstruction"]["ticker_checks"] == 2
    assert report["reconstruction"]["ticker_mismatches"] == 0
    assert report["monotonic_clock_scope"] == "recorder_id+run_id only"
    assert (output / "coverage-ledger.ndjson").is_file()
    with pytest.raises(FileExistsError, match="immutable"):
        verify_day(DAY, inputs, output)


def test_parse_input_discovery_and_manifest_corruption(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    parsed = parse_input(f"a={inputs[0].root}")
    assert parsed.recorder_id == "a"
    manifest = verify_manifest(parsed, "BTCUSDT", DAY)
    assert manifest["config_hash"] == "config-hash"
    depth_path = next((inputs[0].root / "btcusdt" / DAY).glob("depth-*.zst"))
    depth_path.write_bytes(depth_path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        verify_manifest(parsed, "BTCUSDT", DAY)


def test_parse_input_rejects_bad_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        parse_input("missing-separator")
    with pytest.raises(ValueError):
        parse_input(f"a={tmp_path / 'missing'}")


def test_manifest_rejects_scope_and_unsafe_paths(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    source = inputs[0]
    manifest_path = source.root / "btcusdt" / DAY / f"manifest-{DAY}.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"].append({"name": "../escape", "finalized": True})
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsafe"):
        verify_manifest(source, "BTCUSDT", DAY)


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(ValueError, match="missing final"):
        verify_manifest(InputSource("a", root), "BTCUSDT", DAY)
