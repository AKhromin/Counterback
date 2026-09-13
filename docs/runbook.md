# Production runbook

## Deployment topology

The acceptance target is two machines in different providers and non-US regions. The original deployment plan was:

| Recorder | Initial target | ID | Upgrade order |
|---|---|---|---|
| A | Hetzner, Helsinki | `recorder-a` | First |
| B | AWS, Tokyo | `recorder-b` | At least 24 hours after A |

Do not deploy either recorder or any live test runner in a US region. The plan above is a target, not proof of the hosts' current locations or acceptance status. The recent recorder deployments use separate private Google Drive roots through rclone; verify each host's actual remote configuration before relying on it. Raw data is stored under `raw/<recorder-id>/<symbol>/<UTC-date>/` within that root.

## One-time host setup

1. Create a non-root SSH administrator with key-only authentication.
2. Clone the same tagged repository revision on each host.
3. Run `sudo ./deploy/install.sh /absolute/path/to/checkout`.
4. Give each host its unique `recorder_id` in `/etc/depth-recorder/config.yaml`.
5. Put Healthchecks URLs and offload settings in `/etc/depth-recorder/environment`.
6. Confirm `chronyc tracking` reports synchronization.
7. Run the configuration check as the service user.
8. Start the service and inspect JSON logs with `journalctl -u depth-recorder -f`.
9. Confirm depth, trade, ticker, snapshot, ledger, WAL, and compressed-partial files appear.
10. Confirm Healthchecks receives a heartbeat containing the correct recorder and run IDs.

The installer enables timers but does not start the recorder until configuration is reviewed.

## Private backup and rclone

1. Prepare a private backup root for each recorder and keep its credentials off GitHub.
2. Configure separate credentials for each host with only the access it needs.
3. Configure rclone as root because the offload unit runs as root:

   ```bash
   sudo rclone config
   ```

4. Configure the host's chosen rclone backend and set `RCLONE_REMOTE` to its private root. The recent deployments use `gdrive:DepthRecorderBackup` for A and `gdrive:DepthRecorderBackup-B` for B.
5. Keep `/etc/depth-recorder/environment` owned by `root:depth-recorder`, mode `0640`, with the host's `RECORDER_ID`, `DATA_ROOT`, `RCLONE_REMOTE`, and heartbeat URLs.
6. Run a harmless connectivity check:

   ```bash
   sudo rclone lsf gdrive:DepthRecorderBackup
   ```

   Replace that example with this host's configured remote and root.

7. Trigger the offload unit manually after the first final daily manifest exists:

   ```bash
   sudo systemctl start depth-recorder-offload.service
   sudo journalctl -u depth-recorder-offload.service --since today
   ```

The offload job first builds any missing manifests for completed UTC days in its
low-priority process. It then uploads only final `.zst`, ledger, and manifest files,
performs `rclone check --download`, and creates a local `.uploaded` marker. Check the remote `raw/<recorder-id>/` listing, offload journal, and local `manifest-*.json.uploaded` marker to establish a real-data upload; a connectivity test or enabled timer is insufficient. The prune
job only considers manifest-listed data files with that marker and an age greater than
48 hours. Full-day manifest scanning must never run in the live recorder event loop.

## Normal daily check

This should take less than five minutes:

1. Confirm both recorder Healthchecks are green and fresh.
2. Confirm both offload Healthchecks completed after midnight UTC.
3. Read each final manifest’s gap count, missing-ID span, message counts, and disk-free percentage.
4. Confirm recorder IDs, current run IDs, and deployed versions are expected.
5. Investigate any new quarantine file, sequence gap, repeated reconnect, snapshot failure, or writer-backpressure event.

## Alerts

### Missing recorder heartbeat

1. Check provider status and host reachability.
2. Inspect `systemctl status depth-recorder` and recent journal entries.
3. Check disk and inode usage.
4. Check time synchronization.
5. Restart only the failed recorder. Do not restart both together.
6. Confirm the new process emits a new `run_id`, takes a snapshot, and resumes depth IDs.
7. Let the verifier decide whether the other recorder filled the interval.

### Data stale or repeated 30-second stalls

1. Confirm DNS and outbound TLS connectivity to the market-data-only endpoints.
2. Check for HTTP 451, 429, `serverShutdown`, or provider packet loss.
3. Do not add client-generated WebSocket keepalives. Binance server PING frames are answered automatically.
4. If one provider is persistently impaired, replace only that recorder in another non-US region and record the decision.

### Disk at or above 80%

1. Check whether nightly offload and remote verification are succeeding.
2. Run the offload service manually if the configured backup remote is healthy.
3. Run pruning only after successful remote verification.
4. Never remove a WAL, compressed partial, unmanifested file, or file without an upload marker.
5. Expand the disk if verified pruning cannot restore a safe margin.

### Gap or failed reconstruction

1. Preserve both raw copies unchanged.
2. Check whether both recorders missed the same update-ID interval.
3. Find the first later snapshot that can anchor a new segment.
4. Treat unseen removals beyond the 5,000-level horizon as normal no-ops.
5. Distinguish ticker mismatches, horizon exhaustion, sequence gaps, and structural book errors in the report.
6. If daily joint coverage is below 98%, record an explicit exclude-or-retain-with-caveat decision.

## Upgrade procedure

1. Verify yesterday’s offload and current health of both recorders.
2. Upgrade recorder A and deliberately leave recorder B unchanged.
3. Confirm A starts with a new `run_id`, obtains snapshots, and remains healthy for 24 hours.
4. Upgrade recorder B only after A’s observation period passes.
5. Never deploy a schema change without incrementing `schema_version` or providing verifier compatibility.

## Weekly integrity job

1. Restore or mount the previous seven final days from both recorder prefixes.
2. Run `depth-recorder verify` for each UTC day.
3. Review joint, per-recorder, wall-clock, and trustworthy reconstruction coverage independently.
4. Check `bookTicker` mismatches by update ID.
5. Optionally compare recorded trade IDs with Binance’s next-day public trade archive.
6. Store immutable reports under a separate private verification prefix.

## Monthly restore drill

1. Select a random completed day that has not previously been used for a drill.
2. Restore both recorder prefixes to a clean directory on a third machine.
3. Do not use either recorder VM’s retained files.
4. Validate all manifest checksums.
5. Run the verifier and compare its result with the stored weekly report.
6. Record retrieval duration, bytes, checksum result, coverage result, and any discrepancy.
7. The drill passes only if reconstruction succeeds using restored objects alone.

## Seven-day production acceptance

All conditions must pass:

- Both recorders run in different providers and regions.
- Every day reaches at least 99.5% joint update-ID coverage.
- Hourly presence and five-minute UTC boundary checks provide complete wall-clock evidence.
- Reconstruction reports all invalid or horizon-indeterminate segments explicitly.
- Every nightly offload passes download-based checksum verification.
- A deliberate process kill triggers the dead-man alert and automatic recovery with a new `run_id`.
- One monthly-style restore drill succeeds.
- Measured steady-state memory remains below 100 MB and CPU remains near or below 5% of one ARM vCPU.

Only after this checklist passes should the project describe recording as production-accepted.
