from pathlib import Path

import pytest

from depth_recorder.config import RecorderConfig, load_config, validate_config


def test_defaults_and_combined_url(tmp_path: Path) -> None:
    config = validate_config(RecorderConfig(recorder_id="recorder-a", output_dir=str(tmp_path)))
    assert config.symbols == ("BTCUSDT",)
    assert config.combined_stream_url().endswith(
        "/stream?streams=btcusdt@depth@100ms/btcusdt@trade/btcusdt@bookTicker"
    )


def test_normalizes_symbols(tmp_path: Path) -> None:
    config = validate_config(
        RecorderConfig(recorder_id="a", symbols=("btcusdt",), output_dir=str(tmp_path))
    )
    assert config.symbols == ("BTCUSDT",)


@pytest.mark.parametrize(
    "change",
    [
        {"streams": ("depth@100ms",)},
        {"websocket_base_url": "wss://example.com"},
        {"rest_base_url": "https://api.binance.com/userData"},
        {"snapshot_interval_seconds": 1},
        {"freshness_failure_seconds": 20},
        {"queue_size": 1},
    ],
)
def test_rejects_invalid_values(tmp_path: Path, change: dict[str, object]) -> None:
    values = {"recorder_id": "a", "output_dir": str(tmp_path), **change}
    with pytest.raises(ValueError):
        validate_config(RecorderConfig(**values))


def test_rejects_relative_output_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        validate_config(
            RecorderConfig(recorder_id="a", output_dir="relative/data"),
            check_filesystem=False,
        )


def test_rejects_unknown_and_secret_keys(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("recorder_id: a\nunknown: true\n")
    with pytest.raises(ValueError, match="unknown"):
        load_config(unknown, check_filesystem=False)
    secret = tmp_path / "secret.yaml"
    secret.write_text("recorder_id: a\napi_key: forbidden\n")
    with pytest.raises(ValueError, match="credentials"):
        load_config(secret, check_filesystem=False)
