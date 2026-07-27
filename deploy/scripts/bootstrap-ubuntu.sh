#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script with sudo: sudo ./deploy/scripts/bootstrap-ubuntu.sh" >&2
  exit 2
fi

LOGIN_USER=${SUDO_USER:-}
if [ -z "$LOGIN_USER" ] || [ "$LOGIN_USER" = "root" ]; then
  echo "Run through sudo from your normal SSH user, not directly as root." >&2
  exit 2
fi

apt-get update
apt-get install -y ca-certificates curl git openssl

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
ARCHITECTURE=$(dpkg --print-architecture)
printf '%s\n' \
  "deb [arch=$ARCHITECTURE signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker "$LOGIN_USER"

if ! swapon --show=NAME --noheadings | grep -q .; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
fi

echo
echo "Docker and 4 GB swap are ready."
echo "Log out and reconnect so user '$LOGIN_USER' receives Docker group access."
