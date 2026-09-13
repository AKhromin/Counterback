from __future__ import annotations

import heapq
import json
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypeVar

import zstandard as zstd

from .book import BookDataError, HorizonBook
from .jsonutil import parse_envelope
from .models import DepthEvent, Snapshot, TickerEvent
from .storage import iter_zstd_lines, sha256_file

T = TypeVar("T")
PASS_COVERAGE_RATIO = 0.995
EXCLUDE_COVERAGE_RATIO = 0.98
PASS_TRUSTED_RATIO = 0.995


@dataclass(frozen=True, slots=True)
class InputSource:
    recorder_id: str
    root: Path


def parse_input(value: str) -> InputSource:
    if "=" not in value:
        raise ValueError("input must have the form recorder-id=/absolute/path")
    recorder_id, path_text = value.split("=", 1)
    path = Path(path_text).expanduser().resolve()
    if not recorder_id or not path.is_dir():
        raise ValueError(f"invalid verifier input: {value}")
    return InputSource(recorder_id, path)


def _symbol_directory(root: Path, symbol: str, day: str) -> Path:
    return root / symbol.lower() / day


def discover_symbols(inputs: list[InputSource], day: str) -> list[str]:
    symbols: set[str] = set()
    for source in inputs:
        if not source.root.exists():
            continue
        for candidate in source.root.iterdir():
            if candidate.is_dir() and (candidate / day).is_dir():
                symbols.add(candidate.name.upper())
    return sorted(symbols)


def verify_manifest(source: InputSource, symbol: str, day: str) -> dict[str, Any]:
    directory = _symbol_directory(source.root, symbol, day)
    path = directory / f"manifest-{day}.json"
    if not path.exists():
        raise ValueError(f"missing final manifest: {path}")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"manifest is not an object: {path}")
    manifest: dict[str, Any] = loaded
    if manifest.get("partial") or manifest.get("recorder_id") != source.recorder_id:
        raise ValueError(f"invalid final manifest: {path}")
    if manifest.get("symbol") != symbol or manifest.get("date_utc") != day:
        raise ValueError(f"manifest scope mismatch: {path}")
    for item in manifest.get("files", []):
        if not item.get("finalized", True):
            continue
        file_path = (directory / item["name"]).resolve()
        if file_path.parent != directory.resolve():
            raise ValueError(f"unsafe manifest file path: {item['name']}")
        if not file_path.is_file():
            raise ValueError(f"manifest file is missing: {file_path}")
        expected = item.get("sha256")
        if expected and sha256_file(file_path) != expected:
            raise ValueError(f"checksum mismatch: {file_path}")
    return manifest


def _stream_files(source: InputSource, symbol: str, day: str, stream: str) -> list[Path]:
    prefix = "bookticker" if stream == "bookTicker" else stream
    return sorted(_symbol_directory(source.root, symbol, day).glob(f"{prefix}-*.ndjson.zst"))


def _raw_data(envelope: dict[str, Any]) -> dict[str, Any]:
    raw = envelope["raw"]
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        raise ValueError("raw data payload is not an object")
    return data


def iter_depth(source: InputSource, symbol: str, day: str) -> Iterator[DepthEvent]:
    for path in _stream_files(source, symbol, day, "depth"):
        for line in iter_zstd_lines(path):
            envelope = parse_envelope(line)
            data = _raw_data(envelope)
            yield DepthEvent(
                symbol=str(data["s"]),
                first_update_id=int(data["U"]),
                final_update_id=int(data["u"]),
                bids=tuple((str(level[0]), str(level[1])) for level in data.get("b", [])),
                asks=tuple((str(level[0]), str(level[1])) for level in data.get("a", [])),
                recorder_id=str(envelope["recorder_id"]),
                run_id=str(envelope["run_id"]),
                rx_utc=int(envelope["rx_utc"]),
                rx_mono=int(envelope["rx_mono"]),
                raw_data=data,
            )


def iter_tickers(source: InputSource, symbol: str, day: str) -> Iterator[TickerEvent]:
    for path in _stream_files(source, symbol, day, "bookTicker"):
        for line in iter_zstd_lines(path):
            envelope = parse_envelope(line)
            data = _raw_data(envelope)
            yield TickerEvent(
                symbol=str(data["s"]),
                update_id=int(data["u"]),
                bid_price=str(data["b"]),
                bid_quantity=str(data["B"]),
                ask_price=str(data["a"]),
                ask_quantity=str(data["A"]),
                recorder_id=str(envelope["recorder_id"]),
                run_id=str(envelope["run_id"]),
                rx_utc=int(envelope["rx_utc"]),
            )


def load_snapshots(source: InputSource, symbol: str, day: str) -> list[Snapshot]:
    snapshots: list[Snapshot] = []
    directory = _symbol_directory(source.root, symbol, day)
    for path in sorted(directory.glob("snapshot-*.json.zst")):
        raw = zstd.ZstdDecompressor().decompress(path.read_bytes())
        envelope = parse_envelope(raw + (b"" if raw.endswith(b"\n") else b"\n"))
        data = _raw_data(envelope)
        snapshots.append(
            Snapshot(
                symbol=symbol,
                last_update_id=int(data["lastUpdateId"]),
                bids=tuple((str(level[0]), str(level[1])) for level in data.get("bids", [])),
                asks=tuple((str(level[0]), str(level[1])) for level in data.get("asks", [])),
                recorder_id=str(envelope["recorder_id"]),
                run_id=str(envelope["run_id"]),
                rx_utc=int(envelope["rx_utc"]),
            )
        )
    return sorted(snapshots, key=lambda item: (item.last_update_id, item.rx_utc))


def merge_sorted(
    iterators: Iterable[Iterator[T]], key: Callable[[T], tuple[Any, ...]]
) -> Iterator[T]:
    heap: list[tuple[tuple[Any, ...], int, T, Iterator[T]]] = []
    for index, iterator in enumerate(iterators):
        try:
            item = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(heap, (key(item), index, item, iterator))
    while heap:
        _, index, item, iterator = heapq.heappop(heap)
        yield item
        try:
            replacement = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(heap, (key(replacement), index, replacement, iterator))


def deduplicate_depth(events: Iterable[DepthEvent]) -> Iterator[DepthEvent]:
    previous_key: tuple[str, int, int] | None = None
    previous_updates: tuple[
        tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]
    ] | None = None
    for event in events:
        key = (event.symbol, event.first_update_id, event.final_update_id)
        updates = (event.bids, event.asks)
        if key == previous_key:
            if updates != previous_updates:
                raise ValueError(f"conflicting duplicate depth event: {key}")
            continue
        previous_key, previous_updates = key, updates
        yield event


def deduplicate_tickers(events: Iterable[TickerEvent]) -> Iterator[TickerEvent]:
    previous_id: int | None = None
    previous_values: tuple[str, str, str, str] | None = None
    for event in events:
        values = (
            event.bid_price,
            event.bid_quantity,
            event.ask_price,
            event.ask_quantity,
        )
        if event.update_id == previous_id:
            if values != previous_values:
                raise ValueError(f"conflicting bookTicker event at {event.update_id}")
            continue
        previous_id, previous_values = event.update_id, values
        yield event


def merge_intervals(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[list[int]] = []
    for first, last in sorted(intervals):
        if first > last:
            raise ValueError("invalid update-ID interval")
        if not result or first > result[-1][1] + 1:
            result.append([first, last])
        else:
            result[-1][1] = max(result[-1][1], last)
    return [(first, last) for first, last in result]


def coverage_summary(
    intervals: Iterable[tuple[int, int]], denominator: tuple[int, int] | None = None
) -> dict[str, Any]:
    merged = merge_intervals(intervals)
    if not merged and denominator is None:
        return {
            "first_update_id": None,
            "last_update_id": None,
            "covered_ids": 0,
            "ratio": 0.0,
            "missing": [],
        }
    if denominator is None:
        denominator = (merged[0][0], merged[-1][1])
    first, last = denominator
    clipped = [
        (max(start, first), min(end, last))
        for start, end in merged
        if end >= first and start <= last
    ]
    clipped = merge_intervals(clipped)
    covered = sum(end - start + 1 for start, end in clipped)
    total = max(0, last - first + 1)
    missing: list[list[int]] = []
    cursor = first
    for start, end in clipped:
        if start > cursor:
            missing.append([cursor, start - 1])
        cursor = max(cursor, end + 1)
    if cursor <= last:
        missing.append([cursor, last])
    return {
        "first_update_id": first,
        "last_update_id": last,
        "covered_ids": covered,
        "total_ids": total,
        "ratio": covered / total if total else 0.0,
        "missing": missing,
    }


def _source_intervals(source: InputSource, symbol: str, day: str) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for event in iter_depth(source, symbol, day):
        interval = (event.first_update_id, event.final_update_id)
        if not merged or interval[0] > merged[-1][1] + 1:
            merged.append(interval)
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], interval[1]))
    return merged


def _wall_clock_summary(inputs: list[InputSource], symbol: str, day: str) -> dict[str, Any]:
    expected = {f"{hour:02d}" for hour in range(24)}
    per_recorder: dict[str, Any] = {}
    joint_hours: set[str] = set()
    first_rx: int | None = None
    last_rx: int | None = None
    for source in inputs:
        files = _stream_files(source, symbol, day, "depth")
        hours = {path.name.split("-")[1].split(".")[0] for path in files}
        joint_hours.update(hours)
        local_first: int | None = None
        local_last: int | None = None
        for event in iter_depth(source, symbol, day):
            local_first = event.rx_utc if local_first is None else min(local_first, event.rx_utc)
            local_last = event.rx_utc if local_last is None else max(local_last, event.rx_utc)
        if local_first is not None:
            first_rx = local_first if first_rx is None else min(first_rx, local_first)
            assert local_last is not None
            last_rx = local_last if last_rx is None else max(last_rx, local_last)
        per_recorder[source.recorder_id] = {
            "present_hours": sorted(hours),
            "missing_hours": sorted(expected - hours),
        }
    day_start = int(
        datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC).timestamp() * 1e9
    )
    day_end = day_start + 86_400 * 1_000_000_000 - 1
    boundary_ok = (
        first_rx is not None
        and last_rx is not None
        and first_rx - day_start <= 300 * 1_000_000_000
        and day_end - last_rx <= 300 * 1_000_000_000
    )
    return {
        "per_recorder": per_recorder,
        "joint_missing_hours": sorted(expected - joint_hours),
        "first_rx_utc": first_rx,
        "last_rx_utc": last_rx,
        "boundary_evidence": boundary_ok,
        "complete": not (expected - joint_hours) and boundary_ok,
    }


def _find_bridge_snapshot(snapshots: list[Snapshot], event: DepthEvent) -> Snapshot | None:
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.last_update_id < event.final_update_id
        and event.first_update_id <= snapshot.last_update_id + 1 <= event.final_update_id
    ]
    return max(candidates, key=lambda item: item.last_update_id, default=None)


def reconstruct(
    depth_events: Iterator[DepthEvent],
    ticker_events: Iterator[TickerEvent],
    snapshots: list[Snapshot],
) -> dict[str, Any]:
    ticker = next(ticker_events, None)
    latest_ticker: TickerEvent | None = None
    checked_ticker_id: int | None = None
    book: HorizonBook | None = None
    segments = 0
    sequence_gaps = 0
    structural_errors: list[str] = []
    ticker_checks = 0
    ticker_mismatches = 0
    ticker_skipped_untrusted = 0
    unseen_removals = 0
    outer_unseen_removals = 0
    trusted_intervals: list[tuple[int, int]] = []
    previous_depth_u: int | None = None
    snapshot_index = 0
    for event in depth_events:
        if previous_depth_u is not None and event.first_update_id > previous_depth_u + 1:
            sequence_gaps += 1
        previous_depth_u = (
            event.final_update_id
            if previous_depth_u is None
            else max(previous_depth_u, event.final_update_id)
        )
        while ticker is not None and ticker.update_id <= event.final_update_id:
            latest_ticker = ticker
            ticker = next(ticker_events, None)

        bridge_snapshot: Snapshot | None = None
        while (
            snapshot_index < len(snapshots)
            and snapshots[snapshot_index].last_update_id < event.final_update_id
        ):
            candidate = snapshots[snapshot_index]
            snapshot_index += 1
            if event.first_update_id <= candidate.last_update_id + 1 <= event.final_update_id:
                bridge_snapshot = candidate
        if bridge_snapshot is not None:
            try:
                book = HorizonBook(bridge_snapshot)
            except BookDataError as exc:
                structural_errors.append(str(exc))
                book = None
                continue
            segments += 1
        if book is None:
            continue
        if event.final_update_id <= book.last_update_id:
            continue
        if event.first_update_id > book.last_update_id + 1:
            book = None
            continue
        try:
            outcome = book.apply(event)
        except BookDataError as exc:
            structural_errors.append(f"at {event.first_update_id}-{event.final_update_id}: {exc}")
            book = None
            continue
        unseen_removals += outcome.unseen_removals
        outer_unseen_removals += outcome.outer_unseen_removals
        if book.top_trusted:
            trusted_intervals.append((event.first_update_id, event.final_update_id))
        if latest_ticker is not None and latest_ticker.update_id != checked_ticker_id:
            # Only the greatest ticker ID at or before this batch boundary is observable.
            match = book.matches_ticker(latest_ticker)
            if match is None:
                ticker_skipped_untrusted += 1
            else:
                ticker_checks += 1
                if not match:
                    ticker_mismatches += 1
            checked_ticker_id = latest_ticker.update_id
    return {
        "segments": segments,
        "sequence_gaps": sequence_gaps,
        "structural_errors": structural_errors,
        "ticker_checks": ticker_checks,
        "ticker_mismatches": ticker_mismatches,
        "ticker_skipped_untrusted": ticker_skipped_untrusted,
        "unseen_removals": unseen_removals,
        "outer_unseen_removals": outer_unseen_removals,
        "trusted_intervals": merge_intervals(trusted_intervals),
        "last_depth_update_id": previous_depth_u,
    }


def verify_day(
    day: str,
    inputs: list[InputSource],
    output: Path,
    *,
    symbols: list[str] | None = None,
) -> list[Path]:
    date.fromisoformat(day)
    if len(inputs) < 1:
        raise ValueError("at least one recorder input is required")
    if len({source.recorder_id for source in inputs}) != len(inputs):
        raise ValueError("recorder input IDs must be unique")
    selected = symbols or discover_symbols(inputs, day)
    if not selected:
        raise ValueError(f"no symbols found for {day}")
    output.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    for symbol in selected:
        manifests = [verify_manifest(source, symbol, day) for source in inputs]
        intervals_by_source = {
            source.recorder_id: _source_intervals(source, symbol, day) for source in inputs
        }
        joint_intervals = merge_intervals(
            interval for intervals in intervals_by_source.values() for interval in intervals
        )
        denominator = (joint_intervals[0][0], joint_intervals[-1][1]) if joint_intervals else None
        coverage = coverage_summary(joint_intervals, denominator)
        per_recorder = {
            recorder_id: coverage_summary(intervals, denominator)
            for recorder_id, intervals in intervals_by_source.items()
        }
        depth = deduplicate_depth(
            merge_sorted(
                [iter_depth(source, symbol, day) for source in inputs],
                key=lambda event: (event.first_update_id, event.final_update_id, event.rx_utc),
            )
        )
        tickers = deduplicate_tickers(
            merge_sorted(
                [iter_tickers(source, symbol, day) for source in inputs],
                key=lambda event: (event.update_id, event.rx_utc),
            )
        )
        snapshots = [
            snapshot for source in inputs for snapshot in load_snapshots(source, symbol, day)
        ]
        reconstruction = reconstruct(depth, tickers, snapshots)
        trusted = coverage_summary(reconstruction.pop("trusted_intervals"), denominator)
        wall_clock = _wall_clock_summary(inputs, symbol, day)
        quality_issues: list[str] = []
        if coverage["ratio"] < PASS_COVERAGE_RATIO:
            quality_issues.append("joint update-ID coverage is below 99.5%")
        if not wall_clock["complete"]:
            quality_issues.append("wall-clock coverage is incomplete")
        if reconstruction["segments"] < 1:
            quality_issues.append("no snapshot-anchored reconstruction segment exists")
        if reconstruction["structural_errors"]:
            quality_issues.append("reconstruction contains structural errors")
        if trusted["ratio"] < PASS_TRUSTED_RATIO:
            quality_issues.append("trustworthy reconstruction coverage is below 99.5%")
        if reconstruction["ticker_checks"] < 1:
            quality_issues.append("no trusted bookTicker comparison was possible")
        if reconstruction["ticker_mismatches"]:
            quality_issues.append("reconstructed bookTicker values disagree with capture")
        status = "pass" if not quality_issues else "review"
        if coverage["ratio"] < EXCLUDE_COVERAGE_RATIO:
            status = "exclude-or-document"
        report = {
            "schema_version": 1,
            "date_utc": day,
            "symbol": symbol,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "status": status,
            "quality_issues": quality_issues,
            "quality_thresholds": {
                "joint_update_id_coverage": PASS_COVERAGE_RATIO,
                "trusted_reconstruction_coverage": PASS_TRUSTED_RATIO,
                "ticker_mismatches": 0,
            },
            "manifests": [
                {
                    "recorder_id": manifest["recorder_id"],
                    "config_hash": manifest["config_hash"],
                    "run_ids": manifest["run_ids"],
                }
                for manifest in manifests
            ],
            "joint_update_id_coverage": coverage,
            "per_recorder_coverage": per_recorder,
            "wall_clock": wall_clock,
            "reconstruction": {**reconstruction, "trusted_coverage": trusted},
            "monotonic_clock_scope": "recorder_id+run_id only",
        }
        destination = output / f"verification-{day}-{symbol.lower()}.json"
        payload = json.dumps(report, indent=2, sort_keys=True).encode() + b"\n"
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as exc:
            raise FileExistsError(f"verification report is immutable: {destination}") from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        ledger = output / "coverage-ledger.ndjson"
        with ledger.open("ab", buffering=0) as handle:
            handle.write(
                json.dumps(
                    {
                        "date_utc": day,
                        "symbol": symbol,
                        "status": status,
                        "joint_ratio": coverage["ratio"],
                        "trusted_ratio": trusted["ratio"],
                        "report": destination.name,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                + b"\n"
            )
            os.fsync(handle.fileno())
        results.append(destination)
    return results
