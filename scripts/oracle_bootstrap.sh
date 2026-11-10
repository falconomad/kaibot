#!/usr/bin/env bash
# Bootstrap an Oracle Cloud Always Free Ubuntu 24.04 VM (Ampere A1, arm64) for Kaibot.
# Run once as the default `ubuntu` user:  bash oracle_bootstrap.sh
# Safe to re-run. Paper trading only: never put live broker keys on this box.
set -euo pipefail

sudo timedatectl set-timezone UTC
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git ufw unattended-upgrades fail2ban

# Docker Engine + compose plugin (official repo)
if ! command -v docker >/dev/null; then
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
fi

# Firewall: SSH + HTTPS only (Oracle's VCN security list must also allow 443)
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 443/tcp
sudo ufw --force enable

# App directory; the repo is cloned with a read-only deploy key (see docs/ops/oracle_vm.md)
sudo mkdir -p /opt/kaibot && sudo chown "$USER":"$USER" /opt/kaibot
echo "Bootstrap done. Log out and back in (docker group), then follow docs/ops/oracle_vm.md step 5."
