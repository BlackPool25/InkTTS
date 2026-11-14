"""Crawl scheduler: 6h+jitter per novel, conditional re-poll, backoff, quarantine.

- Interval: 6h + uniform jitter (<=30m) per novel, so restarts never storm.
- Conditional: ticks run lncrawl with --resume (fetch only missing chapters =
  the conditional-GET equivalent); poll_headers()/note_response() additionally
  track ETag/Last-Modified + TOC hash, and a 304/unchanged TOC skips the novel
  to the next full interval without enqueueing.
- 429 honors Retry-After; other failures back off 2^n (base 60s, cap 48h).
- N consecutive blocked/empty -> quarantine (source-level, never novel-level:
  a bad source must not poison good novels) + a blocked Job row as the UI flag.
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone

INTERVAL_S = 6 * 3600
JITTER_S = 1800
BACKOFF_BASE_S = 60
BACKOFF_CAP_S = 48 * 3600
QUARANTINE_AFTER_N = 3  # consecutive blocked/empty -> quarantine

STATE_PATH = os.environ.get("SCHEDULER_STATE_PATH", "/data/db/scheduler_state.json")
HEARTBEAT_PATH = os.environ.get("SCHEDULER_HEARTBEAT_PATH",
                                "/data/db/scheduler.heartbeat")


def backoff_s(attempts: int) -> int:
    """2^attempts * base, capped at 48h. attempts>=1."""
    return min(BACKOFF_BASE_S * (2 ** max(0, attempts - 1)), BACKOFF_CAP_S)


def next_run_in(status: int | None, headers: dict | None, attempts: int,
                now: float | None = None) -> float:
    """Seconds until next poll. 429 honors Retry-After (0/unparseable falls
    back to backoff); 304/unchanged returns the full interval."""
    from .scraper.client import parse_retry_after

    now = time.time() if now is None else now
    headers = headers or {}
    if status == 304:
        return INTERVAL_S + random.uniform(0, JITTER_S)
    if status == 429:
        ra = parse_retry_after(str(headers.get("Retry-After", "")), now)
        if ra > 0:
            return ra
    return backoff_s(attempts) if attempts else INTERVAL_S + random.uniform(0, JITTER_S)


def poll_headers(entry: dict) -> dict:
    """Conditional headers for a re-poll (ETag / Last-Modified when known)."""
    out = {}
    if entry.get("etag"):
        out["If-None-Match"] = entry["etag"]
    if entry.get("last_modified"):
        out["If-Modified-Since"] = entry["last_modified"]
    return out


def note_response(entry: dict, status: int | None, headers: dict | None,
                  toc_hash: str | None = None) -> bool:
    """Fold a poll response into state. Returns True if content is unchanged
    (304 or same TOC hash) and the novel may skip this tick."""
    headers = headers or {}
    if headers.get("ETag"):
        entry["etag"] = headers["ETag"]
    if headers.get("Last-Modified"):
        entry["last_modified"] = headers["Last-Modified"]
    entry["last_status"] = status
    if status == 304:
        return True
    if toc_hash and entry.get("toc_hash") == toc_hash:
        return True
    if toc_hash:
        entry["toc_hash"] = toc_hash
    return False


def load_state(path: str = STATE_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: str = STATE_PATH) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)  # tmp+rename: full disk never kills last good state


def record_result(state: dict, source: str, ok: bool,
                  error_class: str = "") -> bool:
    """Fold a crawl outcome into state. Returns True if newly quarantined."""
    entry = state.setdefault(source, {})
    if ok:
        entry["consec"] = 0
        entry.pop("quarantined", None)
        return False
    if error_class == "impassable":
        # Login-walled novel: terminal per novel. Never retry, never count
        # toward source quarantine (the source is fine; this novel needs an
        # account). UI reads the failed Job row's error_class.
        entry["consec"] = 0
        entry["impassable"] = True
        return False
    if error_class in ("blocked", "empty"):
        entry["consec"] = int(entry.get("consec", 0)) + 1
        if entry["consec"] >= QUARANTINE_AFTER_N and not entry.get("quarantined"):
            entry["quarantined"] = True
            return True
    else:  # parse fails LOUD (no retry); transient just backs off
        entry["consec"] = 0
    return False


def quarantined(state: dict, source: str) -> bool:
    return bool(state.get(source, {}).get("quarantined", False))


def due_sources(state: dict, sources: list[str], now: float | None = None) -> list[str]:
    """Sources due for a tick: past next_run and not quarantined."""
    now = time.time() if now is None else now
    return [s for s in sources
            if not quarantined(state, s)
            and float(state.get(s, {}).get("next_run", 0)) <= now]


def apply_result(session, state: dict, source: str, ok: bool,
                 error_class: str = "", error: str = "",
                 job_ref: str = "") -> bool:
    """record_result + persist the UI-visible flag: a blocked Job row on
    (new) quarantine, so /api/v1/jobs shows quarantined sources."""
    from .gate import record_failure
    from sqlalchemy import select as _select
    from .models import Job as _Job

    newly = record_result(state, source, ok, error_class)
    if quarantined(state, source):
        # Invariant: quarantined source <=> blocked Job row (the UI flag).
        # record_result is pure and may have quarantined without a session.
        exists = session.exec(_select(_Job).where(
            _Job.ref == f"quarantine:{source}",
            _Job.error_class == "blocked")).first()
        if exists is None:
            record_failure(session, "crawl", f"quarantine:{source}", "blocked",
                           error or f"{QUARANTINE_AFTER_N} consecutive failures")
            newly = True
    elif not ok and job_ref:
        record_failure(session, "crawl", job_ref, error_class or "transient",
                       error, payload=json.dumps({"source": source}))
    return newly


def heartbeat(path: str = HEARTBEAT_PATH) -> str:
    """Touch the heartbeat file read by /api/v1/health (deep-check)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        f.write(ts)
    return ts


def tick(session, sources: list[str], state: dict | None = None,
         now: float | None = None) -> dict:
    """One scheduler pass: sweep leases+tmp, enqueue due crawls (--resume),
    write heartbeat. Returns {'enqueued': [...], 'quarantined': [...]}."""
    from . import jobs as _jobs
    from .scraper.client import sweep_stale_tmp

    now = time.time() if now is None else now
    state = load_state() if state is None else state
    _jobs.sweep_stale(session)
    sweep_stale_tmp()
    out = {"enqueued": [], "quarantined": []}
    for source in due_sources(state, sources, now):
        entry = state.setdefault(source, {})
        job = _jobs.enqueue(
            session, "crawl", f"crawl:{source}",
            payload=json.dumps({"source": source, "resume": True,
                                "headers": poll_headers(entry)}))
        out["enqueued"].append(job.ref)
        entry["next_run"] = now + INTERVAL_S + random.uniform(0, JITTER_S)
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
    for source, entry in state.items():
        if entry.get("quarantined"):
            out["quarantined"].append(source)
    save_state(state)
    heartbeat()
    return out
