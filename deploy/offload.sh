#!/usr/bin/env bash
set -euo pipefail

: "${DATA_ROOT:=/var/lib/depth-recorder/data}"
: "${RCLONE_REMOTE:?Set RCLONE_REMOTE, for example gdrive:DepthRecorderBackup}"
: "${RECORDER_ID:?Set RECORDER_ID}"
: "${DEPTH_RECORDER_CONFIG:=/etc/depth-recorder/config.yaml}"
: "${DEPTH_RECORDER_CLI:=/opt/depth-recorder/venv/bin/depth-recorder}"

notify() {
  local outcome="$1"
  local message="$2"
  if [[ -n "${OFFLOAD_HEARTBEAT_URL:-}" ]]; then
    local target="${OFFLOAD_HEARTBEAT_URL}"
    [[ "${outcome}" == "fail" ]] && target="${target%/}/fail"
    curl --fail --silent --show-error --max-time 15 --data-raw "${message}" "${target}" >/dev/null || true
  fi
}

trap 'notify fail "Depth data offload failed on ${RECORDER_ID}"' ERR

# Manifest construction scans every record from the closed UTC day. Keep that work out
# of the live recorder process and run it here under the service account instead.
runuser --user depth-recorder -- \
  "${DEPTH_RECORDER_CLI}" finalize-closed-days --config "${DEPTH_RECORDER_CONFIG}"

while IFS= read -r -d '' manifest; do
  date_dir="$(dirname "${manifest}")"
  symbol="$(basename "$(dirname "${date_dir}")")"
  day="$(basename "${date_dir}")"
  marker="${manifest}.uploaded"
  [[ -f "${marker}" ]] && continue
  destination="${RCLONE_REMOTE%/}/raw/${RECORDER_ID}/${symbol}/${day}"
  rclone copy "${date_dir}" "${destination}" \
    --exclude '*.partial.json' --include '*.zst' --include 'ledger-*.ndjson' --include 'manifest-*.json' --exclude '*'
  rclone check "${date_dir}" "${destination}" --download \
    --exclude '*.partial.json' --include '*.zst' --include 'ledger-*.ndjson' --include 'manifest-*.json' --exclude '*'
  touch "${marker}"
done < <(find "${DATA_ROOT}" -type f -name 'manifest-????-??-??.json' -print0)

notify success "Depth data offload completed on ${RECORDER_ID}"
