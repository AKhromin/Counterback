#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

SOURCE_DIR="${1:-}"
if [[ -z "${SOURCE_DIR}" || ! -f "${SOURCE_DIR}/pyproject.toml" ]]; then
  echo "Usage: sudo ./deploy/install.sh /absolute/path/to/checkout" >&2
  exit 2
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv rclone chrony ufw unattended-upgrades

getent group depth-recorder >/dev/null || groupadd --system depth-recorder
id depth-recorder >/dev/null 2>&1 || useradd --system --gid depth-recorder --home-dir /var/lib/depth-recorder --shell /usr/sbin/nologin depth-recorder
install -d -o depth-recorder -g depth-recorder -m 0750 /var/lib/depth-recorder/data
install -d -o root -g depth-recorder -m 0750 /etc/depth-recorder
install -d -o root -g root -m 0755 /opt/depth-recorder

python3 -m venv /opt/depth-recorder/venv
/opt/depth-recorder/venv/bin/pip install --upgrade pip
/opt/depth-recorder/venv/bin/pip install "${SOURCE_DIR}"

install -o root -g root -m 0644 "${SOURCE_DIR}/deploy/systemd/depth-recorder.service" /etc/systemd/system/depth-recorder.service
install -o root -g root -m 0644 "${SOURCE_DIR}/deploy/systemd/depth-recorder-offload.service" /etc/systemd/system/depth-recorder-offload.service
install -o root -g root -m 0644 "${SOURCE_DIR}/deploy/systemd/depth-recorder-offload.timer" /etc/systemd/system/depth-recorder-offload.timer
install -o root -g root -m 0644 "${SOURCE_DIR}/deploy/systemd/depth-recorder-prune.service" /etc/systemd/system/depth-recorder-prune.service
install -o root -g root -m 0644 "${SOURCE_DIR}/deploy/systemd/depth-recorder-prune.timer" /etc/systemd/system/depth-recorder-prune.timer
install -o root -g root -m 0755 "${SOURCE_DIR}/deploy/offload.sh" /opt/depth-recorder/offload.sh
install -o root -g root -m 0755 "${SOURCE_DIR}/deploy/prune_verified.py" /opt/depth-recorder/prune_verified.py

if [[ ! -f /etc/depth-recorder/config.yaml ]]; then
  install -o root -g depth-recorder -m 0640 "${SOURCE_DIR}/config.example.yaml" /etc/depth-recorder/config.yaml
fi
if [[ ! -f /etc/depth-recorder/environment ]]; then
  install -o root -g depth-recorder -m 0640 /dev/null /etc/depth-recorder/environment
fi

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable
systemctl enable --now chrony
systemctl daemon-reload
systemctl enable depth-recorder.service depth-recorder-offload.timer depth-recorder-prune.timer

echo "Edit /etc/depth-recorder/config.yaml and /etc/depth-recorder/environment, configure /root/.config/rclone/rclone.conf, then start depth-recorder.service."
