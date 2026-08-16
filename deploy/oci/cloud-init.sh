#!/usr/bin/env bash
set -euo pipefail
SHA="${ATM_SOURCE_SHA:?}"
REPO="https://github.com/simonkey888/ATM-Agent-Teller-Machine.git"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git curl jq ca-certificates python3 python3-venv python3-pip build-essential ripgrep ffmpeg
id atm >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/atm --shell /bin/bash atm
mkdir -p /var/lib/atm/state /var/lib/atm/.hermes /etc/atm /opt/atm
chown -R atm:atm /var/lib/atm; chmod 700 /var/lib/atm/state
rm -rf /opt/atm/* /opt/atm/.git
git init /opt/atm
git -C /opt/atm remote add origin "$REPO"
git -C /opt/atm fetch --depth 1 origin "$SHA"
git -C /opt/atm checkout -B agent/atm-v1 "$SHA"
python3 -m venv /opt/atm/.venv
/opt/atm/.venv/bin/python -m pip install --upgrade pip
/opt/atm/.venv/bin/python -m pip install -e /opt/atm
rm -rf /opt/atm/.atm; ln -s /var/lib/atm/state /opt/atm/.atm
cp /opt/atm/config/atm.example.json /var/lib/atm/atm.json
rm -f /opt/atm/config/atm.json; ln -s /var/lib/atm/atm.json /opt/atm/config/atm.json
chown atm:atm /var/lib/atm/atm.json; chmod 600 /var/lib/atm/atm.json
runuser -u atm -- env HOME=/var/lib/atm HERMES_HOME=/var/lib/atm/.hermes bash -lc 'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-browser --skip-setup'
install -m 0644 /opt/atm/deploy/oci/atm-controller.service /etc/systemd/system/atm-controller.service
install -m 0644 /opt/atm/deploy/oci/atm-supervisor.service /etc/systemd/system/atm-supervisor.service
install -m 0644 /opt/atm/deploy/oci/atm-state-backup.service /etc/systemd/system/atm-state-backup.service
install -m 0644 /opt/atm/deploy/oci/atm-state-backup.timer /etc/systemd/system/atm-state-backup.timer
systemctl daemon-reload
systemctl disable --now atm-supervisor.service 2>/dev/null || true
systemctl disable --now atm-controller.service 2>/dev/null || true
touch /var/lib/atm/cloud-init-ready; chown atm:atm /var/lib/atm/cloud-init-ready
