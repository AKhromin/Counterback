from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import shutil
import time
import uuid
from collections import deque
from datetime import UTC, date, datetime, timedelta
from typing import Any

import aiohttp

from .config import RecorderConfig
from .jsonutil import make_envelope, parse_json_object
from .models import WriteRecord
from .sequence import SequenceTracker
from .storage import StorageManager

LOGGER = logging.getLogger("depth_recorder")


class GeoBlockedError(RuntimeError):
    pass


class RecorderService:
    def __init__(self, config: RecorderConfig) -> None:
        self.config = config
        self.run_id = str(uuid.uuid4())
        self.storage = StorageManager(
            config.output_path,
            config.recorder_id,
            self.run_id,
            config.config_hash,
            config.symbols,
            config.compression_level,
            config.service_mode,
        )
        self.sequence = SequenceTracker()
        self.queue: asyncio.Queue[WriteRecord | None] = asyncio.Queue(config.queue_size)
        self.stop_event = asyncio.Event()
        self.snapshot_events = {symbol: asyncio.Event() for symbol in config.symbols}
        self.last_message_mono: float | None = None
        self.last_message_utc: int | None = None
        self.message_times: deque[float] = deque()
        self.messages_by_stream: dict[str, int] = {}
        self.bytes_written = 0
        self.gaps_today = 0
        self.pending_daily_report: dict[str, Any] | None = None
        self._connected_once = False

    async def run(self) -> None:
        self.storage.finalize_previous_days()
        self.storage.process_start()
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        # Preserve application-level JSON bytes, after normal HTTP content decoding.
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await self._preflight(session)
            writer = asyncio.create_task(self._guard(self._writer_loop(), "writer"), name="writer")
            workers = [
                asyncio.create_task(
                    self._guard(self._websocket_supervisor(session), "websocket"),
                    name="websocket",
                ),
                asyncio.create_task(
                    self._guard(self._heartbeat_loop(session), "heartbeat"), name="heartbeat"
                ),
                asyncio.create_task(
                    self._guard(self._day_close_loop(), "day-close"), name="day-close"
                ),
                asyncio.create_task(
                    self._guard(self._metadata_loop(session), "metadata"), name="metadata"
                ),
            ]
            workers.extend(
                asyncio.create_task(
                    self._guard(self._snapshot_worker(session, symbol), f"snapshot-{symbol}"),
                    name=f"snapshot-{symbol}",
                )
                for symbol in self.config.symbols
            )
            workers.extend(
                asyncio.create_task(
                    self._guard(self._snapshot_schedule(symbol), f"schedule-{symbol}"),
                    name=f"schedule-{symbol}",
                )
                for symbol in self.config.symbols
            )
            try:
                await self.stop_event.wait()
            finally:
                for task in workers:
                    task.cancel()
                worker_results = await asyncio.gather(*workers, return_exceptions=True)
                worker_errors = [
                    result
                    for result in worker_results
                    if isinstance(result, BaseException)
                    and not isinstance(result, asyncio.CancelledError)
                ]
                writer_error: BaseException | None = None
                if writer.done():
                    if not writer.cancelled():
                        writer_error = writer.exception()
                    while not self.queue.empty():
                        self.queue.get_nowait()
                        self.queue.task_done()
                else:
                    await self.queue.join()
                    await self.queue.put(None)
                    await writer
                self.storage.append_ledger("process_stop")
                self.storage.close_partial()
                if writer_error is not None:
                    raise RuntimeError("writer task failed") from writer_error
                if worker_errors:
                    raise RuntimeError("background task failed") from worker_errors[0]

    async def _guard(self, coroutine: Any, name: str) -> Any:
        try:
            return await coroutine
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "background task failed", extra={"event": "task_failure", "task": name}
            )
            self.request_stop()
            raise

    def request_stop(self) -> None:
        self.stop_event.set()

    async def _preflight(self, session: aiohttp.ClientSession) -> None:
        url = f"{self.config.rest_base_url.rstrip('/')}/api/v3/ping"
        async with session.get(url) as response:
            if response.status == 451:
                raise GeoBlockedError(
                    "Binance returned HTTP 451; deploy or test from an approved non-US region"
                )
            if response.status != 200:
                raise RuntimeError(f"Binance preflight failed with HTTP {response.status}")
            await response.read()

    async def _writer_loop(self) -> None:
        while True:
            record = await self.queue.get()
            try:
                if record is None:
                    return
                self.storage.append(record)
                self.bytes_written += len(record.envelope)
            finally:
                self.queue.task_done()

    async def _websocket_supervisor(self, session: aiohttp.ClientSession) -> None:
        attempt = 0
        while not self.stop_event.is_set():
            reason = "disconnect"
            try:
                url = self.config.combined_stream_url()
                # autoping echoes server PING payloads. No heartbeat means no client PING loop.
                async with session.ws_connect(url, autoping=True, heartbeat=None) as websocket:
                    attempt = 0
                    self.storage.append_ledger(
                        "reconnect" if self._connected_once else "connection_open"
                    )
                    self._connected_once = True
                    for event in self.snapshot_events.values():
                        event.set()
                    connected_at = time.monotonic()
                    reconnect_after = self._stable_reconnect_age()
                    while not self.stop_event.is_set():
                        remaining = reconnect_after - (time.monotonic() - connected_at)
                        timeout = max(0.01, min(self.config.stall_timeout_seconds, remaining))
                        try:
                            message = await websocket.receive(timeout=timeout)
                        except TimeoutError:
                            if time.monotonic() - connected_at >= reconnect_after:
                                reason = "proactive_reconnect"
                            else:
                                reason = "stall"
                            self.storage.append_ledger(reason)
                            break
                        if message.type == aiohttp.WSMsgType.TEXT:
                            should_reconnect = await self._handle_frame(message.data)
                            if should_reconnect:
                                reason = "server_shutdown"
                                break
                        elif message.type in {
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            reason = "disconnect"
                            break
                    await websocket.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "websocket failure", extra={"event": "websocket_error", "error": str(exc)}
                )
                self.storage.append_ledger("disconnect", error=str(exc))
            if self.stop_event.is_set():
                return
            if reason not in {"stall", "proactive_reconnect", "server_shutdown"}:
                self.storage.append_ledger("disconnect", detail=reason)
            delay = self._backoff(attempt)
            attempt += 1
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _handle_frame(self, raw: str) -> bool:
        rx_utc, rx_mono = time.time_ns(), time.monotonic_ns()
        parsed = parse_json_object(raw)
        stream_name = str(parsed.get("stream", ""))
        data = parsed.get("data")
        if stream_name == "!serverShutdown" or (
            isinstance(data, dict) and data.get("e") == "serverShutdown"
        ):
            self.storage.append_ledger("server_shutdown", rx_utc=rx_utc, rx_mono=rx_mono)
            return True
        if not isinstance(data, dict) or "@" not in stream_name:
            LOGGER.warning("unroutable websocket frame", extra={"event": "unroutable_frame"})
            return False
        symbol_part, stream_part = stream_name.split("@", 1)
        symbol = symbol_part.upper()
        stream_lower = stream_part.lower()
        if stream_lower.startswith("depth"):
            stream = "depth"
        elif stream_lower == "trade":
            stream = "trade"
        elif stream_lower == "bookticker":
            stream = "bookTicker"
        else:
            LOGGER.warning(
                "unknown stream", extra={"event": "unknown_stream", "stream": stream_name}
            )
            return False
        if symbol not in self.config.symbols:
            LOGGER.warning("unknown symbol", extra={"event": "unknown_symbol", "symbol": symbol})
            return False
        first = int(data["U"]) if stream == "depth" else None
        final = int(data["u"]) if stream == "depth" else None
        record = WriteRecord(
            symbol=symbol,
            stream=stream,
            rx_utc=rx_utc,
            rx_mono=rx_mono,
            envelope=make_envelope(
                raw,
                recorder_id=self.config.recorder_id,
                run_id=self.run_id,
                rx_utc=rx_utc,
                rx_mono=rx_mono,
            ),
            first_update_id=first,
            final_update_id=final,
            run_id=self.run_id,
        )
        backpressured = self.queue.full()
        if backpressured:
            self.storage.append_ledger(
                "writer_backpressure", symbol=symbol, queue_size=self.queue.qsize()
            )
            await self.queue.put(record)
        else:
            self.queue.put_nowait(record)
        now = time.monotonic()
        self.last_message_mono = now
        self.last_message_utc = rx_utc
        self.message_times.append(now)
        self.messages_by_stream[stream] = self.messages_by_stream.get(stream, 0) + 1
        if stream == "depth":
            assert first is not None and final is not None
            gap = self.sequence.observe(symbol, first, final)
            if gap is not None:
                self.gaps_today += 1
                self.storage.append_ledger(
                    "sequence_gap",
                    symbol=symbol,
                    prev_u=gap.prev_u,
                    first_update_id=gap.first_update_id,
                    final_update_id=gap.final_update_id,
                    missing_span=gap.missing_span,
                    rx_utc=rx_utc,
                    rx_mono=rx_mono,
                )
                self.snapshot_events[symbol].set()
        return backpressured

    async def _snapshot_worker(self, session: aiohttp.ClientSession, symbol: str) -> None:
        event = self.snapshot_events[symbol]
        while True:
            await event.wait()
            event.clear()
            await self._fetch_and_store(session, symbol, "snapshot")

    async def _snapshot_schedule(self, symbol: str) -> None:
        sequence = 0
        while True:
            delay = self._stable_jitter(symbol, "snapshot", sequence)
            sequence += 1
            await asyncio.sleep(delay)
            self.snapshot_events[symbol].set()

    async def _metadata_loop(self, session: aiohttp.ClientSession) -> None:
        while True:
            for symbol in self.config.symbols:
                await self._fetch_and_store(session, symbol, "exchange-info")
            now = datetime.now(UTC)
            tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
            await asyncio.sleep((tomorrow - now).total_seconds() + 300)

    async def _fetch_and_store(
        self, session: aiohttp.ClientSession, symbol: str, kind: str
    ) -> None:
        endpoint = "depth" if kind == "snapshot" else "exchangeInfo"
        params: dict[str, str | int] = {"symbol": symbol}
        if kind == "snapshot":
            params["limit"] = 5000
        url = f"{self.config.rest_base_url.rstrip('/')}/api/v3/{endpoint}"
        for attempt in range(6):
            try:
                async with session.get(url, params=params) as response:
                    body = await response.read()
                    if response.status == 451:
                        raise GeoBlockedError("Binance REST endpoint returned HTTP 451")
                    if response.status == 429:
                        retry_after = float(response.headers.get("Retry-After", "1"))
                        if attempt == 5:
                            raise RuntimeError("HTTP 429 after maximum retries")
                        await asyncio.sleep(min(retry_after, 60))
                        continue
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}")
                    rx_utc, rx_mono = time.time_ns(), time.monotonic_ns()
                    envelope = make_envelope(
                        body,
                        recorder_id=self.config.recorder_id,
                        run_id=self.run_id,
                        rx_utc=rx_utc,
                        rx_mono=rx_mono,
                    )
                    self.storage.write_http_payload(symbol, kind, body, envelope, rx_utc)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt == 5:
                    self.storage.append_ledger(
                        "snapshot_failure" if kind == "snapshot" else "metadata_failure",
                        symbol=symbol,
                        error=str(exc),
                    )
                    return
                await asyncio.sleep(min(2**attempt, 30))

    async def _heartbeat_loop(self, session: aiohttp.ClientSession) -> None:
        url = self.config.heartbeat_url
        if not url:
            LOGGER.info("heartbeat disabled", extra={"event": "heartbeat_disabled"})
        while True:
            await asyncio.sleep(self.config.heartbeat_interval_seconds)
            now = time.monotonic()
            cutoff = now - 60
            while self.message_times and self.message_times[0] < cutoff:
                self.message_times.popleft()
            age = None if self.last_message_mono is None else now - self.last_message_mono
            usage = shutil.disk_usage(self.config.output_path)
            used_percent = 100 * usage.used / usage.total
            payload = {
                "recorder_id": self.config.recorder_id,
                "run_id": self.run_id,
                "messages_per_minute": len(self.message_times),
                "seconds_since_last_message": age,
                "open_gaps_today": self.gaps_today,
                "bytes_written": self.bytes_written,
                "writer_backlog": self.queue.qsize(),
                "disk_free_percent": 100 - used_percent,
            }
            if self.pending_daily_report is not None:
                payload["daily_report"] = self.pending_daily_report
            LOGGER.info("heartbeat", extra={"event": "heartbeat", **payload})
            if url:
                unhealthy = (
                    age is None
                    or age >= self.config.freshness_failure_seconds
                    or used_percent >= self.config.disk_failure_percent
                )
                target = f"{url.rstrip('/')}/fail" if unhealthy else url
                try:
                    async with session.post(target, json=payload) as response:
                        await response.read()
                        if response.status >= 400:
                            raise RuntimeError(f"heartbeat HTTP {response.status}")
                except Exception as exc:
                    LOGGER.warning(
                        "heartbeat delivery failed",
                        extra={"event": "heartbeat_error", "error": str(exc)},
                    )
            self.pending_daily_report = None

    async def _day_close_loop(self) -> None:
        while True:
            now = datetime.now(UTC)
            tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
            await asyncio.sleep((tomorrow - now).total_seconds())
            report = {
                "date_utc": now.date().isoformat(),
                "message_counts": dict(self.messages_by_stream),
                "gap_count": self.gaps_today,
                "bytes_written": self.bytes_written,
                "manifest_status": "deferred_to_offload",
            }
            self.gaps_today = 0
            self.messages_by_stream.clear()
            self.bytes_written = 0
            await self._finalize_closed_day_streams(now.date())
            self.pending_daily_report = report
            LOGGER.info("daily report", extra={"event": "daily_report", **report})

    async def _finalize_closed_day_streams(self, day: date) -> None:
        # Drain pre-boundary records, then detach without yielding. Current-day writers
        # can continue while the closed files are validated and renamed in a worker.
        await self.queue.join()
        writers = self.storage.detach_day_writers(day)
        await asyncio.to_thread(self.storage.finalize_detached_writers, writers)
        self.storage.append_ledger("day_streams_finalized", source_day=day.isoformat())

    def _stable_jitter(self, symbol: str, purpose: str, sequence: int) -> float:
        base = self.config.snapshot_interval_seconds
        seed = f"{self.config.recorder_id}:{symbol}:{purpose}:{datetime.now(UTC).date()}:{sequence}"
        value = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16) / (16**16 - 1)
        offset = (2 * value - 1) * self.config.snapshot_jitter_fraction
        return base * (1 + offset)

    def _stable_reconnect_age(self) -> float:
        seed = int(hashlib.sha256(self.config.recorder_id.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        return float(self.config.proactive_reconnect_seconds + rng.uniform(-300, 300))

    def _backoff(self, attempt: int) -> float:
        cap = min(
            self.config.reconnect_max_seconds,
            self.config.reconnect_min_seconds * (2**attempt),
        )
        seed = f"{self.config.recorder_id}:{self.run_id}:{attempt}"
        value = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16) / (16**16 - 1)
        return float(max(self.config.reconnect_min_seconds, cap * (0.5 + 0.5 * value)))


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ignored = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        payload.update({key: value for key, value in record.__dict__.items() if key not in ignored})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
