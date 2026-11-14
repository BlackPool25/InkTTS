"""Jobs-table queue truth: atomic claim+lease, coalesce, caps, misfire sweep.

- enqueue coalesces: an existing queued job with same (kind, ref) is reused.
- claim_next is atomic (single UPDATE..WHERE status=queued) and enforces
  max_instances=1 per kind: never two running jobs of the same kind.
- Leases: running jobs hold lease_until; sweep_stale re-queues jobs past
  MISFIRE_S (1800s). boot_stagger spreads workers after restart.
- Per-host caps via JOBS_MAX_PER_HOST env (default 4 concurrent running).
"""
from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlmodel import Session

from .models import Job, utcnow

MISFIRE_S = 1800
MAX_INSTANCES_PER_KIND = 1


def _cap() -> int:
    try:
        return max(1, int(os.environ.get("JOBS_MAX_PER_HOST", "4")))
    except ValueError:
        return 4


def enqueue(s: Session, kind: str, ref: str, payload: str = "") -> Job:
    existing = s.exec(select(Job).where(
        Job.kind == kind, Job.ref == ref,
        Job.status.in_(("queued", "running")))).first()
    if existing:
        return existing
    job = Job(kind=kind, ref=ref, status="queued", payload=payload)
    s.add(job)
    s.commit()
    s.refresh(job)
    return job


def claim_next(s: Session, kinds: list[str] | None = None) -> Job | None:
    """Atomically claim one queued job (lease 30 min). None if capped/busy."""
    running = s.scalar(select(func.count(Job.id)).where(
        Job.status == "running")) or 0
    if running >= _cap():
        return None
    q = select(Job).where(Job.status == "queued")
    if kinds:
        q = q.where(Job.kind.in_(kinds))
    cand = s.exec(q.order_by(Job.created_at)).first()
    if cand is None:
        return None
    # max_instances=1 per kind
    busy_kind = s.scalar(select(func.count(Job.id)).where(
        Job.status == "running", Job.kind == cand.kind)) or 0
    if busy_kind >= MAX_INSTANCES_PER_KIND:
        return None
    now = utcnow()
    res = s.exec(update(Job).where(
        Job.id == cand.id, Job.status == "queued").values(
        status="running", attempts=cand.attempts + 1,
        lease_until=now + timedelta(seconds=MISFIRE_S),
        updated_at=now))
    s.commit()
    if res.rowcount != 1:
        return None  # lost the race; another worker claimed it
    s.refresh(cand)
    return cand


def heartbeat(s: Session, job_id: int, seconds: int = MISFIRE_S) -> None:
    now = utcnow()
    s.exec(update(Job).where(Job.id == job_id).values(
        lease_until=now + timedelta(seconds=seconds), updated_at=now))
    s.commit()


def complete(s: Session, job_id: int) -> None:
    s.exec(update(Job).where(Job.id == job_id).values(
        status="done", error="", updated_at=utcnow()))
    s.commit()


def fail(s: Session, job_id: int, error_class: str, error: str) -> None:
    status = "blocked" if error_class == "blocked" else "failed"
    s.exec(update(Job).where(Job.id == job_id).values(
        status=status, error_class=error_class, error=error,
        updated_at=utcnow()))
    s.commit()


def sweep_stale(s: Session) -> int:
    """Re-queue running jobs whose lease expired (misfire 1800s)."""
    now = utcnow()
    res = s.exec(update(Job).where(
        Job.status == "running", Job.lease_until.is_not(None),
        Job.lease_until < now).values(status="queued", updated_at=now))
    s.commit()
    return res.rowcount or 0


def boot_stagger(worker_idx: int = 0, workers: int = 1,
                 base_s: float = 5.0) -> None:
    """Spread worker startup so a restart never storms the queue/db."""
    if workers > 1:
        time.sleep(base_s * worker_idx + random.uniform(0, base_s))
