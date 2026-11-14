"""Single-worker engine loop: scheduler tick + jobs queue drain.

Sequential (one SQLite writer, no parallel workers): each pass sweeps stale
leases, enqueues due conditional polls via scheduler helpers, drains the queue
with claim_next (crawl -> run_crawl+gate ingest; synth -> render pipeline),
then writes the scheduler heartbeat read by /health. Per-job exceptions fail
the row LOUD and never crash the loop.
"""

from __future__ import annotations

import asyncio
import json
import random
import time

EXEC_PREFIX: list[str] | None = None  # test hook: local subprocess, no sidecar
CRAWL_TIMEOUT_S = 300.0  # <= job lease; expiry raises CrawlTimeout (transient)


def _payload(job) -> dict:
    try:
        return json.loads(job.payload) if job.payload else {}
    except (ValueError, TypeError):
        return {}


def _handle_crawl(session, job, *, state: dict, exec_prefix=None,
                  sidecar_mount=None, dest_base=None,
                  timeout_s: float = CRAWL_TIMEOUT_S) -> None:
    from . import jobs as _jobs
    from . import scheduler as _sched
    from .scraper.client import (CrawlError, build_crawl_argv,
                                 iter_zip_chapters, run_crawl)

    payload = _payload(job)
    url = payload.get("url") or payload.get("source") or ""
    if not url and isinstance(job.ref, str) and job.ref.startswith("http"):
        url = job.ref
    source = payload.get("source") or url
    if not url:
        _jobs.fail(session, job.id, "transient",
                   f"crawl {job.ref}: no url/source in payload")
        return
    argv = build_crawl_argv(url, resume=payload.get("resume", True))
    try:
        kw = {}
        if sidecar_mount is not None:
            kw["sidecar_mount"] = sidecar_mount
        if dest_base is not None:
            kw["dest_base"] = dest_base
        result = run_crawl(argv, job.id, timeout_s,
                           exec_prefix=exec_prefix if exec_prefix is not None
                           else EXEC_PREFIX, **kw)
    except CrawlError as e:
        _jobs.fail(session, job.id, e.error_class or "transient", str(e))
        _sched.record_result(state, source, False, e.error_class or "transient")
        return
    # Ingest: artifact FILES only (never stdout), gate before any row.
    texts: list[str] = []
    for path in result.files:
        try:
            if path.lower().endswith((".zip", ".epub")):
                texts.extend(t for _, t in iter_zip_chapters(path))
            elif path.lower().endswith((".json", ".txt")):
                with open(path, encoding="utf-8", errors="replace") as f:
                    texts.append(f.read())
        except OSError:
            continue
    from .gate import gate_text

    failures = [g for t in texts for g in [gate_text(t)] if not g.ok]
    if texts and len(failures) == len(texts):
        first = failures[0]
        _jobs.fail(session, job.id, first.error_class, first.reason)
        _sched.record_result(state, source, False, first.error_class)
        return
    _jobs.complete(session, job.id)
    _sched.record_result(state, source, True)


def _handle_synth(session, job, *, render_fn=None, data_dir=None) -> None:
    from . import jobs as _jobs

    payload = _payload(job)
    novel_id, idx = payload.get("novel_id"), payload.get("idx")
    text = payload.get("text")
    if novel_id is None or idx is None or not text:
        _jobs.fail(session, job.id, "transient",
                   f"synth {job.ref}: payload needs novel_id/idx/text")
        return
    fn = render_fn
    if fn is None:
        from . import db as _db
        from .render import render_chapter_text as _render

        def fn(nid, i, t, voice="af_heart", speed=1.0, data=""):  # noqa: ANN001,ANN202
            return _render(nid, i, t, voice, speed,
                           data_dir or data or _db.DATA_DIR)
    try:
        fn(novel_id, idx, text, payload.get("voice", "af_heart"),
           payload.get("speed", 1.0), data_dir or "")
        _jobs.complete(session, job.id)
    except ValueError as e:  # gate rejection (render already failed LOUD)
        msg = str(e)
        cls = next((c for c in ("blocked", "empty", "parse") if c in msg),
                   "transient")
        _jobs.fail(session, job.id, cls, msg[:2000])
    except Exception as e:  # noqa: BLE001 -- fail the row, never crash loop
        _jobs.fail(session, job.id, "transient", f"synth: {e}"[:2000])


def run_once(session, *, state: dict | None = None, sources=(),
             exec_prefix=None, render_fn=None, heartbeat_path=None,
             data_dir=None, sidecar_mount=None, dest_base=None,
             crawl_timeout_s: float = CRAWL_TIMEOUT_S) -> dict:
    """One pass: sweep, tick due sources, drain queue, heartbeat."""
    from . import jobs as _jobs
    from . import scheduler as _sched
    from .scraper.client import sweep_stale_tmp

    state = {} if state is None else state
    now = time.time()
    _jobs.sweep_stale(session)
    sweep_stale_tmp()
    enqueued = []
    for src in _sched.due_sources(state, list(sources), now):
        entry = state.setdefault(src, {})
        job = _jobs.enqueue(
            session, "crawl", f"crawl:{src}",
            payload=json.dumps({"source": src, "resume": True,
                                "headers": _sched.poll_headers(entry)}))
        enqueued.append(job.ref)
        entry["next_run"] = now + _sched.INTERVAL_S + random.uniform(0, _sched.JITTER_S)
    done = failed = 0
    while True:
        job = _jobs.claim_next(session)
        if job is None:
            break
        try:
            if job.kind == "crawl":
                _handle_crawl(session, job, state=state,
                              exec_prefix=exec_prefix,
                              sidecar_mount=sidecar_mount, dest_base=dest_base,
                              timeout_s=crawl_timeout_s)
            elif job.kind == "synth":
                _handle_synth(session, job, render_fn=render_fn,
                              data_dir=data_dir)
            else:
                _jobs.fail(session, job.id, "transient",
                           f"unknown kind {job.kind!r}")
            done += 1
        except Exception as e:  # noqa: BLE001 -- poison job: fail, continue
            try:
                _jobs.fail(session, job.id, "transient", f"worker: {e}"[:2000])
            except Exception:  # noqa: BLE001,S110
                pass
            failed += 1
    _jobs.sweep_stale(session)
    _sched.heartbeat(heartbeat_path or _sched.HEARTBEAT_PATH)
    return {"enqueued": enqueued, "processed": done, "crashed": failed}


async def run_forever(interval_s: float = 60, **kwargs) -> None:
    """Engine loop: one sequential pass per interval, never crashes."""
    from . import db as _db
    from . import jobs as _jobs
    from sqlmodel import Session

    _jobs.boot_stagger()  # restart storm guard (no-op for a single worker)
    while True:
        try:
            with Session(_db.engine) as s:
                run_once(s, **kwargs)
        except Exception:  # noqa: BLE001 -- per-job rows already failed LOUD
            pass
        await asyncio.sleep(interval_s)
