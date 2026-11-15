#!/usr/bin/env bash
# Manual update for a LAN appliance: pull, rebuild, show the LAN URL.
# No Watchtower (no surprise restarts). Run from tts-stack/.
set -euo pipefail
cd "$(dirname "$0")"

GPU_FLAGS=()
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver.version --format=csv | tail -n +2
elif [ -e /dev/kfd ]; then
  echo "AMD ROCm device present (/dev/kfd)."
  command -v rocm-smi >/dev/null 2>&1 && rocm-smi --showproductname 2>/dev/null | head -3 || true
  GPU_FLAGS=(-f docker-compose.yml -f docker-compose.rocm.yml)
  echo "Using ROCm overlay for chatterbox."
else
  echo "WARN: no GPU detected — chatterbox (GPU) will fail health; kokoro CPU still works."
fi

docker compose "${GPU_FLAGS[@]}" pull kokoro || true
docker compose "${GPU_FLAGS[@]}" up -d --build

LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
echo "Extension server URL: http://${LAN_IP:-<this-host>}:${ORCH_PORT:-8095}"
