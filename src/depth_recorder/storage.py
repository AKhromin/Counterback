from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tempfile
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, BinaryIO

import zstandard as zstd

from . import __version__
from .jsonutil import canonical_json, parse_envelope
from .models import WriteRecord

STREAM_FILE_NAMES = {"depth": "depth", "trade": "trade", "bookTicker": "bookticker"}


def utc_day_and_hour(timestamp_ns: int) -> tuple[str, str]:
    instant = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, UTC)
    return instant.date().isoformat(), f"{instant.hour:02d}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_zstd_lines(path: Path) -> Iterable[bytes]:
    decompressor = zstd.ZstdDecompressor()
    with path.open("rb") as raw:
        with decompressor.stream_reader(raw, read_across_frames=True) as reader:
            pending = b""
            while chunk := reader.read(1024 * 1024):
                pending += chunk
                parts = pending.split(b"\n")
                pending = parts.pop()
                for line in parts:
                    if line:
                        yield line + b"\n"
            if pending:
                raise ValueError(f"compressed file has torn final line: {path}")


class HourlyWriter:
    def __init__(
        self,
        directory: Path,
        stream: str,
        hour: str,
        compression_level: int,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{STREAM_FILE_NAMES[stream]}-{hour}.ndjson"
        self.final_path = directory / f"{stem}.zst"
        self.partial_path = directory / f"{stem}.zst.partial"
        self.wal_path = directory / f"{stem}.wal"
        if self.final_path.exists():
            raise FileExistsError(f"finalized stream file already exists: {self.final_path}")
        self.compression_level = compression_level
        self.line_count = 0
        self.first_rx_utc: int | None = None
        self.last_rx_utc: int | None = None
        self.first_update_id: int | None = None
        self.last_update_id: int | None = None
        self.run_ids: set[str] = set()
        self.recovered_tail: Path | None = None
        if self.wal_path.exists():
            self._repair_wal()
            self._scan_wal()
            self._rebuild_partial()
        elif self.partial_path.exists():
            quarantine = self.partial_path.with_name(self.partial_path.name + ".orphan")
            os.replace(self.partial_path, quarantine)
        self._wal: BinaryIO = self.wal_path.open("ab", buffering=0)
        self._compressed_raw: BinaryIO = self.partial_path.open("ab", buffering=0)
        self._compressor = zstd.ZstdCompressor(level=compression_level).stream_writer(
            self._compressed_raw, closefd=False
        )
        self._since_flush = 0

    def _repair_wal(self) -> None:
        size = self.wal_path.stat().st_size
        if size == 0:
            return
        with self.wal_path.open("rb+") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) == b"\n":
                return
            position = size
            valid_size = 0
            while position > 0:
                scan_size = min(position, 1024 * 1024)
                position -= scan_size
                handle.seek(position)
                tail = handle.read(scan_size)
                newline = tail.rfind(b"\n")
                if newline >= 0:
                    valid_size = position + newline + 1
                    break
            handle.seek(valid_size)
            torn = handle.read()
            quarantine_dir = self.wal_path.parent / "quarantine"
            quarantine_dir.mkdir(exist_ok=True)
            quarantine = quarantine_dir / f"{self.wal_path.name}.{os.getpid()}.tail"
            quarantine.write_bytes(torn)
            self.recovered_tail = quarantine
            handle.truncate(valid_size)
            handle.flush()
            os.fsync(handle.fileno())

    def _scan_wal(self) -> None:
        with self.wal_path.open("rb") as handle:
            for line in handle:
                envelope = parse_envelope(line)
                self.line_count += 1
                rx = int(envelope["rx_utc"])
                self.first_rx_utc = rx if self.first_rx_utc is None else min(self.first_rx_utc, rx)
                self.last_rx_utc = rx if self.last_rx_utc is None else max(self.last_rx_utc, rx)
                self.run_ids.add(str(envelope["run_id"]))
                raw = envelope["raw"]
                data = raw.get("data", raw)
                if isinstance(data, dict) and "U" in data and "u" in data:
                    first, final = int(data["U"]), int(data["u"])
                    self.first_update_id = (
                        first if self.first_update_id is None else min(self.first_update_id, first)
                    )
                    self.last_update_id = (
                        final if self.last_update_id is None else max(self.last_update_id, final)
                    )

    def _rebuild_partial(self) -> None:
        temporary = self.partial_path.with_name(self.partial_path.name + ".rebuild")
        compressor = zstd.ZstdCompressor(level=self.compression_level)
        with self.wal_path.open("rb") as source, temporary.open("wb") as target:
            with compressor.stream_writer(target, closefd=False) as writer:
                shutil.copyfileobj(source, writer)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, self.partial_path)

    def append(self, record: WriteRecord) -> None:
        self._wal.write(record.envelope)
        self._compressor.write(record.envelope)
        self.line_count += 1
        self.first_rx_utc = (
            record.rx_utc if self.first_rx_utc is None else min(self.first_rx_utc, record.rx_utc)
        )
        self.last_rx_utc = (
            record.rx_utc if self.last_rx_utc is None else max(self.last_rx_utc, record.rx_utc)
        )
        self.run_ids.add(record.run_id)
        if record.first_update_id is not None:
            self.first_update_id = (
                record.first_update_id
                if self.first_update_id is None
                else min(self.first_update_id, record.first_update_id)
            )
        if record.final_update_id is not None:
            self.last_update_id = (
                record.final_update_id
                if self.last_update_id is None
                else max(self.last_update_id, record.final_update_id)
            )
        self._since_flush += 1
        if self._since_flush >= 256:
            self._compressor.flush(zstd.FLUSH_BLOCK)
            self._since_flush = 0

    def close_active(self) -> None:
        if getattr(self, "_compressor", None) is None:
            return
        self._compressor.close()  # type: ignore[no-untyped-call]
        self._compressed_raw.flush()
        os.fsync(self._compressed_raw.fileno())
        self._compressed_raw.close()
        self._wal.flush()
        os.fsync(self._wal.fileno())
        self._wal.close()
        self._compressor = None  # type: ignore[assignment]

    def finalize(self) -> dict[str, Any]:
        self.close_active()
        actual_lines = sum(1 for _ in iter_zstd_lines(self.partial_path))
        if actual_lines != self.line_count:
            raise ValueError(
                f"line-count mismatch for {self.partial_path}: {actual_lines} != {self.line_count}"
            )
        os.replace(self.partial_path, self.final_path)
        self.wal_path.unlink()
        return self.metadata()

    def metadata(self) -> dict[str, Any]:
        path = self.final_path if self.final_path.exists() else self.partial_path
        return {
            "path": str(path.relative_to(path.parent.parent)),
            "name": path.name,
            "kind": "stream",
            "line_count": self.line_count,
            "bytes": path.stat().st_size if path.exists() else 0,
            "sha256": sha256_file(path) if self.final_path.exists() else None,
            "first_rx_utc": self.first_rx_utc,
            "last_rx_utc": self.last_rx_utc,
            "first_update_id": self.first_update_id,
            "last_update_id": self.last_update_id,
            "run_ids": sorted(self.run_ids),
            "finalized": self.final_path.exists(),
        }


class StorageManager:
    def __init__(
        self,
        root: Path,
        recorder_id: str,
        run_id: str,
        config_hash: str,
        symbols: tuple[str, ...],
        compression_level: int,
        service_mode: str,
    ) -> None:
        self.root = root
        self.recorder_id = recorder_id
        self.run_id = run_id
        self.config_hash = config_hash
        self.symbols = symbols
        self.compression_level = compression_level
        self.service_mode = service_mode
        self.writers: dict[tuple[str, str, str, str], HourlyWriter] = {}

    def append(self, record: WriteRecord) -> None:
        day, hour = utc_day_and_hour(record.rx_utc)
        key = (record.symbol, record.stream, day, hour)
        writer = self.writers.get(key)
        if writer is None:
            self._finalize_older_writers(record.symbol, record.stream, day, hour)
            directory = self.root / record.symbol.lower() / day
            writer = HourlyWriter(directory, record.stream, hour, self.compression_level)
            self.writers[key] = writer
            if writer.recovered_tail is not None:
                self.append_ledger(
                    "recovered_torn_line",
                    symbol=record.symbol,
                    source=writer.wal_path.name,
                    quarantine=writer.recovered_tail.name,
                )
        writer.append(record)

    def _finalize_older_writers(self, symbol: str, stream: str, day: str, hour: str) -> None:
        for key, writer in list(self.writers.items()):
            if key[0] == symbol and key[1] == stream and (key[2], key[3]) < (day, hour):
                writer.finalize()
                del self.writers[key]

    def append_ledger(self, reason: str, *, symbol: str | None = None, **details: Any) -> None:
        now_utc = int(details.pop("rx_utc", 0)) or __import__("time").time_ns()
        now_mono = int(details.pop("rx_mono", 0)) or __import__("time").monotonic_ns()
        event = {
            "schema_version": 1,
            "recorder_id": self.recorder_id,
            "run_id": self.run_id,
            "reason": reason,
            "rx_utc": now_utc,
            "rx_mono": now_mono,
            **details,
        }
        targets = (symbol,) if symbol else self.symbols
        day, _ = utc_day_and_hour(now_utc)
        payload = canonical_json(event) + b"\n"
        for target in targets:
            directory = self.root / target.lower() / day
            directory.mkdir(parents=True, exist_ok=True)
            ledger = directory / f"ledger-{day}.ndjson"
            with ledger.open("ab", buffering=0) as handle:
                handle.write(payload)
                os.fsync(handle.fileno())

    def write_http_payload(
        self,
        symbol: str,
        kind: str,
        raw: bytes,
        envelope: bytes,
        rx_utc: int,
    ) -> Path:
        day, _ = utc_day_and_hour(rx_utc)
        instant = datetime.fromtimestamp(rx_utc / 1_000_000_000, UTC)
        stamp = instant.strftime("%H%M%S-%f")
        directory = self.root / symbol.lower() / day
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{kind}-{stamp}.json.zst"
        destination = directory / name
        compressed = zstd.ZstdCompressor(level=self.compression_level).compress(
            envelope.rstrip(b"\n")
        )
        fd, temp_name = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(compressed)
                handle.flush()
                os.fsync(handle.fileno())
            if destination.exists():
                raise FileExistsError(destination)
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return destination

    def process_start(self) -> None:
        self.append_ledger(
            "process_start",
            software_version=__version__,
            config_hash=self.config_hash,
            architecture=platform.machine(),
            service_mode=self.service_mode,
            pid=os.getpid(),
        )

    def finalize_day(self, day: date | str) -> list[Path]:
        day_text = day.isoformat() if isinstance(day, date) else day
        writers = self.detach_day_writers(day_text)
        self.finalize_detached_writers(writers)
        return [self._write_manifest(symbol, day_text, partial=False) for symbol in self.symbols]

    def detach_day_writers(self, day: date | str) -> list[HourlyWriter]:
        """Remove a closed day's writers so they can be finalized off the event loop."""
        day_text = day.isoformat() if isinstance(day, date) else day
        detached: list[HourlyWriter] = []
        for key, writer in list(self.writers.items()):
            if key[2] == day_text:
                detached.append(writer)
                del self.writers[key]
        return detached

    @staticmethod
    def finalize_detached_writers(writers: Iterable[HourlyWriter]) -> None:
        for writer in writers:
            writer.finalize()

    def close_partial(self) -> list[Path]:
        for writer in self.writers.values():
            writer.close_active()
        today = datetime.now(UTC).date().isoformat()
        return [self._write_manifest(symbol, today, partial=True) for symbol in self.symbols]

    def finalize_previous_days(self) -> list[Path]:
        today = datetime.now(UTC).date().isoformat()
        results: list[Path] = []
        for symbol in self.symbols:
            symbol_root = self.root / symbol.lower()
            if not symbol_root.exists():
                continue
            for directory in sorted(path for path in symbol_root.iterdir() if path.is_dir()):
                if directory.name < today and len(directory.name) == 10:
                    for wal in sorted(directory.glob("*.ndjson.wal")):
                        stem = wal.name.removesuffix(".ndjson.wal")
                        prefix, hour = stem.rsplit("-", 1)
                        stream = "bookTicker" if prefix == "bookticker" else prefix
                        writer = HourlyWriter(directory, stream, hour, self.compression_level)
                        writer.finalize()
                        if writer.recovered_tail is not None:
                            self.append_ledger(
                                "recovered_torn_line",
                                symbol=symbol,
                                source_day=directory.name,
                                source=wal.name,
                                quarantine=writer.recovered_tail.name,
                            )
                    for orphan in directory.glob("*.zst.partial"):
                        quarantine = directory / "quarantine"
                        quarantine.mkdir(exist_ok=True)
                        os.replace(orphan, quarantine / f"{orphan.name}.orphan")
                    manifest = directory / f"manifest-{directory.name}.json"
                    if not manifest.exists():
                        results.append(self._write_manifest(symbol, directory.name, partial=False))
        return results

    def _write_manifest(self, symbol: str, day: str, *, partial: bool) -> Path:
        directory = self.root / symbol.lower() / day
        directory.mkdir(parents=True, exist_ok=True)
        files: list[dict[str, Any]] = []
        run_ids: set[str] = set()
        for path in sorted(directory.glob("*.zst")):
            info: dict[str, Any] = {
                "name": path.name,
                "kind": "stream" if ".ndjson." in path.name else "http",
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            if ".ndjson." in path.name:
                count = 0
                first_rx: int | None = None
                last_rx: int | None = None
                first_u: int | None = None
                last_u: int | None = None
                for line in iter_zstd_lines(path):
                    record = parse_envelope(line)
                    count += 1
                    rx = int(record["rx_utc"])
                    first_rx = rx if first_rx is None else min(first_rx, rx)
                    last_rx = rx if last_rx is None else max(last_rx, rx)
                    run_ids.add(str(record["run_id"]))
                    raw = record["raw"]
                    data = raw.get("data", raw)
                    if isinstance(data, dict) and "U" in data and "u" in data:
                        first, final = int(data["U"]), int(data["u"])
                        first_u = first if first_u is None else min(first_u, first)
                        last_u = final if last_u is None else max(last_u, final)
                info.update(
                    line_count=count,
                    first_rx_utc=first_rx,
                    last_rx_utc=last_rx,
                    first_update_id=first_u,
                    last_update_id=last_u,
                )
            files.append(info)
        ledger_path = directory / f"ledger-{day}.ndjson"
        ledger: list[dict[str, Any]] = []
        if ledger_path.exists():
            for line in ledger_path.read_bytes().splitlines():
                if line:
                    event = json.loads(line)
                    ledger.append(event)
                    run_ids.add(str(event["run_id"]))
            files.append(
                {
                    "name": ledger_path.name,
                    "kind": "ledger",
                    "bytes": ledger_path.stat().st_size,
                    "sha256": sha256_file(ledger_path),
                    "line_count": len(ledger),
                }
            )
        if partial:
            for key, writer in self.writers.items():
                if key[0] == symbol and key[2] == day:
                    info = writer.metadata()
                    files.append(info)
                    run_ids.update(info["run_ids"])
        gaps = [event for event in ledger if event.get("reason") == "sequence_gap"]
        message_counts: dict[str, int] = {}
        for item in files:
            name = str(item.get("name", ""))
            for prefix, stream in (
                ("depth-", "depth"),
                ("trade-", "trade"),
                ("bookticker-", "bookTicker"),
            ):
                if name.startswith(prefix):
                    message_counts[stream] = message_counts.get(stream, 0) + int(
                        item.get("line_count", 0)
                    )
        manifest = {
            "schema_version": 1,
            "date_utc": day,
            "recorder_id": self.recorder_id,
            "software_version": __version__,
            "config_hash": self.config_hash,
            "symbol": symbol,
            "partial": partial,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "run_ids": sorted(run_ids),
            "files": files,
            "ledger": ledger,
            "summary": {
                "gap_count": len(gaps),
                "gap_span": sum(int(event.get("missing_span", 0)) for event in gaps),
                "message_counts": message_counts,
                "bytes": sum(int(item.get("bytes", 0)) for item in files),
                "disk_free_percent": 100
                * shutil.disk_usage(self.root).free
                / shutil.disk_usage(self.root).total,
            },
        }
        name = f"manifest-{day}{'.partial' if partial else ''}.json"
        destination = directory / name
        temporary = directory / f".{name}.{os.getpid()}"
        temporary.write_bytes(json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        if not partial and destination.exists():
            return destination
        os.replace(temporary, destination)
        if not partial:
            partial_path = directory / f"manifest-{day}.partial.json"
            if partial_path.exists():
                partial_path.unlink()
        return destination
