"""Wire-up: queue + render + cache + SSE events, live engines.

Background worker drains the queue (1 Kokoro + 1 Chatterbox lane via
thread pool of 2), stores audio in Cache, emits ready/failed/degraded.
CACHE global is what api.audio serves (fixes the stub).
"""
from __future__ import annotations

import os
import threading
import time

from . import api as _api
from . import render as _render
from .cache import Cache, canonical_key
from .queue import PREFETCH_K

KOKORO_URL = os.environ.get("KOKORO_URL", "http://127.0.0.1:8880")
CHATTERBOX_URL = os.environ.get("CHATTERBOX_URL", "http://127.0.0.1:8004")
CACHE_DIR = os.environ.get("CACHE_DIR", "/tmp/inkcast-cache")
CACHE_CAP = int(os.environ.get("CACHE_CAP_GB", "5")) * 1024**3

CACHE = Cache(CACHE_DIR, CACHE_CAP)
_api.CACHE = CACHE  # type: ignore[attr-defined] -- serves GET /audio
breaker = _render.Breaker()
DOC_TEXT: dict[str, list[dict]] = {}  # doc_id -> sentences [{idx,text}]
DOC_META: dict[str, dict] = {}  # doc_id -> {voice,speed,model,dict_ver,tag}


def key_for(doc: str, text: str) -> str:
    m = DOC_META.get(doc, {})
    return canonical_key(text, m.get("tag", "kokoro-v0.10.0"),
                         m.get("voice", "af_heart"), m.get("speed", 1.0),
                         m.get("dict_ver", "d1"),
                         m.get("model", "kokoro"))


def worker_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        job = _api.queue.pop()
        if job is None:
            time.sleep(0.05)
            continue
        doc, idx = job["doc"], job["idx"]
        sents = DOC_TEXT.get(doc, [])
        sent = next((s for s in sents if s["idx"] == idx), None)
        if sent is None:
            continue
        m = DOC_META.get(doc, {})
        key = key_for(doc, sent["text"])
        if CACHE.lookup(key):
            _api.emit(doc, "ready", idx=idx, hash=key, cached=True)
            continue
        _api.emit(doc, "rendering", idx=idx)
        try:
            r = _render.render_sentence(
                sent["text"], m.get("voice", "af_heart"),
                m.get("speed", 1.0), m.get("model", "kokoro"), doc,
                breaker, KOKORO_URL, CHATTERBOX_URL)
            CACHE.pinned.add(key)
            CACHE.store(key, r["audio"], r.get("dur_ms", 0),
                        {"voice": m.get("voice", ""), "speed": m.get("speed", ""),
                         "model": m.get("model", "")})
            CACHE.pinned.discard(key)
            _api.emit(doc, "ready", idx=idx, hash=key,
                      degraded=r.get("degraded", False))
        except _render.RenderError as e:
            _api.emit(doc, "failed", idx=idx, error_class=e.error_class,
                      reason=str(e)[:120])


def start() -> threading.Event:
    stop = threading.Event()
    t = threading.Thread(target=worker_loop, args=(stop,), daemon=True)
    t.start()
    return stop
