"""Deterministic integer adapter and HorizonBook oracle for offline replay.

No exchange requests, recorder writes, floating-point arithmetic or C++ dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, TextIO

import zstandard as zstd

from depth_recorder.book import BookDataError, HorizonBook
from depth_recorder.jsonutil import parse_envelope
from depth_recorder.models import DepthEvent, Snapshot
from depth_recorder.storage import sha256_file
from depth_recorder.verify import (
    InputSource,
    coverage_summary,
    deduplicate_depth,
    iter_depth,
    load_snapshots,
    merge_sorted,
    parse_input,
    verify_manifest,
)

MAX_UNITS = (1 << 63) - 1
MAX_ID = (1 << 64) - 1


def decimal(value: str) -> Decimal:
    # Binance uses unsigned fixed-point text. Bound input before allocating big integers.
    if len(value) > 128 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        raise ValueError(f"invalid fixed-point decimal: {value!r}")
    return Decimal(value)


@lru_cache(maxsize=131072)
def exact_units(value: str, step: str) -> int:
    numerator, denominator = decimal(value).as_integer_ratio()
    step_numerator, step_denominator = decimal(step).as_integer_ratio()
    if step_numerator <= 0:
        raise ValueError("instrument step must be positive")
    units, remainder = divmod(numerator * step_denominator, denominator * step_numerator)
    if remainder or units > MAX_UNITS:
        raise ValueError(f"decimal {value!r} is off-grid or outside int64 for step {step!r}")
    return units


@dataclass(frozen=True)
class Instrument:
    symbol: str
    tick_size: str
    lot_size: str

    def __post_init__(self) -> None:
        token(self.symbol)
        if decimal(self.tick_size) <= 0 or decimal(self.lot_size) <= 0:
            raise ValueError("instrument tick and lot sizes must be positive")

    def levels(self, levels: Iterable[tuple[str, str]]) -> list[list[int]]:
        result = []
        for price, quantity in levels:
            p, q = exact_units(price, self.tick_size), exact_units(quantity, self.lot_size)
            if p <= 0:
                raise ValueError("price must be positive")
            result.append([p, q])
        return result


def token(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError(f"invalid replay identity: {value!r}")
    return value


def unsigned(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_ID:
        raise ValueError("integer outside uint64 range")
    return value


def _append(intervals: list[list[int]], first: int, last: int) -> None:
    if not intervals or first > intervals[-1][1] + 1:
        intervals.append([first, last])
    else:
        intervals[-1][1] = max(last, intervals[-1][1])


def _depth_key(event: DepthEvent) -> tuple[int, int, int]:
    return event.first_update_id, event.final_update_id, event.rx_utc


class ReferenceReplay:
    """Verifier scheduling with the unchanged HorizonBook as the state oracle.

    Tests additionally compare these aggregate results to verify.reconstruct.
    Snapshot-list order deliberately matches that function, including across sources.
    """

    def __init__(self, instrument: Instrument, snapshots: list[Snapshot]) -> None:
        self.instrument = instrument
        self.snapshots = snapshots
        self.index = 0
        self.book: HorizonBook | None = None
        self.previous: DepthEvent | None = None
        self.status = "unanchored"
        self.stats: dict[str, Any] = dict(
            applied=0,
            duplicates=0,
            stale=0,
            unanchored=0,
            segments=0,
            sequence_gaps=0,
            unseen_removals=0,
            outer_unseen_removals=0,
            last_depth_update_id=None,
            trusted_intervals=[],
            invalid_intervals=[],
            gaps=[],
            structural_errors=[],
        )
        self.events = 0

    def apply(self, event: DepthEvent) -> None:
        if event.symbol != self.instrument.symbol or event.first_update_id > event.final_update_id:
            raise ValueError("event scope or update range mismatch")
        previous = self.previous
        if previous is not None:
            if _depth_key(event) < _depth_key(previous):
                raise ValueError("depth stream is not sorted by U, u, rx_utc")
            # Reuse the verifier's raw decimal duplicate-conflict rule, before conversion.
            pair = list(deduplicate_depth([previous, event]))
            if len(pair) == 1:
                self.events += 1
                self.previous = event
                self.stats["duplicates"] += 1
                self.status = "duplicate"
                return
        self.previous = event
        self.events += 1
        first, final = event.first_update_id, event.final_update_id
        last = self.stats["last_depth_update_id"]
        if last is not None and first > last + 1:
            self.stats["sequence_gaps"] += 1
            _append(self.stats["gaps"], last + 1, first - 1)
            _append(self.stats["invalid_intervals"], last + 1, first - 1)
        self.stats["last_depth_update_id"] = max(last or 0, final)
        bridge = None
        while (
            self.index < len(self.snapshots) and self.snapshots[self.index].last_update_id < final
        ):
            candidate = self.snapshots[self.index]
            self.index += 1
            if first <= candidate.last_update_id + 1 <= final:
                bridge = candidate
        if bridge is not None:
            try:
                self.book = HorizonBook(bridge)
            except BookDataError as exc:
                self.book = None
                self.stats["structural_errors"].append(str(exc))
                self._invalid(event, "structural_error")
                return
            self.stats["segments"] += 1
        if self.book is None:
            self.stats["unanchored"] += 1
            self._invalid(event, "unanchored")
            return
        if final <= self.book.last_update_id:
            self.stats["stale"] += 1
            self.status = "stale"
            return
        if first > self.book.last_update_id + 1:
            self.book = None
            self._invalid(event, "gap")
            return
        try:
            outcome = self.book.apply(event)
        except BookDataError as exc:
            self.book = None
            self.stats["structural_errors"].append(f"at {first}-{final}: {exc}")
            self._invalid(event, "structural_error")
            return
        self.stats["applied"] += 1
        self.stats["unseen_removals"] += outcome.unseen_removals
        self.stats["outer_unseen_removals"] += outcome.outer_unseen_removals
        _append(
            self.stats["trusted_intervals" if self.book.top_trusted else "invalid_intervals"],
            first,
            final,
        )
        self.status = "applied"

    def _invalid(self, event: DepthEvent, status: str) -> None:
        self.status = status
        _append(self.stats["invalid_intervals"], event.first_update_id, event.final_update_id)

    def checkpoint(self, *, summary: bool = False) -> dict[str, Any]:
        book, instrument = self.book, self.instrument

        def best(value: tuple[Decimal, Decimal] | None) -> list[int] | None:
            return (
                instrument.levels([(format(value[0], "f"), format(value[1], "f"))])[0]
                if value
                else None
            )

        bid, ask = best(book.best_bid) if book else None, best(book.best_ask) if book else None
        state = dict(
            kind="summary" if summary else "checkpoint",
            event=self.events,
            status=self.status,
            last_update_id=book.last_update_id if book else None,
            bid_trusted=book.bid_trusted if book else False,
            ask_trusted=book.ask_trusted if book else False,
            bid_boundary=exact_units(format(book.bid_boundary, "f"), instrument.tick_size)
            if book
            else None,
            ask_boundary=exact_units(format(book.ask_boundary, "f"), instrument.tick_size)
            if book
            else None,
            best_bid=bid,
            best_ask=ask,
            spread_ticks=ask[0] - bid[0] if bid and ask else None,
            bids=instrument.levels(
                (format(p, "f"), format(q, "f")) for p, q in sorted(book.bids.items(), reverse=True)
            )
            if book
            else [],
            asks=instrument.levels(
                (format(p, "f"), format(q, "f")) for p, q in sorted(book.asks.items())
            )
            if book
            else [],
        )
        if summary:
            state.update(self.stats)
        return state


def _write_record(handle: TextIO, prefix: list[str | int], levels: list[list[int]]) -> None:
    handle.write(
        " ".join(str(value) for value in [*prefix, *(v for level in levels for v in level)])
    )
    handle.write("\n")


def export_events(
    output: Path,
    instrument: Instrument,
    snapshots: list[Snapshot],
    events: Iterable[DepthEvent],
    *,
    checkpoint_every: int = 1,
) -> dict[str, Any]:
    if checkpoint_every <= 0:
        raise ValueError("checkpoint interval must be positive")
    reference = ReferenceReplay(instrument, snapshots)
    with (
        (output / "events.l2").open("x") as handle,
        (output / "expected.ndjson").open("x") as oracle,
    ):
        handle.write(
            f"COUNTERBACK_L2 1 {instrument.symbol} {instrument.tick_size} {instrument.lot_size}\n"
        )
        for snapshot in snapshots:
            if snapshot.symbol != instrument.symbol:
                raise ValueError("snapshot symbol mismatch")
            bids, asks = instrument.levels(snapshot.bids), instrument.levels(snapshot.asks)
            _write_record(
                handle,
                [
                    "S",
                    unsigned(snapshot.last_update_id),
                    unsigned(snapshot.rx_utc),
                    token(snapshot.recorder_id),
                    token(snapshot.run_id),
                    len(bids),
                    len(asks),
                ],
                bids + asks,
            )
        handle.write("BEGIN\n")
        for event in events:
            bids, asks = instrument.levels(event.bids), instrument.levels(event.asks)
            reference.apply(event)
            _write_record(
                handle,
                [
                    "D",
                    unsigned(event.first_update_id),
                    unsigned(event.final_update_id),
                    unsigned(event.rx_utc),
                    unsigned(event.rx_mono),
                    unsigned(event.raw_data.get("E", 0)),
                    token(event.recorder_id),
                    token(event.run_id),
                    len(bids),
                    len(asks),
                ],
                bids + asks,
            )
            if reference.events % checkpoint_every == 0:
                oracle.write(json.dumps(reference.checkpoint(), separators=(",", ":")) + "\n")
        handle.write(f"END {reference.events}\n")
        summary = reference.checkpoint(summary=True)
        oracle.write(json.dumps(summary, separators=(",", ":")) + "\n")
    return summary


def _ordered_depth(source: InputSource, symbol: str, day: str) -> Iterator[DepthEvent]:
    previous: tuple[int, int, int] | None = None
    for event in iter_depth(source, symbol, day):
        key = (event.first_update_id, event.final_update_id, event.rx_utc)
        if previous is not None and key < previous:
            raise ValueError(f"recorder stream is not sorted: {source.recorder_id}")
        if event.recorder_id != source.recorder_id or event.symbol != symbol:
            raise ValueError("depth envelope identity mismatch")
        previous = key
        yield event


def export_recording(
    day: str,
    inputs: list[InputSource],
    report_path: Path,
    output: Path,
    *,
    symbol: str = "BTCUSDT",
    checkpoint_every: int = 10000,
) -> dict[str, Any]:
    date.fromisoformat(day)
    if not inputs or len({source.recorder_id for source in inputs}) != len(inputs):
        raise ValueError("nonempty, unique recorder inputs are required")
    report = json.loads(report_path.read_text())
    if (
        report.get("schema_version") != 1
        or report.get("status") != "pass"
        or report.get("date_utc") != day
        or report.get("symbol") != symbol
        or report.get("quality_issues")
    ):
        raise ValueError("a passing verification report for the requested day/symbol is required")
    manifests = [verify_manifest(source, symbol, day) for source in inputs]
    identities = [
        dict(recorder_id=m["recorder_id"], config_hash=m["config_hash"], run_ids=m["run_ids"])
        for m in manifests
    ]
    if identities != report["manifests"]:
        raise ValueError("verification report recorder identities/order do not match inputs")
    metadata: list[dict[str, str]] = []
    instruments: list[Instrument] = []
    snapshots: list[Snapshot] = []
    manifest_hashes = []
    for source, manifest in zip(inputs, manifests, strict=True):
        directory = source.root / symbol.lower() / day
        manifest_hashes.append(sha256_file(directory / f"manifest-{day}.json"))
        files = {item["name"]: item for item in manifest["files"] if item.get("finalized", True)}
        # The verifier allows missing checksums/unlisted files; replay requires exact provenance.
        required = [
            *directory.glob("depth-*.ndjson.zst"),
            *directory.glob("snapshot-*.json.zst"),
            *directory.glob("exchange-info-*.json.zst"),
        ]
        for path in required:
            item = files.get(path.name)
            if (
                item is None
                or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))
                or item.get("bytes") != path.stat().st_size
            ):
                raise ValueError(f"replay input lacks finalized checksum/size evidence: {path}")
        info_paths = sorted(directory.glob("exchange-info-*.json.zst"))
        if not info_paths:
            raise ValueError(f"missing captured exchangeInfo: {directory}")
        for path in info_paths:
            payload = zstd.ZstdDecompressor().decompress(path.read_bytes())
            envelope = parse_envelope(payload)
            if envelope["recorder_id"] != source.recorder_id:
                raise ValueError("metadata recorder identity mismatch")
            raw = envelope["raw"]
            data = raw.get("data", raw)
            selected = [item for item in data["symbols"] if item["symbol"] == symbol]
            if len(selected) != 1:
                raise ValueError("exchangeInfo must contain the requested symbol exactly once")
            filters = selected[0]["filters"]
            prices = [f["tickSize"] for f in filters if f["filterType"] == "PRICE_FILTER"]
            lots = [f["stepSize"] for f in filters if f["filterType"] == "LOT_SIZE"]
            if len(prices) != 1 or len(lots) != 1:
                raise ValueError("unique PRICE_FILTER and LOT_SIZE are required")
            instruments.append(Instrument(symbol, prices[0], lots[0]))
            metadata.append(
                dict(recorder_id=source.recorder_id, name=path.name, sha256=sha256_file(path))
            )
        snapshots.extend(load_snapshots(source, symbol, day))
    instrument = instruments[0]
    if any(
        decimal(i.tick_size) != decimal(instrument.tick_size)
        or decimal(i.lot_size) != decimal(instrument.lot_size)
        for i in instruments
    ):
        raise ValueError("instrument grid changed within recording; split the replay explicitly")
    if not snapshots:
        raise ValueError("no snapshots in recording")
    output.mkdir(parents=True, exist_ok=False)
    events = merge_sorted(
        [_ordered_depth(source, symbol, day) for source in inputs],
        key=lambda e: (e.first_update_id, e.final_update_id, e.rx_utc),
    )
    summary = export_events(
        output, instrument, snapshots, events, checkpoint_every=checkpoint_every
    )
    reconstruction = report["reconstruction"]
    for key in (
        "segments",
        "sequence_gaps",
        "structural_errors",
        "last_depth_update_id",
        "unseen_removals",
        "outer_unseen_removals",
    ):
        if summary[key] != reconstruction[key]:
            raise ValueError(f"reference replay disagrees with verification report: {key}")
    coverage = report["joint_update_id_coverage"]
    trusted = coverage_summary(
        summary["trusted_intervals"], (coverage["first_update_id"], coverage["last_update_id"])
    )
    if trusted != reconstruction["trusted_coverage"]:
        raise ValueError("reference trusted intervals disagree with verification report")
    evidence = dict(
        schema_version=1,
        symbol=symbol,
        date_utc=day,
        tick_size=instrument.tick_size,
        lot_size=instrument.lot_size,
        checkpoint_every=checkpoint_every,
        snapshots=len(snapshots),
        verification_report_sha256=sha256_file(report_path),
        manifest_sha256=manifest_hashes,
        manifests=identities,
        metadata=metadata,
        events_sha256=sha256_file(output / "events.l2"),
        expected_sha256=sha256_file(output / "expected.ndjson"),
    )
    # Completion marker is written only after report parity and all input checks succeed.
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return {k: v for k, v in summary.items() if k not in ("bids", "asks")}


def compare(expected: Path, actual: Path) -> dict[str, Any]:
    count = 0
    final: dict[str, Any] = {}
    with expected.open() as reference, actual.open() as candidate:
        while True:
            left, right = reference.readline(), candidate.readline()
            if not left and not right:
                break
            count += 1
            if not left or not right:
                raise ValueError(f"checkpoint count differs at row {count}")
            a, b = json.loads(left), json.loads(right)
            if a != b:
                keys = sorted(key for key in a.keys() | b.keys() if a.get(key) != b.get(key))
                raise ValueError(
                    f"checkpoint {count}, event {a.get('event')}: differing fields {keys}"
                )
            final = a
    if final.get("kind") != "summary":
        raise ValueError("missing final summary")
    return dict(
        checkpoints_compared=count - 1,
        full_observed_depth_equal=True,
        **{k: v for k, v in final.items() if k not in ("bids", "asks")},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--date", required=True)
    export.add_argument("--input", action="append", type=parse_input, required=True)
    export.add_argument("--verification-report", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--checkpoint-every", type=int, default=10000)
    check = sub.add_parser("compare")
    check.add_argument("--expected", type=Path, required=True)
    check.add_argument("--actual", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            result = export_recording(
                args.date,
                args.input,
                args.verification_report,
                args.output,
                checkpoint_every=args.checkpoint_every,
            )
        else:
            result = compare(args.expected, args.actual)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"replay adapter failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
