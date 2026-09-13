from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

REQUIRED_STREAMS = ("depth@100ms", "trade", "bookTicker")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,30}$")
ALLOWED_WS_HOSTS = {"data-stream.binance.vision", "stream.binance.com"}
ALLOWED_REST_HOSTS = {"data-api.binance.vision", "api.binance.com"}
FORBIDDEN_KEY_PARTS = ("api_key", "apikey", "secret_key", "listen_key", "signature")


@dataclass(frozen=True, slots=True)
class RecorderConfig:
    recorder_id: str
    symbols: tuple[str, ...] = ("BTCUSDT",)
    streams: tuple[str, ...] = REQUIRED_STREAMS
    websocket_base_url: str = "wss://data-stream.binance.vision:443"
    rest_base_url: str = "https://data-api.binance.vision"
    output_dir: str = "/var/lib/depth-recorder/data"
    snapshot_interval_seconds: int = 900
    snapshot_jitter_fraction: float = 0.10
    stall_timeout_seconds: int = 30
    freshness_failure_seconds: int = 300
    proactive_reconnect_seconds: int = 23 * 60 * 60
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0
    heartbeat_interval_seconds: int = 60
    heartbeat_url_env: str = "DEPTH_RECORDER_HEARTBEAT_URL"
    disk_failure_percent: float = 80.0
    compression_level: int = 6
    queue_size: int = 10_000
    log_level: str = "INFO"
    service_mode: str = "native"

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir).expanduser().resolve()

    @property
    def config_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def heartbeat_url(self) -> str | None:
        return os.environ.get(self.heartbeat_url_env)

    def combined_stream_url(self) -> str:
        names = [f"{symbol.lower()}@{stream}" for symbol in self.symbols for stream in self.streams]
        return f"{self.websocket_base_url.rstrip('/')}/stream?streams={'/'.join(names)}"


def _validate_endpoint(value: str, hosts: set[str], schemes: set[str], label: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in schemes or parsed.hostname not in hosts:
        raise ValueError(f"{label} must be an approved public Binance market-data endpoint")
    lowered = value.lower()
    if any(part in lowered for part in ("user", "listenkey", "ws-api")):
        raise ValueError(f"{label} must not reference an authenticated or user-data endpoint")


def validate_config(config: RecorderConfig, *, check_filesystem: bool = True) -> RecorderConfig:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", config.recorder_id):
        raise ValueError("recorder_id must be 1-64 safe path characters")
    symbols = tuple(symbol.upper() for symbol in config.symbols)
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("symbols must be non-empty and unique")
    if any(not SYMBOL_RE.fullmatch(symbol) for symbol in symbols):
        raise ValueError("symbols must contain only uppercase ASCII letters and digits")
    if tuple(config.streams) != REQUIRED_STREAMS:
        raise ValueError(f"streams must be exactly {REQUIRED_STREAMS!r}")
    _validate_endpoint(config.websocket_base_url, ALLOWED_WS_HOSTS, {"wss"}, "websocket_base_url")
    _validate_endpoint(config.rest_base_url, ALLOWED_REST_HOSTS, {"https"}, "rest_base_url")
    if config.snapshot_interval_seconds < 60:
        raise ValueError("snapshot_interval_seconds must be at least 60")
    if not 0 <= config.snapshot_jitter_fraction <= 0.25:
        raise ValueError("snapshot_jitter_fraction must be between 0 and 0.25")
    if config.stall_timeout_seconds < 10:
        raise ValueError("stall_timeout_seconds must be at least 10")
    if config.freshness_failure_seconds <= config.stall_timeout_seconds:
        raise ValueError("freshness_failure_seconds must exceed stall_timeout_seconds")
    if (
        config.proactive_reconnect_seconds >= 24 * 60 * 60
        or config.proactive_reconnect_seconds < 3600
    ):
        raise ValueError("proactive reconnect must be between one and 24 hours")
    if not 0 < config.reconnect_min_seconds <= config.reconnect_max_seconds:
        raise ValueError("invalid reconnect delay range")
    if not 1 <= config.compression_level <= 19:
        raise ValueError("compression_level must be between 1 and 19")
    if config.queue_size < 100:
        raise ValueError("queue_size must be at least 100")
    if not 1 <= config.disk_failure_percent <= 99:
        raise ValueError("disk_failure_percent must be between 1 and 99")
    configured_path = Path(config.output_dir).expanduser()
    if not configured_path.is_absolute():
        raise ValueError("output_dir must resolve to an absolute path")
    path = config.output_path
    if check_filesystem:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        try:
            probe.touch(exist_ok=False)
            probe.unlink()
        except OSError as exc:
            raise ValueError(f"output_dir is not writable: {path}") from exc
    if symbols != config.symbols:
        values = asdict(config)
        values["symbols"] = symbols
        config = RecorderConfig(**values)
    return config


def load_config(path: str | Path, *, check_filesystem: bool = True) -> RecorderConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a YAML mapping")
    string_keys = {str(key) for key in raw}
    if any(any(part in key.lower() for part in FORBIDDEN_KEY_PARTS) for key in string_keys):
        raise ValueError("exchange credentials are forbidden in recorder configuration")
    allowed = {field.name for field in fields(RecorderConfig)}
    unknown = string_keys - allowed
    if unknown:
        raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
    if "recorder_id" not in raw:
        raise ValueError("recorder_id is required")
    values: dict[str, Any] = dict(raw)
    if "symbols" in values:
        values["symbols"] = tuple(values["symbols"])
    if "streams" in values:
        values["streams"] = tuple(values["streams"])
    return validate_config(RecorderConfig(**values), check_filesystem=check_filesystem)
