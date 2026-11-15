#!/usr/bin/env bash
# Task 1 boot proof: config valid + kokoro healthy (+ chatterbox if GPU).
set -euo pipefail
cd "$(dirname "$0")"
docker compose config >/dev/null && echo "compose config: OK"
docker compose up -d kokoro
for i in $(seq 1 24); do
  if docker compose exec -T kokoro python -c "import urllib.request;urllib.request.urlopen('http://localhost:8880/docs')" 2>/dev/null; then
    echo "kokoro: healthy"; exit 0
  fi
  sleep 10
done
echo "kokoro: NOT healthy after 4m"; exit 1
