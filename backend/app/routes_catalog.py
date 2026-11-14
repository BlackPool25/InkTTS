"""Task 8 additive routes: OPDS catalog/search + per-novel M4B export.

All three paths live under the existing /api/v1/opds* Caddy exemption and
re-verify the per-device token per request (query ?token= or Authorization:
Bearer) via opds_auth — fail-closed 401, token never authorizes anything
outside the exemption. The frozen stubs in main.py are untouched; this router
is included before them so it wins routing order.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlmodel import Session, select

router = APIRouter(tags=["catalog"])


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _token(request: Request) -> str | None:
    from .opds import resolve_token
    return resolve_token(request.query_params.get("token"),
                         request.headers.get("authorization"))


@router.get("/api/v1/opds/catalog.xml")
def catalog_xml(request: Request):
    from . import db
    from .models import Novel
    from .opds import PROFILE, catalog_feed
    from .opds_auth import authorize_opds_request
    token = _token(request)
    with Session(db.engine) as s:
        if not authorize_opds_request(request.url.path, token, s):
            raise HTTPException(status_code=401,
                                detail="invalid or missing OPDS token")
        novels = s.exec(select(Novel).order_by(Novel.title)).all()
        xml = catalog_feed(novels, _base_url(request), token)
    return Response(content=xml, media_type=PROFILE)


@router.get("/api/v1/opds/search")
def catalog_search(request: Request, q: str = ""):
    from . import db
    from .models import Novel
    from .opds import PROFILE, search_feed
    from .opds_auth import authorize_opds_request
    token = _token(request)
    with Session(db.engine) as s:
        if not authorize_opds_request(request.url.path, token, s):
            raise HTTPException(status_code=401,
                                detail="invalid or missing OPDS token")
        novels = s.exec(select(Novel).order_by(Novel.title)).all()
        xml = search_feed(novels, q, _base_url(request), token)
    return Response(content=xml, media_type=PROFILE)


@router.get("/api/v1/opds/books/{book_id}.m4b")
def book_m4b(book_id: int, request: Request):
    from . import db
    from .gate import record_failure
    from .m4b import (M4BError, M4BUnavailable, build_m4b, m4b_relpath,
                      sidecar_duration_ms)
    from .models import Chapter, Novel
    from .opds_auth import authorize_opds_request
    token = _token(request)
    with Session(db.engine) as s:
        if not authorize_opds_request(request.url.path, token, s):
            raise HTTPException(status_code=401,
                                detail="invalid or missing OPDS token")
        if s.get(Novel, book_id) is None:
            raise HTTPException(status_code=404, detail="novel not found")
        rows = s.exec(select(Chapter).where(
            Chapter.novel_id == book_id).order_by(Chapter.idx)).all()
        ready = [c for c in rows
                 if c.status == "ready" and c.audio_path and c.sidecar_path
                 and os.path.isfile(os.path.join(db.DATA_DIR, c.audio_path))
                 and os.path.isfile(os.path.join(db.DATA_DIR, c.sidecar_path))]
        if not ready:
            raise HTTPException(status_code=404,
                                detail="no ready chapters to export")
        rel = m4b_relpath(book_id)
        out_abs = os.path.join(db.DATA_DIR, rel)
        try:
            audio = [os.path.join(db.DATA_DIR, c.audio_path) for c in ready]
            durs = [sidecar_duration_ms(
                os.path.join(db.DATA_DIR, c.sidecar_path)) for c in ready]
            fresh = (os.path.isfile(out_abs) and
                     os.path.getmtime(out_abs) >= max(
                         os.path.getmtime(p) for p in audio))
            if not fresh:
                build_m4b(audio, [c.title or f"Chapter {c.idx}" for c in ready],
                          durs, out_abs)
        except M4BUnavailable as e:
            record_failure(s, "export", f"novel:{book_id}:m4b", "transient",
                           str(e))
            raise HTTPException(status_code=503, detail=str(e)) from e
        except M4BError as e:
            record_failure(s, "export", f"novel:{book_id}:m4b", "transient",
                           str(e))
            raise HTTPException(status_code=409, detail=str(e)) from e
    return FileResponse(out_abs, media_type="audio/mp4",
                        filename=f"inkcast-{book_id}.m4b")
