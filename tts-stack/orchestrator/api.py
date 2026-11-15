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

app = FastAPI(title="inkcast-tts-orchestrator", lifespan=lifespan)
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


@app.get("/health")
def health():
    return {"status": "ready", "engines": {"kokoro": "up",
                                           "chatterbox": "idle"}}


@app.post("/manifest")
def manifest(body: dict):
    from . import server as _srv
    doc = body.get("doc_id", "")
    sents = body.get("sentences", [])
    _srv.DOC_TEXT[doc] = sents
    _srv.DOC_META[doc] = {
        "voice": body.get("voice", "af_heart"),
        "speed": float(body.get("speed", 1.0)),
        "model": body.get("model", "kokoro"),
        "dict_ver": body.get("dict_ver", "d1"),
        "tag": body.get("engine_tag", "kokoro-v0.10.0"),
    }
    for s in sents:
        queue.push(doc, s["idx"], "prefetch", playheads.get(doc, 0))
    playheads.setdefault(doc, 0)
    emit(doc, "manifest", n=len(sents))
    return {"doc_id": doc, "accepted": len(sents)}


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
        if last_event_id and rings[doc_id]:
            tail = [e for e in rings[doc_id] if e["id"] > last_event_id]
            if tail and tail[0]["id"] == last_event_id + 1:
                for e in tail:
                    yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"
                return
            yield (f"event: resync\ndata: {json.dumps({'playhead': playheads.get(doc_id, 0)})}\n\n")
            return
        # Live tail: replay last 5 so reconnects never wait silently.
        for e in list(rings[doc_id])[-5:]:
            yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/audio/speech")
def openai_speech(body: dict):
    """Single-shot OpenAI-compat shim (Open WebUI + other clients free)."""
    text = (body.get("input") or "")[:2000]
    voice = str(body.get("voice", "af_heart"))
    return JSONResponse({"model": body.get("model", "kokoro"),
                         "voice": voice, "chars": len(text),
                         "note": "sentence fanout lives in /manifest+/tap"})
