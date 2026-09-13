from datetime import datetime
from pathlib import Path

from depth_recorder.jsonutil import make_envelope, parse_envelope
from depth_recorder.models import WriteRecord
from depth_recorder.storage import HourlyWriter, StorageManager, iter_zstd_lines


def ns(day: str, hour: int = 1) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00").timestamp() * 1e9)


def record(timestamp: int, run_id: str = "run-1") -> WriteRecord:
    raw = '{"stream":"btcusdt@depth@100ms","data":{"s":"BTCUSDT","U":1,"u":1,"b":[],"a":[]}}'
    return WriteRecord(
        "BTCUSDT",
        "depth",
        timestamp,
        2,
        make_envelope(raw, recorder_id="a", run_id=run_id, rx_utc=timestamp, rx_mono=2),
        1,
        1,
        run_id,
    )


def test_hourly_writer_finalizes_and_round_trips(tmp_path: Path) -> None:
    writer = HourlyWriter(tmp_path, "depth", "01", 3)
    writer.append(record(ns("2026-09-01")))
    metadata = writer.finalize()
    assert metadata["line_count"] == 1
    assert metadata["sha256"]
    lines = list(iter_zstd_lines(tmp_path / "depth-01.ndjson.zst"))
    assert parse_envelope(lines[0])["run_id"] == "run-1"
    assert not (tmp_path / "depth-01.ndjson.wal").exists()


def test_torn_wal_tail_is_quarantined(tmp_path: Path) -> None:
    timestamp = ns("2026-09-01")
    valid = record(timestamp).envelope
    wal = tmp_path / "depth-01.ndjson.wal"
    wal.write_bytes(valid + b'{"torn"')
    writer = HourlyWriter(tmp_path, "depth", "01", 3)
    assert writer.line_count == 1
    writer.finalize()
    assert any((tmp_path / "quarantine").iterdir())


def test_partial_then_restart_preserves_multiple_run_ids(tmp_path: Path) -> None:
    timestamp = ns("2026-09-01")
    first = HourlyWriter(tmp_path, "depth", "01", 3)
    first.append(record(timestamp, "run-1"))
    first.close_active()
    second = HourlyWriter(tmp_path, "depth", "01", 3)
    second.append(record(timestamp + 1, "run-2"))
    metadata = second.finalize()
    assert metadata["run_ids"] == ["run-1", "run-2"]
    assert len(list(iter_zstd_lines(tmp_path / "depth-01.ndjson.zst"))) == 2


def test_storage_manifest_contains_runs_and_checksums(tmp_path: Path) -> None:
    day = "2026-09-01"
    manager = StorageManager(tmp_path, "a", "run-1", "hash", ("BTCUSDT",), 3, "test")
    manager.append_ledger("process_start", rx_utc=ns(day), rx_mono=1)
    manager.append(record(ns(day)))
    paths = manager.finalize_day(day)
    manifest = __import__("json").loads(paths[0].read_text())
    assert manifest["run_ids"] == ["run-1"]
    assert manifest["files"][0]["sha256"]
    assert manifest["summary"]["message_counts"] == {"depth": 1}
    assert manifest["partial"] is False


def test_closed_day_writers_can_be_detached_before_background_finalization(
    tmp_path: Path,
) -> None:
    day = "2026-09-01"
    manager = StorageManager(tmp_path, "a", "run-1", "hash", ("BTCUSDT",), 3, "test")
    manager.append(record(ns(day)))
    writers = manager.detach_day_writers(day)
    assert len(writers) == 1
    assert manager.writers == {}
    manager.finalize_detached_writers(writers)
    assert (tmp_path / "btcusdt" / day / "depth-01.ndjson.zst").is_file()


def test_startup_finalizes_previous_day_wal(tmp_path: Path) -> None:
    day = "2026-09-01"
    directory = tmp_path / "btcusdt" / day
    writer = HourlyWriter(directory, "depth", "01", 3)
    writer.append(record(ns(day)))
    writer.close_active()
    manager = StorageManager(tmp_path, "a", "run-2", "hash", ("BTCUSDT",), 3, "test")
    manifests = manager.finalize_previous_days()
    assert manifests
    assert (directory / "depth-01.ndjson.zst").is_file()
    assert not (directory / "depth-01.ndjson.wal").exists()
