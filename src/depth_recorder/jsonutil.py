from __future__ import annotations

import json
from typing import Any

from .models import SCHEMA_VERSION


def parse_json_object(raw: str | bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def make_envelope(
    raw: str | bytes,
    *,
    recorder_id: str,
    run_id: str,
    rx_utc: int,
    rx_mono: int,
) -> bytes:
    """Wrap a valid raw JSON object without reserializing the raw payload."""
    parse_json_object(raw)
    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
    prefix = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "recorder_id": recorder_id,
            "run_id": run_id,
            "rx_utc": rx_utc,
            "rx_mono": rx_mono,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return prefix[:-1] + b',"raw":' + raw_bytes + b"}\n"


def parse_envelope(line: bytes) -> dict[str, Any]:
    value = json.loads(line)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported or malformed record envelope")
    if not isinstance(value.get("raw"), dict):
        raise ValueError("record raw field must be an object")
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
