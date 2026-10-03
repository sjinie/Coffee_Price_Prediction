#!/usr/bin/env bash
# Ubuntu 24.04; run as root with the Actions PUBLIC key file as the first argument.
set -euo pipefail
test "$(id -u)" = 0
test -f "${1:?Actions public key file required}"
ssh-keygen -lf "$1" >/dev/null

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl rsync
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# Docker's official Ubuntu repository; no downloaded installation script.
. /etc/os-release
test "$ID" = ubuntu
test "$VERSION_ID" = 24.04
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# Keep brief image builds from exhausting the 1 GiB host; measure real usage after deployment.
if ! test -e /swapfile-coffee; then
  fallocate -l 2G /swapfile-coffee
  chmod 600 /swapfile-coffee
  mkswap /swapfile-coffee
fi
if ! swapon --show=NAME --noheadings | grep -Fxq /swapfile-coffee; then
  swapon /swapfile-coffee
fi
if ! grep -Fq '/swapfile-coffee none swap' /etc/fstab; then
  printf '%s\n' '/swapfile-coffee none swap sw 0 0' >> /etc/fstab
fi

id coffee-actions >/dev/null 2>&1 || useradd --create-home --shell /bin/bash coffee-actions
install -d -m 0700 -o coffee-actions -g coffee-actions /home/coffee-actions/.ssh
{ printf '%s ' 'restrict,port-forwarding,permitopen="127.0.0.1:15432"'; cat "$1"; } > /home/coffee-actions/.ssh/authorized_keys
chown coffee-actions:coffee-actions /home/coffee-actions/.ssh/authorized_keys
chmod 600 /home/coffee-actions/.ssh/authorized_keys
install -d -m 0755 /srv/coffee
install -d -m 0700 -o coffee-actions -g coffee-actions /srv/coffee/v2 /srv/coffee/v2/sources
install -d -m 0700 /srv/coffee/backups
cat > /etc/ssh/sshd_config.d/00-coffee.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
Match User coffee-actions
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:15432
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY no
Match all
EOF
sshd -t
systemctl reload ssh
docker --version
docker compose version
