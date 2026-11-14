"""Task 8 hardening gates: Range/If-Range math, M4B chapters, OPDS token wall,
solver-presence quarantine. Command-level gates live in docs/ops/HARDENING.md;
these tests lock the same behavior without needing a live sidecar."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from backend.app import db as _db
from backend.app import models as _models  # noqa: F401 (register tables)
from backend.app import voice as _voice  # noqa: F401 (NovelVoicePref table)
from backend.app.m4b import (M4BUnavailable, chapter_starts_ms,
                             ffmpeg_m4b_cmd, ffmetadata_text, require_ffmpeg)
from backend.app.opds import (catalog_feed, resolve_token, search_feed)


@pytest.fixture()
def session(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr(_db, "engine", eng)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


@pytest.fixture()
def client(session, tmp_path, monkeypatch):  # noqa: ARG001
    monkeypatch.setattr(_db, "DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from backend.app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _seed(session, novels=(), device="koreader"):
    from backend.app.tokens import create_opds_token
    for title, author in novels:
        session.add(_models.Novel(title=title, author=author))
    session.commit()
    return create_opds_token(session, device)


# --- Range/206 single + open + suffix (served by the frozen audio route) -----
def test_range_single_open_suffix():
    from backend.app.playback import content_range_header, parse_range
    assert parse_range("bytes=0-99", 1000) == (0, 99)
    assert parse_range("bytes=500-", 1000) == (500, 999)
    assert parse_range("bytes=-200", 1000) == (800, 999)
    assert parse_range("bytes=0-9999", 1000) == (0, 999)  # clamp
    assert content_range_header(0, 99, 1000) == "bytes 0-99/1000"
    for bad in (None, "", "items=0-1", "bytes=900-100", "bytes=5000-6000",
                "bytes=0-1,2-3"):
        with pytest.raises(ValueError):
            parse_range(bad, 1000)


def test_if_range_stale_forces_full_200():
    from backend.app.playback import if_range_allows
    assert if_range_allows(None, '"abc"') is True  # no header -> honor Range
    assert if_range_allows('"abc"', '"abc"') is True
    assert if_range_allows("abc", '"abc"') is True  # quote-tolerant
    assert if_range_allows('"stale"', '"abc"') is False  # -> ignore, full 200


# --- M4B chapter math (+43ms priming, contiguous) -----------------------------
def _needs_ffmpeg():
    import shutil
    return shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


def test_m4b_chapter_math():
    durs = [60_000, 120_000, 30_000]
    assert chapter_starts_ms(durs) == [43, 60_043, 180_043]
    txt = ffmetadata_text(["One", "Two", "Three"], durs)
    assert txt.startswith(";FFMETADATA1") and "TIMEBASE=1/1000" in txt
    pairs = re.findall(r"START=(\d+)\nEND=(\d+)", txt)
    assert len(pairs) == 3
    for (s, e), dur in zip(pairs, durs):
        assert int(e) - int(s) == dur
    for (_, prev_end), (nxt_start, _) in zip(pairs, pairs[1:]):
        assert prev_end == nxt_start  # gapless: END[i] == START[i+1]
    with pytest.raises(Exception):
        ffmetadata_text(["only"], [1, 2])  # length mismatch


def test_m4b_cmd_locks_codec():
    cmd = ffmpeg_m4b_cmd("l.txt", "c.ffmeta", "o.tmp.m4b")
    for flag in ("-map_metadata", "1", "-c:a", "aac", "-b:a", "64k",
                 "-ac", "1"):
        assert flag in cmd
    assert cmd[-1] == "o.tmp.m4b"  # evictable tmp, rotated by caller


@pytest.mark.skipif(_needs_ffmpeg(), reason="needs ffmpeg+ffprobe")
def test_m4b_live_encode_roundtrip(tmp_path):
    """Real concat->AAC encode: plays, 2 titled chapters, marks contiguous."""
    import shutil
    import subprocess

    from backend.app.m4b import build_m4b
    ins = []
    for name, freq in (("a.opus", 440), ("b.opus", 550)):
        p = str(tmp_path / name)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"sine=frequency={freq}:duration=2",
                        "-c:a", "libopus", "-b:a", "32k", "-ac", "1",
                        "-ar", "24000", p],
                       check=True, timeout=120)
        ins.append(p)
    out = str(tmp_path / "book.m4b")
    assert build_m4b(ins, ["Ch 1", "Ch 2"], [2000, 2000], out) == out
    assert not os.path.exists(out + ".tmp")  # rotated, no litter
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters",
                        "-of", "json", out],
                       capture_output=True, text=True, check=True, timeout=60)
    chs = json.loads(r.stdout)["chapters"]
    assert [c["tags"]["title"] for c in chs] == ["Ch 1", "Ch 2"]
    assert int(chs[1]["start"]) == int(chs[0]["end"])  # contiguity on disk
    assert os.path.getsize(out) > 10000


def test_ffmpeg_missing_is_loud_not_crash(session, monkeypatch):
    import shutil

    from backend.app.gate import record_failure
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    with pytest.raises(M4BUnavailable):
        require_ffmpeg()
    record_failure(session, "export", "novel:1:m4b", "transient",
                   "ffmpeg not on PATH")
    rows = session.exec(select(_models.Job)).all()
    assert any(j.error_class == "transient" and j.status == "failed"
               for j in rows)


# --- OPDS token gate (HTTP): no bypass, ?token= or Bearer ---------------------
def test_opds_catalog_token_gate(client, session):
    tok = _seed(session, [("A & B", "Au"), ("Plain", "Writer")])
    assert client.get("/api/v1/opds/catalog.xml").status_code == 401
    assert client.get("/api/v1/opds/catalog.xml?token=bad").status_code == 401
    r = client.get(f"/api/v1/opds/catalog.xml?token={tok}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/atom+xml")
    assert "urn:inkcast:novel" in r.text and ".m4b?token=" in r.text
    assert "A &amp; B" in r.text  # XML-escaped
    r2 = client.get("/api/v1/opds/catalog.xml",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r2.status_code == 200


def test_opds_search_filters(client, session):
    tok = _seed(session, [("The Hobbit", "Tolkien"), ("Dune", "Herbert")])
    r = client.get(f"/api/v1/opds/search?q=hobbit&token={tok}")
    assert r.status_code == 200
    assert "Hobbit" in r.text and "Dune" not in r.text
    r = client.get(f"/api/v1/opds/search?q=zzz-no-match&token={tok}")
    assert r.status_code == 200 and "<entry>" not in r.text
    assert client.get("/api/v1/opds/search?q=x").status_code == 401


def test_opds_token_never_leaves_exemption(session):
    from backend.app.opds_auth import authorize_opds_request
    tok = _seed(session)
    assert authorize_opds_request("/api/v1/opds/catalog.xml", tok, session)
    assert not authorize_opds_request("/api/v1/books", tok, session)
    assert not authorize_opds_request("/api/v1/admin/backup", tok, session)


def test_resolve_token_sources():
    assert resolve_token("qt", "Bearer ab") == "qt"  # query wins
    assert resolve_token(None, "Bearer ab") == "ab"
    assert resolve_token(None, "Basic ab") is None
    assert resolve_token(None, None) is None


def test_search_feed_unit():
    n1 = _models.Novel(id=1, title="The Hobbit", author="Tolkien")
    n2 = _models.Novel(id=2, title="Dune", author="Herbert")
    xml = search_feed([n1, n2], "dune", "http://x", "t")
    assert "Dune" in xml and "Hobbit" not in xml
    assert catalog_feed([n1], "http://x/", "t").count("<entry>") == 1


# --- export: 404 with nothing ready, loud 503 without ffmpeg -----------------
def test_export_404_without_ready_chapters(client, session):
    tok = _seed(session, [("Lonely", "Au")])
    r = client.get("/api/v1/opds/books/1.m4b?token=" + tok)
    assert r.status_code == 404
    assert client.get("/api/v1/opds/books/999.m4b?token=" + tok).status_code == 404


def test_export_503_loud_when_ffmpeg_missing(client, session, tmp_path,
                                             monkeypatch):
    import shutil

    tok = _seed(session, [("Ready", "Au")])
    rel_a, rel_s = "audio/1/0/1.ab12cd34.opus", "audio/1/0/1.ab12cd34.json"
    for rel, body in ((rel_a, b"OPUSFALLBACK" + b"\0" * 64),
                      (rel_s, json.dumps(
                          {"sentences": [{"t_start": 0.0, "t_end": 60.0,
                                          "text": "Hi."}] }).encode())):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    session.add(_models.Chapter(novel_id=1, idx=1, title="Ch 1", status="ready",
                               audio_path=rel_a, sidecar_path=rel_s))
    session.commit()
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    r = client.get("/api/v1/opds/books/1.m4b?token=" + tok)
    assert r.status_code == 503
    rows = session.exec(select(_models.Job).where(
        _models.Job.kind == "export")).all()
    assert rows and rows[0].error_class == "transient"
    assert not (tmp_path / "audio/1/book.m4b").exists()  # no half file


# --- solver-presence quarantine path: blocked + artifact, never a crash ------
def test_solver_missing_quarantines_not_crashes(session, tmp_path):
    from backend.app.gate import quarantine_artifact, record_failure
    from backend.app.scraper.client import classify_error
    out = "lncrawl: challenge solver missing in image, cannot bypass"
    assert classify_error(1, out) == "blocked"
    assert classify_error(1, "boom") == "transient"  # generic path intact
    record_failure(session, "crawl", "novel:9:ch:1", "blocked",
                   "solver missing in image")
    art = quarantine_artifact("novel9_ch1", out, "solver missing",
                              base_dir=str(tmp_path / "q"))
    assert os.path.isfile(art)
    jobs = session.exec(select(_models.Job)).all()
    assert any(j.error_class == "blocked" and j.status == "blocked"
               for j in jobs)
    assert session.exec(select(_models.Chapter)).all() == []  # never a row


# --- routers wired ------------------------------------------------------------
def test_catalog_router_paths_registered():
    from backend.app.routes_catalog import router
    paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
    assert any(p[0] == "/api/v1/opds/catalog.xml" for p in paths)
    assert any(p[0] == "/api/v1/opds/search" for p in paths)
    assert any(p[0].endswith(".m4b") for p in paths)
