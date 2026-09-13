# Decision log

## 2026-09-06 — recorder protocol and operations baseline

- Use Binance public market-data-only REST and WebSocket endpoints, with standard public endpoints configurable as fallbacks.
- Use `aiohttp` server-driven autoping and no client keepalive loop.
- Keep GitHub-hosted CI offline from Binance; live tests run only in approved non-US environments.
- Record a UUID `run_id` per process lifetime and prohibit monotonic-clock comparisons across run IDs.
- Preserve unknown depth beyond the 5,000-level snapshot horizon; unseen removals are not corruption.
- Align `bookTicker` validation by update ID at 100 ms depth-batch boundaries.
- Use WAL-backed streaming zstd so active data can be repaired without rewriting finalized files.
- Use Cloudflare R2 Standard as private canonical storage and retain raw data for the project year.
- Initial recorder locations are Hetzner Helsinki and AWS Tokyo, using provider-portable deployment artifacts.
- Publication of raw recordings remains prohibited until a separate market-data redistribution review is completed.

## 2026-09-08 — backup deployment update

- The Cloudflare R2 choice above was the original design. The recent deployments use private Google Drive roots through rclone instead. The offload script accepts the configured rclone remote and stores completed data below `raw/<recorder-id>/`.
- A successful backup connection test is not a completed data offload. Require a final manifest, a successful download-based remote check, a local `.uploaded` marker, and a matching remote listing before treating an offload as verified.
