"""Task 7 routes. Distinct paths (no collision with frozen stubs in main.py).

- GET /books/{id}/chapters/{n}/playback : manifest/audio resolve + Range
  metadata + cold-202 + prefetch hint + orphan repair direction.
- GET/PUT /books/{id}/progress : sentence-granularity position (LWW+flap).
- GET/PUT /books/{id}/voice-cast : per-character-key cast map (distinct from
  frozen /voice-map stubs; same LWW semantics shared via voice.py).
- GET /pron-dict/preview + POST /voices/sample-preview : editor helpers.
- Range bytes themselves are served by the frozen audio route (Task 2);
  this module owns the parse/206 metadata via playback.py.
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

router = APIRouter(prefix="/api/v1", tags=["playback"])


def _require_token(authorization: str | None) -> None:
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
                            detail="invalid or missing API token")


class ProgressBody(BaseModel):
    chapter_idx: int = 0
    sent_id: int = 0
    position_ms: int = 0
    text_hash: str = ""
    at: str | None = None  # UTC ISO from client; server time wins if absent


@router.get("/books/{book_id}/chapters/{n}/playback")
def playback_info(book_id: int, n: int):
    from . import db
    from .models import Chapter
    from .playback import resolve_playback
    with Session(db.engine) as s:
        row = s.exec(select(Chapter).where(
            Chapter.novel_id == book_id, Chapter.idx == n)).first()
        payload = resolve_playback(book_id, n, db.DATA_DIR, row)
    status = payload.pop("status")
    if status == 202:
        raise HTTPException(status_code=202, detail=payload)
    return payload


@router.get("/books/{book_id}/progress")
def get_progress(book_id: int):
    from . import db
    from .models import Progress
    with Session(db.engine) as s:
        row = s.exec(select(Progress).where(
            Progress.novel_id == book_id)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="no progress yet")
        return row


@router.put("/books/{book_id}/progress")
def put_progress(book_id: int, body: ProgressBody,
                 authorization: str | None = Header(default=None),
                 x_tab_id: str | None = Header(default=None, alias="X-Tab-Id"),
                 x_leader_id: str | None = Header(default=None,
                                                  alias="X-Leader-Id")):
    _require_token(authorization)
    from . import db
    from .progress import apply_push, is_leader_write, parse_utc_iso
    if not is_leader_write(x_tab_id, x_leader_id):
        raise HTTPException(status_code=409,
                            detail="not tab leader; position read-only")
    at = parse_utc_iso(body.at) if body.at else None
    with Session(db.engine) as s:
        return apply_push(s, book_id, body.chapter_idx, body.sent_id,
                          body.position_ms, body.text_hash, at)


@router.get("/books/{book_id}/voice-cast")
def get_voice_cast(book_id: int):
    from . import db
    from .voice import get_cast
    with Session(db.engine) as s:
        return {"novel_id": book_id, "cast": get_cast(s, book_id)}


@router.put("/books/{book_id}/voice-cast")
async def put_voice_cast(book_id: int, request: Request,
                         authorization: str | None = Header(default=None)):
    _require_token(authorization)
    from . import db
    from .voice import put_cast
    body = await request.json()
    mapping = body.get("cast", {})
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=422, detail="cast must be an object")
    with Session(db.engine) as s:
        cast = put_cast(s, book_id, mapping)
    return {"novel_id": book_id, "cast": cast}


@router.get("/books/{book_id}/voice-pref")
def get_voice_pref(book_id: int):
    from . import db
    from .voice import get_auto_synthesize
    with Session(db.engine) as s:
        return {"novel_id": book_id,
                "auto_synthesize": get_auto_synthesize(s, book_id)}


@router.put("/books/{book_id}/voice-pref")
async def put_voice_pref(book_id: int, request: Request,
                         authorization: str | None = Header(default=None)):
    _require_token(authorization)
    from . import db
    from .voice import set_auto_synthesize
    body = await request.json()
    enabled = bool(body.get("auto_synthesize", False))
    with Session(db.engine) as s:
        row = set_auto_synthesize(s, book_id, enabled)
    return {"novel_id": book_id, "auto_synthesize": row.auto_synthesize}


@router.get("/pron-dict/preview")
def pron_preview(text: str = "", pattern: str = "", replacement: str = ""):
    from .voice import preview_pron, validate_pron
    if not pattern:
        return {"hits": {}}
    validate_pron(pattern, replacement)
    return {"hits": preview_pron(text, [(pattern, replacement)])}


class SampleBody(BaseModel):
    text: str = ""
    voice: str = ""
    speed: float = 1.0


@router.post("/voices/sample-preview")
def voice_sample_preview(body: SampleBody,
                         authorization: str | None = Header(default=None)):
    _require_token(authorization)
    from .voice import sample_preview
    if not body.text or not body.voice:
        raise HTTPException(status_code=422,
                            detail="text and voice required")
    return sample_preview(body.text[:2000], body.voice, body.speed)
