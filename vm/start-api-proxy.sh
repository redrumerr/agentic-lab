#!/usr/bin/env bash
# Docker does not publish ports on internal-only networks. Keep both networks
# internal and expose the API to the VM evaluator via a loopback-only socket.
set -euo pipefail
cd /workspace
api_container=$(docker compose -f vm/agent-compose.yml ps -q tool-api)
api_ip=$(docker inspect --format '{{(index .NetworkSettings.Networks "agentic-lab_target-net").IPAddress}}' "$api_container")
if [[ ! $api_ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo 'Cannot determine the tool API address' >&2
  exit 1
fi
systemctl stop lab-tool-api.socket lab-tool-api.service 2>/dev/null || true
cat > /etc/systemd/system/lab-tool-api.socket <<'UNIT'
[Unit]
Description=Lab mock API loopback listener
[Socket]
ListenStream=127.0.0.1:8080
NoDelay=true
[Install]
WantedBy=sockets.target
UNIT
cat > /etc/systemd/system/lab-tool-api.service <<UNIT
[Unit]
Description=Lab mock API relay to internal Docker network
Requires=lab-tool-api.socket
After=docker.service
[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd ${api_ip}:8080
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UNIT
systemctl daemon-reload
systemctl enable --now lab-tool-api.socket
