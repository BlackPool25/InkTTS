"""Worker engine-loop tests: hermetic (tmp sqlite, stub exec, no sidecar)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from backend.app import models as _models  # noqa: F401 (register tables)
from backend.app.models import Job

MAKE_EPUB = (
    "import os, zipfile; d = os.environ['SC_MOUNT'];"
    " z = zipfile.ZipFile(os.path.join(d, 'ch1.epub'), 'w');"
    " z.writestr('OEBPS/ch1.xhtml', '<p>' + 'Hello world, this is chapter one. ' * 30 + '</p>');"
    " z.close()"
)


@pytest.fixture()
def session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/w.db")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _run(session, tmp_path, monkeypatch, **kw):
    from backend.app import worker as W
    sc = tmp_path / "sidecar"
    sc.mkdir()
    monkeypatch.setenv("SC_MOUNT", str(sc))
    kw.setdefault("sidecar_mount", str(sc))
    kw.setdefault("dest_base", str(tmp_path / "jobs"))
    kw.setdefault("heartbeat_path", str(tmp_path / "hb" / "scheduler.heartbeat"))
    kw.setdefault("state", {})
    out = W.run_once(session, **kw)
    session.expire_all()
    return out


def _crawl_job(session, ref="novel:1:toc", payload=None):
    payload = payload if payload is not None else {
        "novel_id": 1, "url": "https://example.com/n/1", "toc_only": True}
    job = Job(kind="crawl", ref=ref, status="queued",
              payload=json.dumps(payload))
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_crawl_claimed_attempted_completed_and_tick_enqueues(tmp_path, monkeypatch, session):
    job = _crawl_job(session)
    out = _run(session, tmp_path, monkeypatch,
               exec_prefix=["python3", "-c", MAKE_EPUB],
               sources=["https://example.com/novel/9"])
    assert out["enqueued"] == ["crawl:https://example.com/novel/9"]
    row = session.get(Job, job.id)
    assert row.status == "done" and row.attempts == 1  # attempted exactly once
    ticked = [j for j in session.exec(select(Job)).all()
              if j.ref == "crawl:https://example.com/novel/9"]
    assert ticked and ticked[0].status == "done"  # tick job also drained
    assert os.path.isfile(str(tmp_path / "hb" / "scheduler.heartbeat"))


def test_failing_job_records_failed_with_class(tmp_path, monkeypatch, session):
    job = _crawl_job(session)
    out = _run(session, tmp_path, monkeypatch,
               exec_prefix=["sh", "-c", "exit 3", "--"])
    assert out["processed"] == 1
    row = session.get(Job, job.id)
    assert row.status == "failed" and row.error_class == "transient"
    assert row.attempts == 1 and row.error  # LOUD, never silent


def test_poison_job_does_not_stop_loop(tmp_path, monkeypatch, session):
    from sqlmodel import select
    session.add(Job(kind="explode", ref="boom", status="queued", payload=""))
    good = _crawl_job(session, ref="novel:2:toc")
    session.commit()
    out = _run(session, tmp_path, monkeypatch,
               exec_prefix=["python3", "-c", MAKE_EPUB])
    rows = {j.ref: j for j in session.exec(select(Job)).all()}
    assert rows["boom"].status == "failed"  # unknown kind failed, loop lived
    assert rows["novel:2:toc"].status == "done"
    # crawl stored the chapter + enqueued its synth (stub renders in-test)
    synths = [j for j in rows.values() if j.kind == "synth"]
    assert synths and all(j.status == "done" for j in synths)
    assert out["processed"] == 2 + len(synths)


def test_synth_runs_render_pipeline(tmp_path, monkeypatch, session):
    calls = []
    job = Job(kind="synth", ref="novel:1:ch:7", status="queued",
              payload=json.dumps({"novel_id": 1, "idx": 7,
                                  "text": "Hello world. " * 20}))
    session.add(job)
    session.commit()
    session.refresh(job)
    out = _run(session, tmp_path, monkeypatch,
               render_fn=lambda nid, i, t, v="af_heart", s=1.0, d="": calls.append((nid, i)))
    assert calls == [(1, 7)]
    assert session.get(Job, job.id).status == "done"
    assert out["processed"] == 1


def test_synth_gate_rejection_fails_loud(tmp_path, monkeypatch, session):
    def _boom(*a, **k):
        raise ValueError("gate rejected (blocked): challenge signature: cf")
    job = Job(kind="synth", ref="novel:1:ch:8", status="queued",
              payload=json.dumps({"novel_id": 1, "idx": 8,
                                  "text": "x " * 500}))
    session.add(job)
    session.commit()
    session.refresh(job)
    _run(session, tmp_path, monkeypatch, render_fn=_boom)
    row = session.get(Job, job.id)
    assert row.status == "blocked" and row.error_class == "blocked"


def test_heartbeat_fresh_and_stale_lease_swept(tmp_path, monkeypatch, session):
    from datetime import timedelta
    from backend.app.models import utcnow
    stale = Job(kind="crawl", ref="stale", status="running",
                attempts=1, payload=json.dumps({"url": "https://example.com/s"}),
                lease_until=utcnow() - timedelta(seconds=10))
    session.add(stale)
    session.commit()
    hb = str(tmp_path / "hb2" / "scheduler.heartbeat")
    before = time.time()
    _run(session, tmp_path, monkeypatch,
         exec_prefix=["python3", "-c", MAKE_EPUB], heartbeat_path=hb)
    assert os.path.isfile(hb)
    assert time.time() - os.path.getmtime(hb) < 60 and os.path.getmtime(hb) >= before - 1
    assert session.get(Job, stale.id).status == "done"  # swept, reclaimed, run


def test_single_flight_skips_live_url(tmp_path, monkeypatch, session):
    """A live stray for the same URL -> lease renewed, no second spawn."""
    from backend.app import worker as W
    from backend.app.scraper import exec_docker as _ex
    import backend.app.scraper.client as _cl
    job = _crawl_job(session)
    job.status = "running"  # as claim_next would leave it
    session.add(job)
    session.commit()
    monkeypatch.setattr(_ex, "stray_pids", lambda *a, **k: ["4242"])
    def _boom(*a, **k):
        raise AssertionError("must not spawn while a stray lives")
    monkeypatch.setattr(_cl, "run_crawl", _boom)
    sc = tmp_path / "sidecar"
    sc.mkdir()
    W._handle_crawl(session, job, state={}, sidecar_mount=str(sc),
                    dest_base=str(tmp_path / "jobs"))
    row = session.get(Job, job.id)
    assert row.status == "running" and row.attempts == 0  # untouched, lease renewed


def test_timeout_with_progress_ingests_and_continues(tmp_path, monkeypatch, session):
    """Timeout + new bytes -> ingest + renewed lease (not transient-fail)."""
    from backend.app import worker as W
    job = _crawl_job(session)
    sc = tmp_path / "sidecar"
    sc.mkdir()
    text = "<p>" + "The lanterns swayed in the evening wind. " * 30 + "</p>"
    out = W.run_once(
        session, state={}, sidecar_mount=str(sc),
        dest_base=str(tmp_path / "jobs"),
        heartbeat_path=str(tmp_path / "hb" / "scheduler.heartbeat"),
        exec_prefix=["sh", "-c",
                     f"sleep 0.05; printf %s '{text}' > '{sc}/ch9.json'; sleep 5",
                     "--"],
        crawl_timeout_s=0.6, sources=[])
    row = session.get(Job, job.id)
    # grown file ingested (gate passes) instead of failed-transient
    assert row.status == "done", row.error


def test_timeout_without_progress_fails_transient(tmp_path, monkeypatch, session):
    job = _crawl_job(session)
    from backend.app import worker as W
    sc = tmp_path / "sidecar2"
    sc.mkdir()
    W._handle_crawl(session, job, state={}, sidecar_mount=str(sc),
                    dest_base=str(tmp_path / "jobs2"),
                    exec_prefix=["sh", "-c", "sleep 5", "--"], timeout_s=0.2)
    row = session.get(Job, job.id)
    assert row.status == "failed" and row.error_class == "transient"


def test_ingest_empty_returns_unresolved_job_kept_running(tmp_path, session):
    from backend.app import worker as W
    from backend.app.models import Job
    job = Job(kind="crawl", ref="novel:9:toc", status="running",
              payload='{"novel_id": 9, "url": "https://example.com/n/9"}')
    session.add(job)
    session.commit()
    resolved = W._ingest_files(session, job, "https://example.com/n/9", {},
                               [], novel_id=9)
    assert resolved is False
    assert session.get(Job, job.id).status == "running"
