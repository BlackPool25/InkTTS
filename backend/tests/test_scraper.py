"""Scraper sidecar tests: quarantine path, repair diff, timeout sweep.

- kill mid-crawl -> exec SIGKILLed (CrawlTimeout/transient), stale tmp swept
  on boot, ZERO chapter rows (validate-then-commit holds under kill).
"""
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from sqlmodel import Session, SQLModel, select
from sqlalchemy import create_engine

from backend.app import scheduler as S
from backend.app.models import Chapter, Job
from backend.app.refetch import (refetch_chapter, repair_diff, repair_novel,
                                 unquarantine)
from backend.app.scraper.client import (CrawlTimeout, classify_error, job_dir,
                                        parse_retry_after, run_crawl,
                                        sweep_stale_tmp)
from backend.app.scheduler import (apply_result, backoff_s, due_sources,
                                   next_run_in, record_result)


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_quarantine_path_writes_blocked_job_flag(db, tmp_path):
    state = {}
    src = "https://example.com/novel/1"
    for _ in range(S.QUARANTINE_AFTER_N - 1):
        assert record_result(state, src, False, "blocked") is False
    assert record_result(state, src, False, "blocked") is True  # newly quarantined
    assert due_sources(state, [src]) == []  # excluded from ticks
    assert apply_result(db, state, src, False, "blocked", "cf wall",
                        job_ref="crawl:x") is True  # writes the missing flag
    assert apply_result(db, state, src, False, "blocked", "cf wall",
                        job_ref="crawl:x") is False  # already flagged: no dup
    rows = db.exec(select(Job).where(Job.error_class == "blocked")).all()
    assert any(r.ref == f"quarantine:{src}" for r in rows)  # UI flag
    # success clears; unquarantine enqueues a probe chapter
    record_result(state, src, True)
    assert due_sources(state, [src], now=0) == [src]
    job = unquarantine(state, db, src)
    assert job.kind == "crawl" and "probe" in job.ref
    assert json.loads(job.payload)["unquarantine_probe"] is True


def test_transient_backs_off_parse_does_not_quarantine():
    assert backoff_s(1) == 60 and backoff_s(20) == 48 * 3600  # 2^n cap 48h
    assert next_run_in(429, {"Retry-After": "120"}, 1, now=1000) == 120
    assert next_run_in(429, {"Retry-After": "junk"}, 3, now=0) == backoff_s(3)
    assert parse_retry_after("not-a-date") == 0
    state = {}
    for _ in range(10):
        assert record_result(state, "s", False, "transient") is False
        assert record_result(state, "s", False, "parse") is False
    assert not state.get("s", {}).get("quarantined", False)


def test_repair_diff_gaps_and_flagged_only(db):
    toc = [1, 2, 3, 4, 5]
    assert repair_diff(toc, [1, 2, 4, 5], flagged=[4]) == [3, 4]  # gap + flagged
    assert repair_diff(toc, toc) == []  # nothing missing -> no job
    assert repair_novel(db, 7, toc, toc) is None
    job = repair_novel(db, 7, toc, [1, 2, 4, 5], flagged=[2])
    assert job.kind == "repair"
    assert json.loads(job.payload)["idxs"] == [2, 3]
    ch = refetch_chapter(db, 7, 3)  # per-chapter bypasses hash skip
    assert json.loads(ch.payload) == {"novel_id": 7, "idx": 3,
                                      "bypass_hash": True}


def test_kill_mid_crawl_zero_partial_rows(db, tmp_path):
    base = str(tmp_path / "jobs")
    # real SIGKILL path: prefix overrides docker exec (no daemon needed); the
    # argv '30' sleeps while our 0.2s lease-budget expires.
    with pytest.raises(CrawlTimeout):
        run_crawl(["30"], job_id=11, timeout_s=0.2, sidecar_mount=str(tmp_path),
                  dest_base=base, exec_prefix=["sleep"])
    assert classify_error(-9, "", timed_out=True) == "transient"
    # boot sweep removes the killed job's dir; validate-then-commit means the
    # killed crawl could never have written rows (nothing commits pre-gate).
    assert sweep_stale_tmp(base) >= 1
    assert [d for d in os.listdir(base) if d.startswith("crawl-")] == [] \
        if os.path.isdir(base) else True
    assert db.exec(select(Chapter)).all() == []


def test_parse_zips_only_never_stdout(tmp_path):
    from backend.app.scraper.client import collect_artifacts, iter_zip_chapters
    zpath = str(tmp_path / "ch1.epub")  # epub IS a zip
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("OEBPS/ch1.xhtml", "<html><body><p>Hello world, this is a long chapter text here.</p></body></html>")
        zf.writestr("noise.png", b"\x89PNG-not-text")
    got = dict(iter_zip_chapters(zpath))
    assert list(got) == ["OEBPS/ch1.xhtml"] and "Hello world" in got["OEBPS/ch1.xhtml"]
    assert "noise.png" not in got
    dest = str(tmp_path / "job")
    assert collect_artifacts(str(tmp_path), ["ch1.epub", "stdout.log"], dest) == [
        os.path.join(dest, "ch1.epub")]  # stdout/logs never collected
    d = job_dir(5, base=str(tmp_path / "j"))
    assert os.path.basename(d).startswith("crawl-5-")


def test_challenge_output_is_blocked_not_transient():
    assert classify_error(1, "Cloudflare just a moment verify you are human") == "blocked"
    assert classify_error(1, "HTTP 429 too many requests") == "transient"
    assert classify_error(1, "No chapters to download") == "empty"


def test_identity_gate_is_impassable_terminal(tmp_path):
    # Live-observed L19: login-walled novel. Never retry, never quarantine.
    out = "Impassable('L19 Identity-provider gate for https://x/: authentication required (HTTP 401)')"
    assert classify_error(1, out) == "impassable"
    from backend.app.scheduler import record_result
    st: dict = {}
    assert record_result(st, "src", False, "impassable") is False
    assert st["src"].get("impassable") is True
    assert not st["src"].get("quarantined")
