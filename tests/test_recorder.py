from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from depth_recorder.config import RecorderConfig, validate_config
from depth_recorder.recorder import GeoBlockedError, JsonLogFormatter, RecorderService


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"{}", headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def read(self) -> bytes:
        return self.body


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append((url, params))
        return self.responses.pop(0)

    def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
        self.calls.append((url, json))
        return self.responses.pop(0)


def service(tmp_path: Path, **changes: object) -> RecorderService:
    config = validate_config(
        RecorderConfig(recorder_id="recorder-a", output_dir=str(tmp_path), **changes)
    )
    return RecorderService(config)


async def test_preflight_success_and_failures(tmp_path: Path) -> None:
    recorder = service(tmp_path)
    await recorder._preflight(FakeSession([FakeResponse(200)]))  # type: ignore[arg-type]
    with pytest.raises(GeoBlockedError):
        await recorder._preflight(FakeSession([FakeResponse(451)]))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="503"):
        await recorder._preflight(FakeSession([FakeResponse(503)]))  # type: ignore[arg-type]


async def test_handle_routes_messages_and_detects_gap(tmp_path: Path) -> None:
    recorder = service(tmp_path)
    first = json.dumps(
        {
            "stream": "btcusdt@depth@100ms",
            "data": {"s": "BTCUSDT", "U": 10, "u": 10, "b": [], "a": []},
        }
    )
    gap = json.dumps(
        {
            "stream": "btcusdt@depth@100ms",
            "data": {"s": "BTCUSDT", "U": 13, "u": 14, "b": [], "a": []},
        }
    )
    trade = json.dumps({"stream": "btcusdt@trade", "data": {"s": "BTCUSDT", "t": 1, "p": "1"}})
    ticker = json.dumps(
        {
            "stream": "btcusdt@bookTicker",
            "data": {"s": "BTCUSDT", "u": 14, "b": "1", "B": "1", "a": "2", "A": "1"},
        }
    )
    assert await recorder._handle_frame(first) is False
    assert await recorder._handle_frame(gap) is False
    assert await recorder._handle_frame(trade) is False
    assert await recorder._handle_frame(ticker) is False
    assert recorder.queue.qsize() == 4
    assert recorder.gaps_today == 1
    assert recorder.snapshot_events["BTCUSDT"].is_set()
    ledger = next(tmp_path.glob("btcusdt/*/ledger-*.ndjson"))
    events = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert events[-1]["reason"] == "sequence_gap"
    assert events[-1]["missing_span"] == 2


async def test_handle_control_and_unknown_frames(tmp_path: Path) -> None:
    recorder = service(tmp_path)
    assert await recorder._handle_frame(
        '{"stream":"!serverShutdown","data":{"e":"serverShutdown"}}'
    )
    assert not await recorder._handle_frame('{"result":null}')
    assert not await recorder._handle_frame('{"stream":"btcusdt@unknown","data":{"s":"BTCUSDT"}}')
    assert not await recorder._handle_frame('{"stream":"ethusdt@trade","data":{"s":"ETHUSDT"}}')


async def test_writer_loop_persists_queued_record(tmp_path: Path) -> None:
    recorder = service(tmp_path)
    raw = json.dumps(
        {
            "stream": "btcusdt@depth@100ms",
            "data": {"s": "BTCUSDT", "U": 1, "u": 1, "b": [], "a": []},
        }
    )
    await recorder._handle_frame(raw)
    await recorder.queue.put(None)
    await recorder._writer_loop()
    recorder.storage.close_partial()
    assert next(tmp_path.glob("btcusdt/*/depth-*.wal")).is_file()
    assert recorder.bytes_written > 0


async def test_closed_day_file_finalization_does_not_block_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = service(tmp_path)
    monkeypatch.setattr(recorder.storage, "detach_day_writers", lambda _: [object()])

    def slow_finalize(_: object) -> None:
        time.sleep(0.1)

    monkeypatch.setattr(recorder.storage, "finalize_detached_writers", slow_finalize)
    task = asyncio.create_task(recorder._finalize_closed_day_streams(date(2026, 9, 10)))
    await asyncio.sleep(0.01)
    assert not task.done()
    await task


async def test_fetch_snapshot_and_metadata(tmp_path: Path) -> None:
    recorder = service(tmp_path)
    snapshot = b'{"lastUpdateId":1,"bids":[["1","1"]],"asks":[["2","1"]]}'
    session = FakeSession([FakeResponse(200, snapshot), FakeResponse(200, b'{"symbols":[]}')])
    await recorder._fetch_and_store(session, "BTCUSDT", "snapshot")  # type: ignore[arg-type]
    await recorder._fetch_and_store(session, "BTCUSDT", "exchange-info")  # type: ignore[arg-type]
    assert next(tmp_path.glob("btcusdt/*/snapshot-*.json.zst")).is_file()
    assert next(tmp_path.glob("btcusdt/*/exchange-info-*.json.zst")).is_file()


async def test_fetch_failure_is_ledgered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = service(tmp_path)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("depth_recorder.recorder.asyncio.sleep", no_sleep)
    failures = [FakeResponse(500) for _ in range(6)]
    await recorder._fetch_and_store(FakeSession(failures), "BTCUSDT", "snapshot")  # type: ignore[arg-type]
    ledger = next(tmp_path.glob("btcusdt/*/ledger-*.ndjson"))
    assert json.loads(ledger.read_text().splitlines()[-1])["reason"] == "snapshot_failure"


def test_stable_timing_and_json_logging(tmp_path: Path) -> None:
    first, second = service(tmp_path / "a"), service(tmp_path / "b")
    first.run_id = second.run_id = "same-run"
    assert first._stable_reconnect_age() == second._stable_reconnect_age()
    assert first._backoff(0) >= 1
    assert first._backoff(20) <= 60
    delay = first._stable_jitter("BTCUSDT", "snapshot", 1)
    assert 810 <= delay <= 990
    record = __import__("logging").LogRecord("x", 20, "", 1, "hello", (), None)
    assert json.loads(JsonLogFormatter().format(record))["message"] == "hello"


class FakeSocket:
    def __init__(self, recorder: RecorderService, messages: list[Any]):
        self.recorder = recorder
        self.messages = messages
        self.closed = False

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def receive(self, timeout: float) -> Any:
        item = self.messages.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True
        self.recorder.request_stop()


class FakeWebSocketSession:
    def __init__(self, socket: FakeSocket):
        self.socket = socket
        self.arguments: dict[str, Any] = {}

    def ws_connect(self, url: str, **kwargs: Any) -> FakeSocket:
        self.arguments = {"url": url, **kwargs}
        return self.socket


async def test_websocket_supervisor_uses_server_driven_autoping(tmp_path: Path) -> None:
    from aiohttp import WSMessage, WSMsgType

    recorder = service(tmp_path)
    shutdown = WSMessage(
        WSMsgType.TEXT,
        '{"stream":"!serverShutdown","data":{"e":"serverShutdown"}}',
        None,
    )
    socket = FakeSocket(recorder, [shutdown])
    session = FakeWebSocketSession(socket)
    await recorder._websocket_supervisor(session)  # type: ignore[arg-type]
    assert session.arguments["autoping"] is True
    assert session.arguments["heartbeat"] is None
    assert socket.closed


async def test_websocket_supervisor_stall_and_proactive_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stalled = service(tmp_path / "stall")
    socket = FakeSocket(stalled, [TimeoutError()])
    await stalled._websocket_supervisor(FakeWebSocketSession(socket))  # type: ignore[arg-type]
    events = next((tmp_path / "stall").glob("btcusdt/*/ledger-*.ndjson")).read_text()
    assert '"reason":"stall"' in events

    proactive = service(tmp_path / "proactive")
    monkeypatch.setattr(proactive, "_stable_reconnect_age", lambda: 0.0)
    socket = FakeSocket(proactive, [TimeoutError()])
    await proactive._websocket_supervisor(FakeWebSocketSession(socket))  # type: ignore[arg-type]
    events = next((tmp_path / "proactive").glob("btcusdt/*/ledger-*.ndjson")).read_text()
    assert '"reason":"proactive_reconnect"' in events


async def test_heartbeat_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEPTH_RECORDER_HEARTBEAT_URL", "https://hc-ping.test/id")
    recorder = service(tmp_path)
    recorder.last_message_mono = __import__("time").monotonic()
    session = FakeSession([FakeResponse(200)])
    sleeps = 0

    async def one_iteration(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise __import__("asyncio").CancelledError

    monkeypatch.setattr("depth_recorder.recorder.asyncio.sleep", one_iteration)
    with pytest.raises(__import__("asyncio").CancelledError):
        await recorder._heartbeat_loop(session)  # type: ignore[arg-type]
    assert session.calls[0][0] == "https://hc-ping.test/id"

    unhealthy = service(tmp_path / "unhealthy")
    session = FakeSession([FakeResponse(200)])
    sleeps = 0
    with pytest.raises(__import__("asyncio").CancelledError):
        await unhealthy._heartbeat_loop(session)  # type: ignore[arg-type]
    assert session.calls[0][0].endswith("/fail")


async def test_service_run_starts_and_shuts_down_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = service(tmp_path)

    async def no_preflight(_: object) -> None:
        return None

    async def stop_now(_: object) -> None:
        recorder.request_stop()

    async def wait_forever(*_: object) -> None:
        await __import__("asyncio").Event().wait()

    monkeypatch.setattr(recorder, "_preflight", no_preflight)
    monkeypatch.setattr(recorder, "_websocket_supervisor", stop_now)
    monkeypatch.setattr(recorder, "_heartbeat_loop", wait_forever)
    monkeypatch.setattr(recorder, "_day_close_loop", wait_forever)
    monkeypatch.setattr(recorder, "_metadata_loop", wait_forever)
    monkeypatch.setattr(recorder, "_snapshot_worker", wait_forever)
    monkeypatch.setattr(recorder, "_snapshot_schedule", wait_forever)
    await recorder.run()
    ledger = next(tmp_path.glob("btcusdt/*/ledger-*.ndjson")).read_text()
    assert '"reason":"process_start"' in ledger
    assert '"reason":"process_stop"' in ledger
