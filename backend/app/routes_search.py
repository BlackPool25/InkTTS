"""Add-novel flow: search (sidecar exec) + TOC-job enqueue + job polling.

- GET  /api/v1/search?q=               token-required, read-only trigger of a
  sidecar exec (same posture as jobs). -> {query, results, took_s}
- POST /api/v1/books/add {url}        no auto-add of chapters: creates the
  novel row + enqueues ONE crawl/TOC job, returns {novel_id, job_id, queued}.
- GET  /api/v1/books/add/status/{id}  jobs-table polling (user-triggered only,
  no background loops server-side).

Additive router; auth/ops/backup/playback/opds/tts untouched.
"""

from __future__ import annotations

import json
import os
import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

router = APIRouter(prefix="/api/v1", tags=["search"])


def _require_caller(authorization: str | None = None,
                    remote_user: str | None = None) -> None:
    """Browser sessions come through Caddy's wall (Remote-User set by
    forward_auth — backend is not port-published so clients can't spoof it);
    scripts/curl use API_TOKEN. Either is sufficient here. Admin/backup
    stay strict-token (see main.py)."""
    if remote_user:
        return
    expected = os.environ.get("API_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=500,
                            detail="server misconfigured: API_TOKEN unset")
    tok = None
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            tok = parts[1]
    if not tok or not secrets.compare_digest(tok, expected):
        raise HTTPException(status_code=401,
                            detail="login required (web) or bad API token")


class AddBody(BaseModel):
    url: str = ""


@router.get("/search")
def search(q: str = "", authorization: str | None = Header(default=None),
               remote_user: str | None = Header(default=None, alias="Remote-User")):
    _require_caller(authorization, remote_user)
    if not q.strip():
        raise HTTPException(status_code=422, detail="q is required")
    from .scraper.client import CrawlError, CrawlTimeout
    from .scraper.search import timed_search
    try:
        hits, took = timed_search(q.strip())
    except CrawlTimeout as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except CrawlError as e:
        raise HTTPException(
            status_code=502,
            detail={"error": str(e), "error_class": e.error_class}) from e
    return {"query": q.strip(),
            "results": [{"title": h["title"], "url": h["url"],
                         "source": h["source"], "status": h["status"]}
                        for h in hits],
            "took_s": round(took, 2)}


@router.post("/books/add")
def add_book(body: AddBody,
             authorization: str | None = Header(default=None),
             remote_user: str | None = Header(default=None, alias="Remote-User")):
    _require_caller(authorization, remote_user)
    url = (body.url or "").strip()
    try:
        host = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        host = ""
    if urlparse(url).scheme not in ("http", "https") or not host:
        raise HTTPException(status_code=422,
                            detail="url must be an http(s) chapter/novel URL")
    from . import db
    from . import jobs as J
    from .models import Novel, utcnow
    with Session(db.engine) as s:
        fp = f"url:{url.lower()}"
        dup = s.exec(select(Novel).where(Novel.fingerprint == fp)).first()
        if dup is not None:
            return {"novel_id": dup.id, "job_id": None,
                    "status": "exists"}
        novel = Novel(title=url, source_url=url, fingerprint=fp,
                      updated_at=utcnow())
        s.add(novel)
        s.commit()
        s.refresh(novel)
        nid = novel.id
        job = J.enqueue(s, "crawl", f"novel:{nid}:toc",
                        json.dumps({"novel_id": nid, "url": url,
                                    "toc_only": True}))
        jid = job.id
    return {"novel_id": nid, "job_id": jid, "status": "queued"}


@router.get("/books/add/status/{job_id}")
def add_status(job_id: int,
               authorization: str | None = Header(default=None),
               remote_user: str | None = Header(default=None, alias="Remote-User")):
    _require_caller(authorization, remote_user)
    from . import db
    from .models import Job
    with Session(db.engine) as s:
        row = s.get(Job, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        payload = None
        try:
            payload = json.loads(row.payload) if row.payload else None
        except (ValueError, TypeError):
            payload = None
        novel_id = (payload or {}).get("novel_id") if isinstance(
            payload, dict) else None
        return {"job_id": row.id, "kind": row.kind, "ref": row.ref,
                "status": row.status, "error_class": row.error_class,
                "error": row.error, "attempts": row.attempts,
                "novel_id": novel_id,
                "ready": row.status == "done"}
