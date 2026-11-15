"""Render routing: per-sentence POST to Kokoro / Chatterbox, degrade loud.

- Kokoro (default): word timestamps passthrough, words=[{w,start,end}].
- Chatterbox: duration-only, words=None, synthetic=True.
- Chatterbox down/timeout (3 consecutive per doc -> breaker open): route
  the rest of the doc straight to Kokoro with degraded=True + ONE notice.
- Retry 2x backoff per sentence; poison on empty/>400-char; timeout per
  request (no 60s+ silence); warmup surfaces 'warming' (never hang).
"""
from __future__ import annotations

import time
import urllib.request

TIMEOUT_S = 30
MAX_RETRIES = 2
CIRCUIT_TRIPS = 3
POISON_CHARS = 400


class RenderError(Exception):
    def __init__(self, error_class: str, msg: str):
        super().__init__(msg)
        self.error_class = error_class


class Breaker:
    def __init__(self):
        self.failures: dict[str, int] = {}
        self.open_docs: set[str] = set()

    def tripped(self, doc: str) -> bool:
        return doc in self.open_docs

    def record(self, doc: str, ok: bool) -> None:
        if ok:
            self.failures.pop(doc, None)
            return
        n = self.failures.get(doc, 0) + 1
        self.failures[doc] = n
        if n >= CIRCUIT_TRIPS:
            self.open_docs.add(doc)


def _post(url: str, payload: bytes, timeout: float = TIMEOUT_S) -> bytes:
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    last: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 -- classified by caller
            last = e
            time.sleep(min(2 ** attempt, 4))
    raise RenderError("transient", f"POST {url} failed x{MAX_RETRIES+1}: {last}")


def _voice_for(model: str, voice: str) -> str | None:
    """Map UI voice names to engine voice params.
    Kokoro (crispasr): voice packs are files ``kokoro-voice-<name>.gguf``
    resolved from the server CWD (model dir). Chatterbox: server default
    voice (per-request reference-audio voices are a future upload flow)."""
    if model == "kokoro":
        v = (voice or "af_heart").strip() or "af_heart"
        if v.endswith(".gguf"):
            return v
        return f"kokoro-voice-{v}.gguf"
    return None


def render_sentence(text: str, voice: str, speed: float, model: str,
                    doc: str, breaker: Breaker,
                    kokoro_url: str, chatterbox_url: str,
                    post=_post) -> dict:
    """Returns {audio: bytes, dur_ms, words|None, degraded, synthetic}."""
    import json
    if not text.strip() or len(text) > POISON_CHARS:
        raise RenderError("poison", f"unrenderable sentence ({len(text)} chars)")
    use_chatter = model == "chatterbox" and not breaker.tripped(doc)
    engine_url = chatterbox_url if use_chatter else kokoro_url
    req_voice = _voice_for("chatterbox" if use_chatter else "kokoro", voice)
    body = {"model": model, "input": text,
            "speed": speed, "response_format": "opus"}
    if req_voice is not None:
        body["voice"] = req_voice
    payload = json.dumps(body).encode()
    try:
        raw = post(engine_url + "/v1/audio/speech", payload)
        breaker.record(doc, True)
        # Real servers return audio bytes; word timings arrive via the
        # captioned endpoint (wired in api.py). Words=None unless supplied.
        return {"audio": raw, "dur_ms": 0, "words": None,
                "degraded": model == "chatterbox" and not use_chatter,
                "synthetic": use_chatter, "engine": engine_url}
    except RenderError as e:
        breaker.record(doc, False)
        if use_chatter:
            # Immediate loud fallback to Kokoro for THIS sentence; the
            # breaker opens after CIRCUIT_TRIPS and routes the doc.
            # Rebuild the body with the Kokoro voice mapping (the chatter
            # body carries no voice field).
            kokoro_body = dict(body)
            kokoro_body["voice"] = _voice_for("kokoro", voice) or "af_heart"
            raw = post(kokoro_url + "/v1/audio/speech",
                       json.dumps(kokoro_body).encode())
            return {"audio": raw, "dur_ms": 0, "words": None,
                    "degraded": True, "synthetic": False,
                    "engine": kokoro_url, "fallback_of": e.error_class}
        raise
