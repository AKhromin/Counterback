# Depth recorder and offline verifier

**A backtester where the market can answer back.** Counterback is a KCL project to compare conventional backtests with execution-realistic, counterfactual simulations in which a strategy's orders can affect subsequent market state.

**Project status (13 September 2026): data-capture and verification foundation.** This checkout contains a working Binance Spot depth recorder, an offline two-recorder verifier, tests, CI, and deployment/offload tooling (`depth-recorder` package version `0.1.1`). It does **not** yet contain a strategy runner, matching simulator, counterfactual market-response model, or backtest results. The broader backtesting engine is planned work, not a shipped feature. The target for v1 is 30 November 2026.

The remaining work is to establish trustworthy full-day recordings from two independent hosts, complete the production acceptance checks, then build the ingest, simulation, and comparison layers. A local smoke test, an active recorder, or a successful backup connectivity test alone is not production acceptance.

## What is implemented

The current package is a raw-first Binance Spot market-depth recorder and offline verifier. The live service does only the work that cannot be repeated later: it timestamps and stores exchange messages, takes periodic snapshots, and records observable gaps. Order-book reconstruction and quality decisions happen offline in `depth-recorder verify`.

The deployment scripts support systemd recording, daily offload through rclone, download-based remote verification, and age-gated pruning of verified files. Google Drive is the configured backup target in the recent recorder deployments; the scripts accept any configured rclone remote. At the last documented operational checks (8 September), recorder A had been recording and recorder B's service, timers, and Google Drive connection had been checked. Current host health, a first verified real-data upload from B, seven qualifying full days, deliberate-kill recovery, and a clean restore drill have **not** been verified from this checkout. See [the runbook](runbook.md) for the acceptance criteria.

## Captured data

For each configured symbol (BTCUSDT by default), one public combined WebSocket connection records:

- `depth@100ms` diff-depth events;
- every `trade` event;
- real-time `bookTicker` changes;
- a 5,000-level REST depth snapshot at connection startup, every 15 minutes, and after a sequence gap;
- `exchangeInfo` at startup and daily.

Every record contains the untouched exchange JSON plus nanosecond UTC and monotonic receive timestamps. A UUID `run_id` identifies one process lifetime. Monotonic timestamps are meaningful only inside the same `(recorder_id, run_id)` pair.

No Binance API key is accepted or required.

## Local installation

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp config.example.yaml config.yaml
```

Use Python 3.11 or newer. Set `output_dir` in `config.yaml` to an absolute writable directory (for example, this checkout's `data/`), then validate it without making a network request. The local `config.yaml` and captured data are ignored by Git.

```bash
.venv/bin/depth-recorder check-config --config config.yaml
```

Start recording from a non-US network:

```bash
.venv/bin/depth-recorder record --config config.yaml
```

The default market-data-only endpoints are public and unauthenticated. A startup response of HTTP 451 means the host is in a restricted region; do not work around it with authenticated endpoints.

## Output contract

```text
data/
  btcusdt/
    2026-09-01/
      depth-13.ndjson.zst
      trade-13.ndjson.zst
      bookticker-13.ndjson.zst
      snapshot-130002-123456.json.zst
      exchange-info-000500-123456.json.zst
      ledger-2026-09-01.ndjson
      manifest-2026-09-01.json
```

Active hourly streams also have `.wal` and `.zst.partial` files. They are recovery state and must not be uploaded, analyzed, or manually deleted. On an orderly hour boundary they become a validated immutable `.ndjson.zst` file. On restart the WAL repairs the active compressed stream, and an incomplete last record is placed under `quarantine/`.

The recorder writes a partial manifest on graceful shutdown. At UTC day close it
finalizes the closed stream files outside the event loop, while capture continues.
The low-priority offload service builds the final manifest before uploading so the
full-day checksum scan cannot stall live WebSocket capture. Only a final manifest is
eligible for offload and verification.

To build any missing manifests for completed days without starting the recorder:

```bash
depth-recorder finalize-closed-days --config /etc/depth-recorder/config.yaml
```

## Offline verification

Restore or otherwise expose both recorder roots on one non-recording machine, then run:

```bash
depth-recorder verify \
  --date 2026-09-01 \
  --input recorder-a=/data/recorder-a \
  --input recorder-b=/data/recorder-b \
  --output /data/verification
```

The verifier:

1. validates final manifests and SHA-256 checksums;
2. merges depth intervals by update ID and detects conflicting duplicates;
3. reports per-recorder, joint update-ID, wall-clock, and trustworthy reconstruction coverage separately;
4. rebuilds snapshot-anchored, gap-free book segments;
5. treats levels beyond the snapshot’s deepest returned prices as unknown, never as zero;
6. accepts removal of an unseen level as an idempotent no-op;
7. stops trusting a side’s top when its known snapshot horizon is exhausted;
8. aligns `bookTicker` checks by update ID at observable depth-batch boundaries.

Verification reports are immutable. Re-running the same day into the same output directory fails instead of overwriting evidence.

## Tests

```bash
.venv/bin/python -m pytest -m 'not live' --cov=depth_recorder --cov-report=term-missing --cov-fail-under=80
.venv/bin/ruff check .
.venv/bin/mypy src/depth_recorder
```

Hosted CI never connects to Binance. All exchange-facing tests must carry the `live` marker and run only on a recorder VM or explicitly non-US self-hosted runner. Protocol behavior, including payload-preserving automatic PONG responses and absence of unsolicited client PING/PONG frames, is tested against a local WebSocket server in ordinary CI.

## Deployment

The primary deployment is a native systemd service on Ubuntu or Debian. Docker Compose provides an equivalent alternative.

On a fresh non-US VM:

```bash
sudo ./deploy/install.sh /absolute/path/to/this/checkout
sudoedit /etc/depth-recorder/config.yaml
sudoedit /etc/depth-recorder/environment
sudo systemctl start depth-recorder
sudo systemctl status depth-recorder
```

The environment file should resemble:

```bash
DEPTH_RECORDER_HEARTBEAT_URL=https://hc-ping.com/REDACTED
OFFLOAD_HEARTBEAT_URL=https://hc-ping.com/REDACTED
RCLONE_REMOTE=gdrive:DepthRecorderBackup
RECORDER_ID=recorder-a
DATA_ROOT=/var/lib/depth-recorder/data
```

Keep it mode `0640`, owned by `root:depth-recorder`. Healthchecks ping URLs are secrets even though exchange data is public.

Set `RCLONE_REMOTE` to the actual private remote/root for each host (for example, recorder B used `gdrive:DepthRecorderBackup-B`). Completed UTC days are uploaded under `raw/<recorder-id>/<symbol>/<UTC-date>/` after a final manifest exists. A local `manifest-*.json.uploaded` marker is created only after `rclone check --download` succeeds. Never commit the environment file, rclone credentials, or raw market data.

See [the production runbook](runbook.md) for backup configuration, alert response, restore, and acceptance procedures.

## Scope boundaries

The recorder deliberately excludes live trading, authenticated/user streams, order placement, and a live order book inside the capture process. Cleaning, analytics, dashboards, and multi-venue support are not implemented here. Backtesting ingest and simulation are later project components.

## License

Apache-2.0. Raw captured data is not licensed for redistribution by this repository and must remain private until the exchange terms review is complete.
