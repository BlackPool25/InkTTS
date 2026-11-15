# Server E2E evidence (2026-11-15, live Kokoro container)

Run: `CACHE_DIR=/tmp/inkcast-e2e PYTHONPATH=tts-stack python3 proof/e2e_live.py`
(Kokoro `remsky/kokoro-fastapi-cpu:v0.10.0` live at 127.0.0.1:8880.)

- `GET /health` → `{"status":"ready","engines":{"kokoro":"up","chatterbox":"idle"}}`
- `POST /manifest` (3 sentences) → `{"doc_id":"e2e1","accepted":3}`
- `POST /tap {idx:1}` → `{"queued":true,"position":4}`
- `GET /events?doc_id=e2e1` → 2 `ready` events within 60s (3rd sentence still
  rendering on CPU — prefetch window, not a stall; tap target idx 1 was ready)
- `GET /audio/{hash}` → **200, 65088 bytes, magic `OggS`** (valid Opus)

Bugs this run caught and fixed:
- SQLite connection shared across worker/API threads → `check_same_thread=False` + lock (`cache.py` rewrite).
- `GET /audio` read `CACHE` off the wrong module → module-level `CACHE = None` in `api.py`, wired by `server.start()`.
