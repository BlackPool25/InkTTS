"""Frozen route table + health + fail-closed token gate.

Task 1 implements: GET /api/v1/health (deep), API_TOKEN enforcement,
WS handshake. All other frozen routes are declared here with real paths
so UI/playback/OPDS can hardcode them; bodies returning 501 are owned by
their Task (2/3/6/7/8) and filled in there.
"""
import os
import secrets
import shutil
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlmodel import Session

from . import db
from .models import MODEL_VER, Job, Novel, Voice

app = FastAPI(title="InkCast", version="1.0.0")

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
    raise HTTPException(status_code=501, detail="owned by Task 7 (voice suite)")


@app.put("/api/v1/books/{book_id}/voice-map")
def put_voice_map(book_id: int, authorization = Header(default=None)):
    token_from_header(authorization)
    raise HTTPException(status_code=501, detail="owned by Task 7 (voice suite)")


@app.get("/api/v1/pron-dict")
def get_pron():
    raise HTTPException(status_code=501, detail="owned by Task 2/7 (pron)")


@app.put("/api/v1/pron-dict")
def put_pron(authorization = Header(default=None)):
    token_from_header(authorization)
    raise HTTPException(status_code=501, detail="owned by Task 2/7 (pron)")


@app.get("/api/v1/opds/catalog.xml")
def opds_catalog():
    raise HTTPException(status_code=501, detail="owned by Task 8 (OPDS)")


@app.get("/api/v1/opds/search")
def opds_search(q: str = ""):
    raise HTTPException(status_code=501, detail="owned by Task 8 (OPDS)")


@app.post("/api/v1/admin/backup")
def admin_backup(authorization = Header(default=None)):
    token_from_header(authorization)
    raise HTTPException(status_code=501, detail="owned by Task 6 (backup)")


@app.get("/api/v1/jobs")
def list_jobs(authorization = Header(default=None)):
    token_from_header(authorization)
    with Session(db.engine) as s:
        return s.exec(select(Job)).all()


# --- WS control-plane handshake (Task 15: WS control only, no PCM) ---------
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
