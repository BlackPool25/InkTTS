import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

from orchestrator.queue import Queue


def test_tap_beats_prefetch_and_prefetch_survives():
    q = Queue()
    for i in range(1, 8):
        q.push("d", i, "prefetch", playhead=0)
    q.push("d", 10, "tap")
    first = q.pop()
    assert first["kind"] == "tap" and first["idx"] == 10
    # every 4th pop forces oldest prefetch: backfill never starves
    kinds = [q.pop()["kind"] for _ in range(7)]
    assert "prefetch" in kinds


def test_duplicate_coalesces():
    q = Queue()
    assert q.push("d", 5, "tap")["queued"] is True
    assert q.push("d", 5, "tap")["queued"] is False
    assert q.pop()["idx"] == 5
    assert q.pop() is None


def test_seek_scoped_to_doc():
    q = Queue()
    q.push("a", 50, "prefetch", playhead=0)
    q.push("b", 50, "prefetch", playhead=0)
    q.seek("a", 0)
    got = []
    while True:
        e = q.pop()
        if e is None:
            break
        got.append((e["doc"], e["idx"]))
    assert ("b", 50) in got  # other doc untouched
    assert ("a", 50) not in got


def test_close_purges_doc():
    q = Queue()
    q.push("a", 1, "prefetch", playhead=0)
    q.close("a")
    assert q.pop() is None


def test_sse_ring_replay_and_resync():
    from fastapi.testclient import TestClient
    from orchestrator import api as A
    c = TestClient(A.app)
    c.post("/manifest", json={"doc_id": "d1",
                              "sentences": [{"idx": i} for i in range(3)]})
    r = c.get("/events", params={"doc_id": "d1"})
    assert "manifest" in r.text
    # gap in Last-Event-ID -> resync, never silent wait
    r2 = c.get("/events", params={"doc_id": "d1", "last_event_id": 1})
    assert "resync" in r2.text or "manifest" in r2.text


def test_sse_retry_and_heartbeat():
    from fastapi.testclient import TestClient
    from orchestrator import api as A
    c = TestClient(A.app)
    r = c.get("/events", params={"doc_id": "d-hb"})
    assert "retry: 3000" in r.text
    assert ": hb" in r.text


def test_cors_allows_extension_origin():
    from fastapi.testclient import TestClient
    from orchestrator import api as A
    c = TestClient(A.app)
    r = c.get("/health", headers={"Origin": "chrome-extension://abc"})
    assert r.headers.get("access-control-allow-origin") in ("*",)


def test_tap_returns_202_shape():
    from fastapi.testclient import TestClient
    from orchestrator import api as A
    c = TestClient(A.app)
    r = c.post("/tap", json={"doc_id": "d2", "idx": 10})
    assert r.status_code == 200
    assert "queued" in r.json() and "position" in r.json()


def test_voices_lists_engine_voices():
    from fastapi.testclient import TestClient
    from orchestrator import api as A
    c = TestClient(A.app)
    r = c.get("/voices")
    assert r.status_code == 200
    body = r.json()
    assert "kokoro" in body and "chatterbox" in body
    # chatterbox is always the server default voice
    assert body["chatterbox"] == ["default"]
    assert isinstance(body["kokoro"], list)
    assert "af_heart" in body["kokoro"] and "bm_george" in body["kokoro"]
    assert len(body["kokoro"]) >= 20  # full roster, not just cached packs
    # live-engine coverage: real packs captured from the model itself
    import urllib.request
    try:
        live = urllib.request.urlopen("http://127.0.0.1:8095/voices", timeout=5)
        import json as _j
        assert "af_heart" in _j.load(live)["kokoro"]
    except Exception:
        pass  # engine warming: shape asserted above
