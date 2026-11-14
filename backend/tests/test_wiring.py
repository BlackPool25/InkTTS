"""Wave 2 route-wiring tests: thin delegation in main.py over tested bodies.

Covers: POST /api/v1/admin/backup export round-trip (file exists, sha256
matches, manifest hashes verify, re-import is a content-hash no-op),
voice-map PUT/GET round-trip, pron-dict PUT/GET + preview 200,
POST /voices/sample-preview 200 (served by routes_playback).
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from backend.app import db as _db
from backend.app import models as _models  # noqa: F401 (register tables)
from backend.app import voice as _voice  # noqa: F401 (NovelVoicePref table)

TOKEN = "wire-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    eng = create_engine(f"sqlite:///{tmp_path}/wire.db")
    monkeypatch.setattr(_db, "engine", eng)
    monkeypatch.setattr(_db, "DATA_DIR", str(tmp_path))
    SQLModel.metadata.create_all(eng)
    from fastapi.testclient import TestClient

    from backend.app.main import app
    return TestClient(app, raise_server_exceptions=False), eng


def test_backup_export_roundtrip_and_reimport_noop(tmp_path, monkeypatch):
    client, _eng = _client(tmp_path, monkeypatch)
    assert client.post("/api/v1/admin/backup").status_code == 401
    r = client.post("/api/v1/admin/backup", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert os.path.isfile(body["path"])
    h = hashlib.sha256()
    with open(body["path"], "rb") as f:
        h.update(f.read())
    assert body["sha256"] == h.hexdigest()

    from backend.app import backup as B
    manifest = B._verify_file(body["path"], None)  # manifest hashes ok
    assert manifest["format"] == B.FORMAT
    live = str(tmp_path / "wire.db")
    first = B.import_backup(body["path"], live, str(tmp_path))
    assert first["noop"] is False
    second = B.import_backup(body["path"], live, str(tmp_path))
    assert second["noop"] is True  # content-hash dedupe: re-import no-op


def test_voice_map_put_get_roundtrip(tmp_path, monkeypatch):
    client, eng = _client(tmp_path, monkeypatch)
    with Session(eng) as s:
        n = _models.Novel(title="T", author="A")
        s.add(n)
        s.commit()
        s.refresh(n)
        nid = n.id
    assert client.put(f"/api/v1/books/{nid}/voice-map",
                      json={"cast": {"n": "v"}}).status_code == 401
    r = client.put(f"/api/v1/books/{nid}/voice-map", headers=AUTH,
                   json={"cast": {"narrator": "af_heart"}})
    assert r.status_code == 200, r.text
    assert r.json() == {"novel_id": nid, "cast": {"narrator": "af_heart"}}
    r = client.get(f"/api/v1/books/{nid}/voice-map")
    assert r.status_code == 200
    assert r.json() == {"novel_id": nid, "cast": {"narrator": "af_heart"}}
    bad = client.put(f"/api/v1/books/{nid}/voice-map", headers=AUTH,
                     json={"cast": {"": "v"}})
    assert bad.status_code == 422


def test_pron_dict_put_get_and_preview(tmp_path, monkeypatch):
    client, _eng = _client(tmp_path, monkeypatch)
    assert client.put("/api/v1/pron-dict",
                      json={"rules": []}).status_code == 401
    r = client.put("/api/v1/pron-dict", headers=AUTH,
                   json={"rules": [{"pattern": "Ryu",
                                    "replacement": "ree-YOO"}]})
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/pron-dict")
    assert r.status_code == 200
    assert {"pattern": "Ryu", "replacement": "ree-YOO"} in r.json()["rules"]
    bad = client.put("/api/v1/pron-dict", headers=AUTH,
                     json={"rules": [{"pattern": "", "replacement": "x"}]})
    assert bad.status_code == 422
    r = client.get("/api/v1/pron-dict/preview",
                   params={"text": "Ryu is here", "pattern": "Ryu",
                           "replacement": "ree-YOO"})
    assert r.status_code == 200
    assert r.json()["hits"] == {"Ryu": 1}


def test_sample_preview_post(tmp_path, monkeypatch):
    client, _eng = _client(tmp_path, monkeypatch)
    assert client.post("/api/v1/voices/sample-preview",
                       json={"text": "hi", "voice": "v"}).status_code == 401
    r = client.post("/api/v1/voices/sample-preview", headers=AUTH,
                    json={"text": "Hello world.", "voice": "af_heart",
                          "speed": 1.0})
    assert r.status_code == 200, r.text
    assert r.json()["voice"] == "af_heart"
    assert "render_key" in r.json()
