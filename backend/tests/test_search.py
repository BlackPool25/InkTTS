"""Novel search + add-novel flow: parser units, timeout, routes.

Parser fixture: 6 verbatim lines from the real 2026-11-14 live probe
(`lncrawl search "lord of mysteries"`, inkcast-lncrawl sidecar).
Full-output regression: backend/tests/fixtures/search_sample.txt (same probe).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from backend.app import db as _db
from backend.app import models as _models  # noqa: F401 (register tables)
from backend.app.scraper.client import CrawlTimeout, classify_error
from backend.app.scraper.search import parse_search_output, search_novels

# Verbatim live probe fragment (title group + arrow urls + statuses).
SAMPLE = """📖 Lord of the Mysteries  (4 results)
  ➡ https://novelbuddy.com/lord-of-the-mysteries
    completed
  ➡ https://novelfire.net/book/lord-of-the-mysteries
    |
"""

TOKEN = "search-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_parser_sample_group_arrows_statuses():
    hits = parse_search_output(SAMPLE)
    assert len(hits) == 2
    assert hits[0] == {"title": "Lord of the Mysteries",
                       "url": "https://novelbuddy.com/lord-of-the-mysteries",
                       "source": "novelbuddy.com", "status": "completed",
                       "raw": "➡ https://novelbuddy.com/lord-of-the-mysteries"}
    assert hits[1]["source"] == "novelfire.net"
    assert hits[1]["status"] == "unknown"  # bare '|' -> unknown, not crash


def test_parser_ongoing_and_noise_lines():
    text = ("Exhausted('no detection layer for https://x/: gave up')\n"
            "📖 T  (1 results)\n"
            "  ➡ https://a.com/t\n"
            "    ongoing\n")
    hits = parse_search_output(text)
    assert len(hits) == 1 and hits[0]["status"] == "ongoing"
    assert parse_search_output("") == []
    assert parse_search_output("garbage\nmore garbage") == []


def test_parser_full_fixture_regression():
    path = os.path.join(os.path.dirname(__file__), "fixtures",
                        "search_sample.txt")
    hits = parse_search_output(open(path).read())
    assert len(hits) >= 30  # live probe yielded 34 on 2026-11-14
    assert all(h["url"].startswith("http") and h["source"]
               and h["status"] in ("ongoing", "completed", "unknown")
               for h in hits)


def test_search_timeout_is_transient_sigkill():
    with pytest.raises(CrawlTimeout):
        search_novels("x", timeout_s=0.2,
                      exec_prefix=["sh", "-c", "sleep 30; ", "--"])
    assert classify_error(-9, "", timed_out=True) == "transient"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    eng = create_engine(f"sqlite:///{tmp_path}/search.db")
    monkeypatch.setattr(_db, "engine", eng)
    monkeypatch.setattr(_db, "DATA_DIR", str(tmp_path))
    SQLModel.metadata.create_all(eng)
    from fastapi.testclient import TestClient

    from backend.app.main import app
    return TestClient(app, raise_server_exceptions=False), eng


def test_search_socket_missing_is_loud_crawl_error(monkeypatch):
    # Default (SDK) path with no daemon: CrawlError, not FileNotFoundError.
    import sys
    import types as _types

    import backend.app.scraper.search as Smod
    from backend.app.scraper.client import CrawlError as _CE

    stub = _types.ModuleType("docker")
    stub.from_env = lambda **kw: (_ for _ in ()).throw(
        FileNotFoundError(2, "No such file or directory"))
    monkeypatch.setitem(sys.modules, "docker", stub)
    with pytest.raises(_CE, match="docker socket unreachable"):
        Smod.search_novels("shadow slave", timeout_s=5)


def test_search_route_auth_and_shape(tmp_path, monkeypatch):
    import backend.app.scraper.search as S
    client, _eng = _client(tmp_path, monkeypatch)
    assert client.get("/api/v1/search", params={"q": "x"}).status_code == 401
    monkeypatch.setattr(S, "timed_search",
                        lambda q, timeout_s=60: ([{"title": "T", "url": "https://a.com/t",
                                                  "source": "a.com", "status": "ongoing",
                                                  "raw": ""}], 0.1))
    r = client.get("/api/v1/search", params={"q": "t"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"query": "t", "results": [
        {"title": "T", "url": "https://a.com/t",
         "source": "a.com", "status": "ongoing"}], "took_s": 0.1}
    assert client.get("/api/v1/search", headers=AUTH).status_code == 422


def test_add_queues_toc_job_and_status_polls(tmp_path, monkeypatch):
    client, eng = _client(tmp_path, monkeypatch)
    assert client.post("/api/v1/books/add",
                       json={"url": "https://a.com/t"}).status_code == 401
    bad = client.post("/api/v1/books/add", headers=AUTH,
                      json={"url": "not-a-url"})
    assert bad.status_code == 422
    r = client.post("/api/v1/books/add", headers=AUTH,
                    json={"url": "https://a.com/t"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"  # user confirms; chapters NOT added
    with Session(eng) as s:
        novel = s.get(_models.Novel, body["novel_id"])
        assert novel is not None and novel.source_url == "https://a.com/t"
        job = s.get(_models.Job, body["job_id"])
        assert job.kind == "crawl" and "toc" in job.ref
        assert json.loads(job.payload)["toc_only"] is True
    st = client.get(f"/api/v1/books/add/status/{body['job_id']}",
                    headers=AUTH)
    assert st.status_code == 200 and st.json()["ready"] is False
    assert client.get("/api/v1/books/add/status/999999",
                      headers=AUTH).status_code == 404
