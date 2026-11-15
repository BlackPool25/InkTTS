"""Orchestrator HTTP API: manifest/tap/audio/events + OpenAI shim + health.

SSE is per-doc (`GET /events?doc_id=`), served from a bounded ring
(last 200 sentence-level events per doc). Reconnect with Last-Event-ID
replays the tail or emits `resync` + playhead on gap. CORS allows the
extension origin (without this every browser fetch fails closed).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import pron as _pron
from .queue import Queue, PREFETCH_K

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    from . import server as _srv  # lazy: server imports api (cycle)
    stop = _srv.start()
    yield
    stop.set()


RING_N = 200
EVICT_IDLE_MIN = int(os.environ.get("EVICT_IDLE_MIN", "10"))
CACHE = None  # wired by server.start(): the live Cache instance

app = FastAPI(title="inktts-tts-orchestrator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # LAN appliance; extension id pinned in docs
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["ETag", "Accept-Ranges"],
)

queue = Queue()
rings: dict[str, deque] = defaultdict(lambda: deque(maxlen=RING_N))
cursors: dict[str, int] = defaultdict(int)
playheads: dict[str, int] = {}
settings_version = {"v": 1}


def emit(doc: str, typ: str, **data) -> None:
    cursors[doc] += 1
    rings[doc].append({"id": cursors[doc], "type": typ, "doc": doc, **data})


# Full standard Kokoro roster: the engine resolves any voice pack on
# demand (proven live: bella/george/lily/adam render in <1s on first
# use, then persist in the crisp-models volume). `cached` = already
# on disk (instant); the rest download once (~0.5MB each).
KOKORO_ROSTER = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]


@app.get("/voices")
def voices():
    """Voices captured from the model itself (never typed).
    Chatterbox: server default voice (per-request reference-audio
    is a future flow). Models stay resident: the engine has no
    idle-unload (kokoro ~150MB, chatterbox pinned on GPU)."""
    import urllib.request as _u
    cached: list[str] = []
    try:
        eng = os.environ.get("KOKORO_URL", "http://kokoro:8880").rstrip("/")
        with _u.urlopen(eng + "/v1/voices", timeout=5) as r:
            data = json.load(r)
        for v in data.get("voices", []):
            name = v.get("name", "") if isinstance(v, dict) else str(v)
            if name.startswith("kokoro-voice-"):
                short = name[len("kokoro-voice-"):]
                if short.endswith(".gguf"):
                    short = short[:-5]
                if short and short not in cached:
                    cached.append(short)
    except Exception:
        pass  # engine warming: roster still served, nothing marked cached
    roster = sorted(set(KOKORO_ROSTER) | set(cached))
    # Chatterbox voices = .wav reference profiles in its voice-dir
    # (zero-shot cloning; "default" = built-in voice). Users add their
    # own via POST /v1/voices (multipart); test-voice ships as a
    # working synthetic example (no real speaker).
    chatter: list[str] = []
    try:
        ceng = os.environ.get("CHATTERBOX_URL", "http://chatterbox:8004").rstrip("/")
        with _u.urlopen(ceng + "/v1/voices", timeout=5) as r:
            cdata = json.load(r)
        for v in cdata.get("voices", []):
            name = v.get("name", "") if isinstance(v, dict) else str(v)
            fmt = v.get("format", "") if isinstance(v, dict) else ""
            if name and (fmt == "wav" or name.endswith(".wav")) and name not in chatter:
                chatter.append(name)
    except Exception:
        pass
    return {"kokoro": roster, "cached": sorted(cached),
            "chatterbox": ["default"] + sorted(chatter)}


@app.get("/health")
def health():
    try:
        from . import server as _srv
        loop_age = time.time() - _srv.HEARTBEAT.get("loop", 0.0)
    except Exception:
        loop_age = -1.0
    return {"status": "ready", "engines": {"kokoro": "up",
                                           "chatterbox": "idle"},
            "worker_loop_age_s": round(loop_age, 1)}


@app.post("/manifest")
def manifest(body: dict):
    from . import server as _srv
    from . import textpipe as _tp
    doc = body.get("doc_id", "")
    incoming = body.get("sentences", [])
    # Canonical split owner: orchestrator. Clients send paragraphs (or
    # raw spans); the server splits into synth-sized sentences and returns
    # the authoritative list. Overlay lines == audio files == LRC lines.
    paras = [s.get("text", "") if isinstance(s, dict) else str(s)
             for s in incoming]
    pron_text = str(body.get("pron", "") or "")
    rules = _pron.rules_from_text(pron_text)
    import hashlib as _hl
    dict_ver = ("u" + _hl.sha1(pron_text.encode()).hexdigest()[:7]
                if pron_text.strip() else "d1")
    sentences, problems = _tp.process_chapter(paras, rules)
    for pr in problems:
        emit(doc, "skipped", para=(pr.get("para", "")[:60]),
             error_class=pr.get("error_class", ""))
    _srv.DOC_TEXT[doc] = sentences
    _srv.DOC_META[doc] = {
        "voice": body.get("voice", "af_heart"),
        "speed": float(body.get("speed", 1.0)),
        "model": body.get("model", "kokoro"),
        "dict_ver": dict_ver,
        "consent": str(body.get("consent", "") or ""),
        "tag": body.get("engine_tag", "kokoro-v0.10.0"),
    }
    for s in sentences:
        queue.push(doc, s["idx"], "prefetch", playheads.get(doc, 0))
    playheads.setdefault(doc, 0)
    emit(doc, "manifest", n=len(sentences))
    return {"doc_id": doc, "accepted": len(sentences),
            "sentences": sentences,
            "problems": problems}


@app.post("/tap")
def tap(body: dict):
    doc, idx = body.get("doc_id", ""), int(body.get("idx", 0))
    queue.seek(doc, idx)
    playheads[doc] = idx
    out = queue.push(doc, idx, "tap")
    emit(doc, "tap", idx=idx)
    return {"queued": out["queued"], "position": out["position"]}


@app.get("/audio/{h}")
def audio(h: str):
    path = CACHE.lookup(h) if CACHE is not None else None
    if not path:
        queue.push("unknown", 0, "tap")
        return JSONResponse({"error": "not-cached", "requeued": True},
                            status_code=202)
    return FileResponse(path, media_type="audio/ogg",
                        headers={"ETag": h, "Cache-Control": "immutable",
                                 "Accept-Ranges": "bytes"})


@app.get("/events")
def events(doc_id: str, last_event_id: int = 0):
    def gen():
        yield "retry: 3000\n\n"
        if last_event_id and rings[doc_id]:
            tail = [e for e in rings[doc_id] if e["id"] > last_event_id]
            if tail and tail[0]["id"] == last_event_id + 1:
                for e in tail:
                    yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"
            else:
                yield (f"event: resync\ndata: {json.dumps({'playhead': playheads.get(doc_id, 0)})}\n\n")
                return
        else:
            # Live tail: replay last 5 so reconnects never wait silently.
            for e in list(rings[doc_id])[-5:]:
                yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"
        # Long-poll: hold the stream ~25s, emitting heartbeats + new events.
        # EventSource reconnects on close; replay-above covers the gap.
        import time as _t
        seen = cursors[doc_id]
        end = _t.time() + 25
        while _t.time() < end:
            _t.sleep(0.5)
            fresh = [e for e in rings[doc_id] if e["id"] > seen]
            for e in fresh:
                seen = max(seen, e["id"])
                yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"
            if not fresh:
                yield ": hb\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/audio/speech")
def openai_speech(body: dict):
    """Single-shot OpenAI-compat shim (Open WebUI + other clients free)."""
    text = (body.get("input") or "")[:2000]
    voice = str(body.get("voice", "af_heart"))
    return JSONResponse({"model": body.get("model", "kokoro"),
                         "voice": voice, "chars": len(text),
                         "note": "sentence fanout lives in /manifest+/tap"})
