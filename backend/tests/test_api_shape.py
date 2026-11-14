"""P0 shape tests: /books + /jobs serialize with rows present; WS route ordered after StaticFiles mount.

Hermetic: TestClient only, tmp sqlite, no services.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from backend.app import db as _db
from backend.app import models as _models  # noqa: F401 (register tables)

TOKEN = "shape-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    eng = create_engine(f"sqlite:///{tmp_path}/shape.db")
    monkeypatch.setattr(_db, "engine", eng)
    monkeypatch.setattr(_db, "DATA_DIR", str(tmp_path))
    SQLModel.metadata.create_all(eng)
    from fastapi.testclient import TestClient

    from backend.app.main import app
    return TestClient(app, raise_server_exceptions=False), eng


def test_books_and_jobs_200_with_rows(tmp_path, monkeypatch):
    client, eng = _client(tmp_path, monkeypatch)
    with Session(eng) as s:
        s.add(_models.Novel(title="Shape Novel", author="T"))
        s.add(_models.Job(kind="synth", ref="novel:1:ch:1"))
        s.commit()
    r = client.get("/api/v1/books")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list) and r.json()[0]["title"] == "Shape Novel"
    r = client.get("/api/v1/jobs", headers=AUTH)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list) and r.json()[0]["kind"] == "synth"
    r = client.get("/api/v1/voices")
    assert r.status_code == 200, r.text  # same sqlalchemy-select bug class


def test_ws_route_registered_after_static_mount():
    from starlette.staticfiles import StaticFiles

    from backend.app.main import app
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/api/v1/ws" in paths
    # Full-server handshake proof needs a live server; hermetic proxy:
    # the "/" StaticFiles catch-all must be mounted AFTER every
    # @app.get/post/websocket declaration or Starlette matches it first
    # and the WS handshake 403s. So assert no API/WS route sorts after it.
    # Primary check (works even on API-only boot where the mount is
    # skipped): the app.mount("/", ...) statement must sit AFTER the
    # @app.websocket declaration in main.py source, so the "/" catch-all
    # can never shadow the WS handshake (Starlette matches first-reg first).
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app" / "main.py"
    text = src.read_text()
    assert text.index("@app.websocket") < text.index("app.mount("), \
        "StaticFiles catch-all must be mounted AFTER the WS route"
    mount_idx = next((i for i, r in enumerate(app.routes)
                      if isinstance(getattr(r, "app", None), StaticFiles)),
                     None)
    if mount_idx is None:
        return  # API-only boot (/app/frontend/dist absent): nothing to shadow
    ws_idx = paths.index("/api/v1/ws")
    assert ws_idx < mount_idx, "WS route shadowed by StaticFiles catch-all"
    api_after = [getattr(r, "path", None) for r in app.routes[mount_idx + 1:]
                 if (getattr(r, "path", "") or "").startswith("/api/")]
    assert api_after == [], f"API routes shadowed by mount: {api_after}"
