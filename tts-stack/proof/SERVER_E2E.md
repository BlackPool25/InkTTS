# Server E2E evidence (2026-11-15, live Kokoro container)

Run 1 (local uvicorn + live Kokoro): manifest accepted 3, tap queued,
2 ready in 60s, audio 200 + 65088 bytes Ogg.

Run 2 (SHIPPED compose stack, `kokoro`+`orchestrator` on :8095, no local
Python in the loop): manifest accepted 3, tap queued, 2 ready in 90s,
`GET /audio/{hash}` -> **200, 65384 bytes, magic `OggS`** (valid Opus).

Bugs this E2E caught and fixed:
- SQLite connection shared across worker/API threads -> `check_same_thread=False` + lock (`cache.py` rewrite).
- `GET /audio` read `CACHE` off the wrong module -> module-level `CACHE = None` in `api.py`, wired by `server.start()` (+ lifespan startup in the image).

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
