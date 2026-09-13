from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class WriteRecord:
    symbol: str
    stream: str
    rx_utc: int
    rx_mono: int
    envelope: bytes
    first_update_id: int | None = None
    final_update_id: int | None = None
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class DepthEvent:
    symbol: str
    first_update_id: int
    final_update_id: int
    bids: tuple[tuple[str, str], ...]
    asks: tuple[tuple[str, str], ...]
    recorder_id: str
    run_id: str
    rx_utc: int
    rx_mono: int
    raw_data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TickerEvent:
    symbol: str
    update_id: int
    bid_price: str
    bid_quantity: str
    ask_price: str
    ask_quantity: str
    recorder_id: str
    run_id: str
    rx_utc: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    symbol: str
    last_update_id: int
    bids: tuple[tuple[str, str], ...]
    asks: tuple[tuple[str, str], ...]
    recorder_id: str
    run_id: str
    rx_utc: int
