from __future__ import annotations

import asyncio
import os

import aiohttp
import pytest

from depth_recorder.config import RecorderConfig, validate_config
from depth_recorder.recorder import RecorderService

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("RUN_BINANCE_LIVE_TESTS") != "1",
    reason="set RUN_BINANCE_LIVE_TESTS=1 only on an approved non-US host",
)
async def test_public_binance_market_data_contract() -> None:
    rest = "https://data-api.binance.vision"
    websocket = (
        "wss://data-stream.binance.vision:443/stream?streams="
        "btcusdt@depth@100ms/btcusdt@trade/btcusdt@bookTicker"
    )
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{rest}/api/v3/ping") as response:
            assert response.status != 451, "live tests must run outside a restricted region"
            assert response.status == 200
        async with session.get(
            f"{rest}/api/v3/depth", params={"symbol": "BTCUSDT", "limit": 5000}
        ) as response:
            assert response.status == 200
            snapshot = await response.json()
            assert isinstance(snapshot["lastUpdateId"], int)
            assert 0 < len(snapshot["bids"]) <= 5000
            assert 0 < len(snapshot["asks"]) <= 5000
        observed: set[str] = set()
        async with session.ws_connect(websocket, autoping=True, heartbeat=None) as socket:
            async with asyncio.timeout(30):
                while len(observed) < 3:
                    message = await socket.receive()
                    assert message.type == aiohttp.WSMsgType.TEXT
                    stream = str(__import__("json").loads(message.data)["stream"]).lower()
                    if "@depth" in stream:
                        observed.add("depth")
                    elif stream.endswith("@trade"):
                        observed.add("trade")
                    elif stream.endswith("@bookticker"):
                        observed.add("bookTicker")
        assert observed == {"depth", "trade", "bookTicker"}


@pytest.mark.skipif(
    os.environ.get("RUN_BINANCE_LIVE_TESTS") != "1",
    reason="set RUN_BINANCE_LIVE_TESTS=1 only on an approved non-US host",
)
async def test_recorder_live_smoke_writes_all_required_streams(tmp_path) -> None:
    config = validate_config(
        RecorderConfig(
            recorder_id="live-smoke",
            output_dir=str(tmp_path),
            snapshot_interval_seconds=60,
            heartbeat_interval_seconds=60,
        )
    )
    recorder = RecorderService(config)
    task = asyncio.create_task(recorder.run())
    try:
        deadline = asyncio.get_running_loop().time() + 30
        while not list(tmp_path.glob("btcusdt/*/snapshot-*.json.zst")):
            if task.done():
                await task
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(1)
    finally:
        recorder.request_stop()
        await task
    assert next(tmp_path.glob("btcusdt/*/depth-*.wal")).is_file()
    assert next(tmp_path.glob("btcusdt/*/trade-*.wal")).is_file()
    assert next(tmp_path.glob("btcusdt/*/bookticker-*.wal")).is_file()
    assert next(tmp_path.glob("btcusdt/*/snapshot-*.json.zst")).is_file()
    assert next(tmp_path.glob("btcusdt/*/manifest-*.partial.json")).is_file()
