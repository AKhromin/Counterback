"""WP1: read-only raw inventory, fresh day qualification and immutable preparation cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import zstandard as zstd

from depth_recorder.jsonutil import parse_envelope
from depth_recorder.storage import sha256_file
from depth_recorder.verify import InputSource, parse_input, verify_day

from .contract import AVAILABILITY_VERSION
from .export import Instrument, decimal, export_recording

PREPARATION_VERSION = "counterback-preparation-1"
SCHEMA_VERSION = 1
RULE_FILES = (
    "depth_recorder/book.py",
    "depth_recorder/models.py",
    "depth_recorder/verify.py",
    "depth_recorder/storage.py",
    "depth_recorder/jsonutil.py",
    "counterback_replay/export.py",
    "counterback_replay/dataset.py",
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_new(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sources(inputs: list[InputSource]) -> None:
    if not inputs or len({s.recorder_id for s in inputs}) != len(inputs):
        raise ValueError("recorder input IDs must be nonempty and unique")
    for source in inputs:
        if not source.root.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]+", source.recorder_id):
            raise ValueError("invalid recorder input")


def _read_only_output(path: Path, inputs: list[InputSource]) -> None:
    for source in inputs:
        if path.resolve().is_relative_to(source.root.resolve()):
            raise ValueError("outputs must be outside all raw recorder roots")


def _day(text: str) -> date:
    parsed = date.fromisoformat(text)
    if parsed.isoformat() != text:
        raise ValueError("dates must use YYYY-MM-DD")
    return parsed


def _raw_files(directory: Path, day: str) -> list[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and (
            path.name.endswith(".zst")
            or path.name == f"manifest-{day}.json"
            or path.name == f"ledger-{day}.ndjson"
        )
    )


def inventory(
    inputs: list[InputSource],
    evaluation_start: str,
    *,
    symbol: str = "BTCUSDT",
    start: str | None = None,
    end: str | None = None,
    minimum_recorders: int = 2,
    today: date | None = None,
) -> dict[str, Any]:
    """Only list/stat files. Observed presence is never a checksum/quality pass."""
    _sources(inputs)
    if symbol != "BTCUSDT":
        raise ValueError("WP1 currently supports BTCUSDT only")
    Instrument(symbol, "1", "1")
    cutoff = _day(evaluation_start)
    today = today or datetime.now(UTC).date()
    if not 1 <= minimum_recorders <= len(inputs):
        raise ValueError("minimum recorders must be between one and the number of inputs")
    discovered: set[date] = set()
    for source in inputs:
        directory = source.root / symbol.lower()
        if directory.is_dir():
            for candidate in directory.iterdir():
                if candidate.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate.name):
                    discovered.add(_day(candidate.name))
    closed = sorted(day for day in discovered if day < today)
    if not closed and (start is None or end is None):
        raise ValueError("no completed candidate days discovered")
    first, last = _day(start) if start else closed[0], _day(end) if end else closed[-1]
    if first > last or last >= today:
        raise ValueError("inventory interval must consist of completed UTC days")
    days = []
    current = first
    while current <= last:
        day = current.isoformat()
        rows = []
        for source in inputs:
            directory = source.root / symbol.lower() / day
            row: dict[str, Any] = dict(recorder_id=source.recorder_id, present=directory.is_dir())
            try:
                paths = _raw_files(directory, day) if row["present"] else []
                names = [path.name for path in paths]
                row.update(
                    final_manifest_present=f"manifest-{day}.json" in names,
                    files=[dict(name=path.name, bytes=path.stat().st_size) for path in paths],
                    depth_hours=sorted(
                        {
                            name[6:8]
                            for name in names
                            if re.fullmatch(r"depth-\d{2}\.ndjson\.zst", name)
                        }
                    ),
                    ticker_hours=sorted(
                        {
                            name[11:13]
                            for name in names
                            if re.fullmatch(r"bookticker-\d{2}\.ndjson\.zst", name)
                        }
                    ),
                    snapshots=sum(name.startswith("snapshot-") for name in names),
                    metadata=sum(name.startswith("exchange-info-") for name in names),
                )
            except OSError as exc:
                row["listing_error"] = str(exc)
            rows.append(row)
        joint_hours = set(hour for row in rows for hour in row.get("depth_hours", []))
        days.append(
            dict(
                date_utc=day,
                partition="evaluation" if current >= cutoff else "development",
                state="discovered_unverified"
                if any(row["present"] for row in rows)
                else "unavailable",
                sources=rows,
                joint_missing_depth_hours=sorted({f"{h:02}" for h in range(24)} - joint_hours),
                observed_bytes=sum(item["bytes"] for row in rows for item in row.get("files", [])),
            )
        )
        current += timedelta(days=1)
    result = dict(
        schema_version=SCHEMA_VERSION,
        kind="counterback.inventory",
        symbol=symbol,
        evaluation_start=cutoff.isoformat(),
        minimum_recorders=minimum_recorders,
        inputs=[dict(recorder_id=s.recorder_id, root=str(s.root.resolve())) for s in inputs],
        start=first.isoformat(),
        end=last.isoformat(),
        days=days,
        inspection="names-and-logical-sizes-only; payloads-and-checksums-not-read",
    )
    return {**result, "inventory_id": digest(result)}


def load_inventory(path: Path) -> dict[str, Any]:
    result = _json(path)
    if result.get("kind") != "counterback.inventory" or result.get("schema_version") != 1:
        raise ValueError("unsupported inventory")
    content = {key: value for key, value in result.items() if key != "inventory_id"}
    if result.get("inventory_id") != digest(content):
        raise ValueError("inventory identity mismatch")
    cutoff = _day(result["evaluation_start"])
    for row in result["days"]:
        expected = "evaluation" if _day(row["date_utc"]) >= cutoff else "development"
        if row["partition"] != expected:
            raise ValueError("inventory partition mismatch")
    return result


def _fingerprint(inputs: list[InputSource], symbol: str, day: str) -> dict[str, Any]:
    """Hash all finalized day payloads, including any unlisted files read by the verifier."""
    source_rows = []
    grids: list[Instrument] = []
    for source in inputs:
        directory = source.root / symbol.lower() / day
        manifest = _json(directory / f"manifest-{day}.json")
        if (
            manifest.get("partial")
            or manifest.get("recorder_id") != source.recorder_id
            or manifest.get("date_utc") != day
            or manifest.get("symbol") != symbol
        ):
            raise ValueError("invalid final manifest scope")
        if not isinstance(manifest.get("files"), list):
            raise ValueError("manifest files must be a list")
        listed: dict[str, Any] = {}
        for item in manifest["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ValueError("invalid manifest file entry")
            if not item.get("finalized", True):
                continue
            name = item["name"]
            path = directory / name
            if (
                name in listed
                or path.resolve().parent != directory.resolve()
                or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))
            ):
                raise ValueError("duplicate/unsafe manifest entry or missing checksum")
            listed[name] = item
        paths = _raw_files(directory, day)
        actual = {path.name: path for path in paths}
        if set(listed) - actual.keys():
            raise ValueError("manifest-listed file is missing")
        for name in actual:
            if name.startswith(("depth-", "bookticker-", "snapshot-", "exchange-info-")):
                if name not in listed:
                    raise ValueError(f"unlisted input: {name}")
        files = []
        for path in paths:
            hashed = sha256_file(path)
            size = path.stat().st_size
            if path.name in listed:
                item = listed[path.name]
                if item["sha256"] != hashed or item.get("bytes") != size:
                    raise ValueError(f"manifest checksum/size mismatch: {path.name}")
            files.append(dict(name=path.name, bytes=size, sha256=hashed))
        metadata = [path for path in paths if path.name.startswith("exchange-info-")]
        if not metadata:
            raise ValueError("captured exchangeInfo is missing")
        for path in metadata:
            envelope = parse_envelope(zstd.ZstdDecompressor().decompress(path.read_bytes()))
            if envelope["recorder_id"] != source.recorder_id:
                raise ValueError("metadata recorder identity mismatch")
            raw = envelope["raw"]
            symbols = raw.get("data", raw)["symbols"]
            matches = [item for item in symbols if item["symbol"] == symbol]
            if len(matches) != 1:
                raise ValueError("metadata symbol mismatch")
            filters = matches[0]["filters"]
            ticks = [item["tickSize"] for item in filters if item["filterType"] == "PRICE_FILTER"]
            lots = [item["stepSize"] for item in filters if item["filterType"] == "LOT_SIZE"]
            if len(ticks) != 1 or len(lots) != 1:
                raise ValueError("unique price/lot filters required")
            grids.append(Instrument(symbol, ticks[0], lots[0]))
        source_rows.append(dict(recorder_id=source.recorder_id, files=files))
    instrument = grids[0]
    if any(
        decimal(grid.tick_size) != decimal(instrument.tick_size)
        or decimal(grid.lot_size) != decimal(instrument.lot_size)
        for grid in grids
    ):
        raise ValueError("instrument grid changes within selected sources/day")
    package_root = Path(__file__).resolve().parents[1]
    return dict(
        symbol=symbol,
        date_utc=day,
        sources=source_rows,
        instrument=dict(tick_size=instrument.tick_size, lot_size=instrument.lot_size),
        preparation_version=PREPARATION_VERSION,
        availability_contract=AVAILABILITY_VERSION,
        rule_hashes={name: sha256_file(package_root / name) for name in RULE_FILES},
    )


def validate_bundle(path: Path, identity: str) -> dict[str, Any]:
    marker = _json(path / "bundle.json")
    if (
        marker.get("schema_version") != 1
        or marker.get("dataset_id") != identity
        or digest(marker["identity"]) != identity
    ):
        raise ValueError("cached bundle identity mismatch")
    required = {
        "replay/events.l2",
        "replay/expected.ndjson",
        "replay/evidence.json",
        f"verification/verification-{marker['date_utc']}-{marker['symbol'].lower()}.json",
    }
    if set(marker["artifacts"]) != required:
        raise ValueError("cached bundle artifact list mismatch")
    for name, expected in marker["artifacts"].items():
        target = path / name
        if not target.resolve().is_relative_to(path.resolve()) or sha256_file(target) != expected:
            raise ValueError(f"cached artifact checksum mismatch: {name}")
    return marker


def prepare_day(
    inputs: list[InputSource],
    day: str,
    cache: Path,
    *,
    symbol: str = "BTCUSDT",
    checkpoint_every: int = 10000,
) -> dict[str, Any]:
    _sources(inputs)
    _read_only_output(cache, inputs)
    if symbol != "BTCUSDT":
        raise ValueError("WP1 currently supports BTCUSDT only")
    if _day(day) >= datetime.now(UTC).date():
        raise ValueError("only completed UTC days can be prepared")
    if checkpoint_every <= 0:
        raise ValueError("checkpoint interval must be positive")
    identity = {**_fingerprint(inputs, symbol, day), "checkpoint_every": checkpoint_every}
    dataset_id = digest(identity)
    destination = cache / "bundles" / dataset_id
    if destination.exists():
        marker = validate_bundle(destination, dataset_id)
        return dict(
            state="prepared",
            reused=True,
            dataset_id=dataset_id,
            bundle=str(destination.resolve()),
            summary=marker["summary"],
        )
    staging_root = cache / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{day}-", dir=staging_root))
    try:
        reports = verify_day(day, inputs, staging / "verification", symbols=[symbol])
        report = _json(reports[0])
        if report["status"] != "pass":
            reason = dict(
                state="excluded",
                report_status=report["status"],
                quality_issues=report["quality_issues"],
                diagnostics=str(staging.resolve()),
            )
            write_new(staging / "exclusion.json", reason)
            return reason
        summary = export_recording(
            day,
            inputs,
            reports[0],
            staging / "replay",
            symbol=symbol,
            checkpoint_every=checkpoint_every,
        )
        if _fingerprint(inputs, symbol, day) != {
            k: v for k, v in identity.items() if k != "checkpoint_every"
        }:
            raise ValueError("raw inputs/rules changed during preparation")
        artifacts = [
            *reports,
            staging / "replay/events.l2",
            staging / "replay/expected.ndjson",
            staging / "replay/evidence.json",
        ]
        marker = dict(
            schema_version=1,
            dataset_id=dataset_id,
            identity=identity,
            symbol=symbol,
            date_utc=day,
            summary=summary,
            artifacts={str(path.relative_to(staging)): sha256_file(path) for path in artifacts},
            qualification="verified-reconstruction-only; WP2 availability gate still required",
            invalid_update_id_intervals=report["reconstruction"]["trusted_coverage"]["missing"],
            source_paths=[str(source.root.resolve()) for source in inputs],
        )
        write_new(staging / "bundle.json", marker)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Complete directory publication; never overwrite an existing immutable bundle.
        staging.rename(destination)
        return dict(
            state="prepared",
            reused=False,
            dataset_id=dataset_id,
            bundle=str(destination.resolve()),
            summary=summary,
        )
    except Exception as exc:
        if staging.exists():
            write_new(staging / "failure.json", dict(state="failed_preparation", error=str(exc)))
        raise


def prepare_catalogue(
    observed: dict[str, Any],
    selected: list[str],
    cache: Path,
    *,
    checkpoint_every: int = 10000,
) -> dict[str, Any]:
    inputs = [parse_input(f"{item['recorder_id']}={item['root']}") for item in observed["inputs"]]
    _sources(inputs)
    _read_only_output(cache, inputs)
    days = {row["date_utc"]: row for row in observed["days"]}
    if not selected or len(selected) != len(set(selected)) or set(selected) - days.keys():
        raise ValueError("selected days must be unique members of the inventory")
    rows = []
    for day, row in days.items():
        result: dict[str, Any] = dict(date_utc=day, partition=row["partition"], state=row["state"])
        if day in selected:
            usable = [
                source
                for source in inputs
                if (
                    source.root / observed["symbol"].lower() / day / f"manifest-{day}.json"
                ).is_file()
            ]
            if len(usable) < observed["minimum_recorders"]:
                result.update(state="unavailable", reason="insufficient finalized recorder inputs")
            else:
                try:
                    result.update(
                        prepare_day(
                            usable,
                            day,
                            cache,
                            symbol=observed["symbol"],
                            checkpoint_every=checkpoint_every,
                        )
                    )
                except (ValueError, OSError, KeyError, zstd.ZstdError) as exc:
                    result.update(state="failed_preparation", reason=str(exc))
        rows.append(result)
    return dict(schema_version=1, kind="counterback.catalogue", inventory=observed, days=rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("inventory")
    scan.add_argument("--input", action="append", type=parse_input, required=True)
    scan.add_argument("--evaluation-start", required=True)
    scan.add_argument("--start-date")
    scan.add_argument("--end-date")
    scan.add_argument("--minimum-recorders", type=int, default=2)
    scan.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--inventory", type=Path, required=True)
    selection = prepare.add_mutually_exclusive_group(required=True)
    selection.add_argument("--date", action="append")
    selection.add_argument("--all", action="store_true")
    prepare.add_argument("--cache", type=Path, required=True)
    prepare.add_argument("--checkpoint-every", type=int, default=10000)
    prepare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise FileExistsError("inventory/catalogue output is immutable; choose a new path")
        if args.command == "inventory":
            _read_only_output(args.output, args.input)
            result = inventory(
                args.input,
                args.evaluation_start,
                start=args.start_date,
                end=args.end_date,
                minimum_recorders=args.minimum_recorders,
            )
        else:
            observed = load_inventory(args.inventory)
            raw_inputs = [
                parse_input(f"{s['recorder_id']}={s['root']}") for s in observed["inputs"]
            ]
            _read_only_output(args.output, raw_inputs)
            selected = [row["date_utc"] for row in observed["days"]] if args.all else args.date
            result = prepare_catalogue(
                observed, selected, args.cache, checkpoint_every=args.checkpoint_every
            )
        write_new(args.output, result)
    except (ValueError, OSError, KeyError, zstd.ZstdError) as exc:
        parser.exit(1, f"dataset preparation failed: {exc}\n")
    counts: dict[str, int] = {}
    for row in result["days"]:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    print(
        json.dumps(dict(output=str(args.output.resolve()), days=len(result["days"]), states=counts))
    )
    if args.command == "prepare" and any(
        row["state"] in ("excluded", "failed_preparation", "unavailable")
        for row in result["days"]
        if row["date_utc"] in selected
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
