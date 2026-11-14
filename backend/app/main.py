"""Frozen route table + health + fail-closed token gate.

Task 1 implements: GET /api/v1/health (deep), API_TOKEN enforcement,
WS handshake. All other frozen routes are declared here with real paths
so UI/playback/OPDS can hardcode them; bodies returning 501 are owned by
their Task (2/3/6/7/8) and filled in there.
"""
import os
import hashlib
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlmodel import Session

from . import db
from .models import MODEL_VER, Job, Novel, Voice


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Single-file SQLite: ensure schema exists at boot. Alembic revisions
    # remain the upgrade path; create_all is the idempotent baseline so a
    # fresh /data/db boots green without manual migration.
    db.create_all()
    yield


app = FastAPI(title="InkCast", version="1.0.0", lifespan=lifespan)

try:  # Task 7/8 additive routers (frozen stubs below untouched).
    # Included BEFORE the stub defs so real handlers win routing order
    # (Starlette matches first-registered first).
    from .routes_playback import router as playback_router
    app.include_router(playback_router)
    from .routes_catalog import router as catalog_router
    app.include_router(catalog_router)
    from .routes_search import router as search_router
    app.include_router(search_router)
except Exception:  # noqa: BLE001 -- health + frozen paths stay up regardless
    pass


HEARTBEAT_PATH = os.environ.get(
    "SCHEDULER_HEARTBEAT_PATH", "/data/db/scheduler.heartbeat"
)
HEARTBEAT_STALE_S = 6 * 3600 + 1800  # scheduler runs 6h+jitter; stale well past that


def _token_configured() -> str:
    tok = os.environ.get("API_TOKEN", "")
    if not tok:
        raise HTTPException(status_code=500, detail="server misconfigured: API_TOKEN unset")
    return tok


def require_token(provided: str | None) -> None:
    """Fail-closed: missing server token -> 500; missing/wrong client token -> 401."""
    expected = _token_configured()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API token")


def token_from_header(authorization: str | None = Header(default=None)) -> None:
    scheme_token = None
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            scheme_token = parts[1]
    require_token(scheme_token)


# --- deep health -----------------------------------------------------------
@app.get("/api/v1/health")
def health() -> JSONResponse:
    checks: dict = {"model_ver": MODEL_VER}
    ok = True

    try:
        db.db_writable()
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        ok = False
        checks["db"] = f"unwritable: {e}"

    try:
        free = shutil.disk_usage(db.DATA_DIR).free
        checks["disk_free_bytes"] = free
        if free < 100 * 1024 * 1024:
            ok = False
            checks["disk"] = "low"
        else:
            checks["disk"] = "ok"
    except Exception as e:  # noqa: BLE001
        ok = False
        checks["disk"] = f"error: {e}"

    try:
        mtime = os.path.getmtime(HEARTBEAT_PATH)
        age = datetime.now(timezone.utc).timestamp() - mtime
        checks["scheduler_heartbeat_age_s"] = int(age)
        checks["scheduler"] = "ok" if age < HEARTBEAT_STALE_S else "stale"
        if age >= HEARTBEAT_STALE_S:
            ok = False
    except FileNotFoundError:
        checks["scheduler"] = "never"  # fresh boot before first scheduler tick
    except Exception as e:  # noqa: BLE001
        ok = False
        checks["scheduler"] = f"error: {e}"

    try:
        with Session(db.engine) as s:
            depth = s.scalar(
                select(func.count(Job.id)).where(Job.status.in_(("queued", "running")))
            )
        checks["queue_depth"] = int(depth or 0)
    except Exception as e:  # noqa: BLE001
        ok = False
        checks["queue_depth"] = f"error: {e}"

    try:
        import docker  # type: ignore  # optional; sidecar probe

        client = docker.from_env()
        c = client.containers.get(os.environ.get("LNCRAWL_CONTAINER", "inkcast-lncrawl"))
        checks["lncrawl"] = c.status
        if c.status != "running":
            ok = False
    except Exception:
        checks["lncrawl"] = "unreachable"
        # Purposefully not ok=False on fresh dev boot without socket; compose
        # health relies on db/disk/scheduler/queue. Sidecar state is informational
        # here; crawl jobs fail LOUD via jobs rows (Task 3).

    checks["status"] = "ok" if ok else "degraded"
    return JSONResponse(status_code=200 if ok else 503, content=checks)


# --- frozen route stubs (real paths, bodies owned by later tasks) -----------
@app.get("/api/v1/books")
def list_books():
    with Session(db.engine) as s:
        return s.exec(select(Novel)).all()


@app.get("/api/v1/books/{book_id}/manifest")
def book_manifest(book_id: int):
    raise HTTPException(status_code=501, detail="owned by Task 2 (render)")


@app.get("/api/v1/books/{book_id}/chapters/{n}")
def chapter_json(book_id: int, n: int, format: str = "json"):
    raise HTTPException(status_code=501, detail="owned by Task 2 (render)")


@app.get("/api/v1/books/{book_id}/chapters/{n}/audio.opus")
def chapter_audio(book_id: int, n: int):
    raise HTTPException(status_code=501, detail="owned by Task 2/7 (render/playback)")


@app.get("/api/v1/books/{book_id}/chapters/{n}/chapter.json")
def chapter_sidecar(book_id: int, n: int):
    raise HTTPException(status_code=501, detail="owned by Task 2 (render)")


@app.post("/api/v1/books/{book_id}/chapters/{n}/synthesize")
def synthesize(book_id: int, n: int, authorization = Header(default=None)):
    token_from_header(authorization)
    raise HTTPException(status_code=501, detail="owned by Task 2 (render)")


@app.get("/api/v1/voices")
def list_voices():
    with Session(db.engine) as s:
        return s.exec(select(Voice)).all()


@app.put("/api/v1/voices")
def put_voices(authorization = Header(default=None)):
    token_from_header(authorization)
    raise HTTPException(status_code=501, detail="owned by Task 7 (voice suite)")


@app.get("/api/v1/books/{book_id}/voice-map")
def get_voice_map(book_id: int):
    from .voice import get_cast
    with Session(db.engine) as s:
        return {"novel_id": book_id, "cast": get_cast(s, book_id)}


@app.put("/api/v1/books/{book_id}/voice-map")
async def put_voice_map(book_id: int, request: Request,
                        authorization = Header(default=None)):
    token_from_header(authorization)
    from .voice import put_cast
    body = await request.json()
    mapping = body.get("cast", body) if isinstance(body, dict) else None
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=422, detail="cast must be an object")
    try:
        with Session(db.engine) as s:
            cast = put_cast(s, book_id, mapping)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"novel_id": book_id, "cast": cast}


@app.get("/api/v1/pron-dict")
def get_pron():
    from sqlmodel import select
    from .models import PronEntry
    with Session(db.engine) as s:
        rows = s.exec(select(PronEntry)).all()
        return {"rules": [{"pattern": r.pattern,
                           "replacement": r.replacement} for r in rows]}


@app.put("/api/v1/pron-dict")
async def put_pron(request: Request, authorization = Header(default=None)):
    token_from_header(authorization)
    from sqlmodel import select
    from .models import PronEntry
    from .voice import validate_pron
    body = await request.json()
    if isinstance(body, dict):
        rules = body.get("rules", [body])
    elif isinstance(body, list):
        rules = body
    else:
        raise HTTPException(status_code=422,
                            detail="rules must be an object or list")
    try:
        clean = []
        for r in rules:
            validate_pron(r.get("pattern", ""), r.get("replacement", ""))
            clean.append((r["pattern"], r.get("replacement", "")))
    except (AttributeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    with Session(db.engine) as s:
        for pattern, replacement in clean:
            row = s.exec(select(PronEntry).where(
                PronEntry.pattern == pattern)).first()
            if row is None:
                s.add(PronEntry(pattern=pattern, replacement=replacement))
            else:
                row.replacement = replacement
                s.add(row)
        s.commit()
        rows = s.exec(select(PronEntry)).all()
        return {"rules": [{"pattern": r.pattern,
                           "replacement": r.replacement} for r in rows]}


@app.post("/api/v1/admin/backup")
def admin_backup(authorization = Header(default=None)):
    token_from_header(authorization)
    from .backup import BackupError, export_backup
    try:
        db_path = db.engine.url.database or "/data/db/inkcast.db"
    except Exception:  # noqa: BLE001
        db_path = "/data/db/inkcast.db"
    backups = os.path.join(db.DATA_DIR, "backups")
    os.makedirs(backups, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = os.path.join(backups, f"inkcast-{stamp}.bak")
    try:
        export_backup(db_path, db.DATA_DIR, out,
                      passphrase=None, allow_plaintext=True)
    except BackupError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    h = hashlib.sha256()
    with open(out, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"path": out, "sha256": h.hexdigest()}


@app.get("/api/v1/jobs")
def list_jobs(authorization = Header(default=None)):
    token_from_header(authorization)
    with Session(db.engine) as s:
        return s.exec(select(Job)).all()


# --- WS control-plane handshake (Task 15: WS control only, no PCM) ---------
try:
    from fastapi.staticfiles import StaticFiles

    if os.path.isdir("/app/frontend/dist"):
        # Mount LAST: /api/* + explicit routes above win; dist serves the
        # PWA with SPA fallback. Missing dir (fresh clone) -> API-only boot.
        app.mount("/", StaticFiles(directory="/app/frontend/dist",
                                   html=True), name="pwa")
    else:
        print("WARN: /app/frontend/dist missing — run `npm run build` in frontend/ (API-only boot)")
except Exception as e:  # noqa: BLE001 -- static UI is optional for API health
    print(f"WARN: PWA mount skipped: {e}")


@app.websocket("/api/v1/ws")
async def ws_control(ws: WebSocket):
    await ws.accept()
    token = ws.query_params.get("token")
    try:
        require_token(token)
    except HTTPException:
        await ws.close(code=4401)
        return
    try:
        while True:
            msg = await ws.receive_json()
            await ws.send_json({"ack": msg.get("cmd"), "status": "not-implemented-task7"})
    except WebSocketDisconnect:
        pass
