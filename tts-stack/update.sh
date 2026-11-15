#!/usr/bin/env bash
# Manual update for a LAN appliance: pull, rebuild, show the LAN URL.
# No Watchtower (no surprise restarts). Run from tts-stack/.
set -euo pipefail
cd "$(dirname "$0")"

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version --format=csv | tail -n +2
else
  echo "WARN: no nvidia-smi — chatterbox (GPU) will fail health; kokoro CPU still works."
fi

docker compose pull kokoro || true
docker compose up -d --build

LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
echo "Extension server URL: http://${LAN_IP:-<this-host>}:${ORCH_PORT:-8095}"
