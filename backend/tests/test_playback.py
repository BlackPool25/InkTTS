"""Task 7 playback tests: cold-seek, prefetch, flap, repair, UTC, voice/leader."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel
from sqlalchemy import create_engine

from backend.app import db as _db
from backend.app import models as _models  # noqa: F401 (register tables)
from backend.app import voice as _voice  # noqa: F401 (NovelVoicePref table)
from backend.app.playback import (is_past_manifest_end, parse_range,
                                  playback_status, repair_direction,
                                  resolve_playback, sentence_index_at,
                                  should_prefetch)
from backend.app.progress import (apply_push, check_import_lock,
                                  ensure_utc, is_leader_write, parse_utc_iso,
                                  set_import_lock, should_accept_push,
                                  validate_push)


@pytest.fixture()
def session(tmp_path, monkeypatch):
    db_file = tmp_path / "t.db"
    eng = create_engine(f"sqlite:///{db_file}")
    monkeypatch.setattr(_db, "engine", eng)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


SENTS = [{"sent_id": 0, "t_start": 0.0, "t_end": 1.5, "text": "A."},
         {"sent_id": 1, "t_start": 1.5, "t_end": 3.0, "text": "B."},
         {"sent_id": 2, "t_start": 3.0, "t_end": 4.5, "text": "C."}]


# --- cold-seek: never set-on-empty-src; 202 + queue-seek -----------------------
def test_cold_synth_queues_seek():
    assert playback_status(None, False) == (202, "queue-seek")
    assert playback_status("synthesizing", False) == (202, "queue-seek")
    assert playback_status("ready", False) == (202, "queue-seek")
    assert playback_status("ready", True) == (200, "play")


def test_resolve_playback_cold_no_row(tmp_path):
    out = resolve_playback(1, 7, str(tmp_path), None)
    assert out["status"] == 202 and out["action"] == "queue-seek"
    assert out["audio_url"] is None


# --- <60s prefetch boundary ----------------------------------------------------
def test_short_chapter_always_prefetches():
    assert should_prefetch(30.0, 1.0) is True
    assert should_prefetch(59.9, 59.0) is True


def test_long_chapter_prefetch_near_end_only():
    assert should_prefetch(600.0, 100.0) is False
    assert should_prefetch(600.0, 550.0) is True


# --- sentence index + Opus-padding end ------------------------------------------
def test_sentence_index_upper_bound_tiebreak():
    assert sentence_index_at(SENTS, 0.0) == 0
    assert sentence_index_at(SENTS, 1.5) == 1  # boundary -> next sentence
    assert sentence_index_at(SENTS, 99.0) == 2  # clamp past-end
    assert sentence_index_at(SENTS, -1.0) == -1
    assert sentence_index_at([], 1.0) == -1


def test_opus_padding_end_still_advances():
    assert is_past_manifest_end(SENTS, 4.5) is True
    assert is_past_manifest_end(SENTS, 9.9) is True  # padding overshoot
    assert is_past_manifest_end(SENTS, 4.4) is False


# --- Range/206 ------------------------------------------------------------------
def test_parse_range_ok():
    assert parse_range("bytes=0-99", 1000) == (0, 99)
    assert parse_range("bytes=500-", 1000) == (500, 999)
    assert parse_range("bytes=-200", 1000) == (800, 999)
    assert parse_range("bytes=0-9999", 1000) == (0, 999)  # clamp


def test_parse_range_bad():
    for bad in (None, "", "items=0-1", "bytes=900-100", "bytes=5000-6000"):
        with pytest.raises(ValueError):
            parse_range(bad, 1000)


# --- orphan repair direction: manifest is truth ----------------------------------
def test_repair_direction():
    assert repair_direction(True, True) == "ok"
    assert repair_direction(True, False) == "drop-orphan-audio+resynth"
    assert repair_direction(False, True) == "resynth-missing-audio"
    assert repair_direction(False, False) == "cold-synth"


# --- LWW + 5s flap ---------------------------------------------------------------
def test_lww_newer_wins_older_loses():
    t0 = datetime.now(timezone.utc)
    assert should_accept_push(t0, t0 + timedelta(seconds=10), 100, 200)
    assert not should_accept_push(t0, t0 - timedelta(seconds=10), 200, 100)


def test_flap_guard_rejects_backward_write_inside_5s():
    t0 = datetime.now(timezone.utc)
    # stale background tab overwriting fresh foreground write -> reject
    assert not should_accept_push(t0, t0 + timedelta(seconds=2), 5000, 1000)
    # forward progress inside window -> accept
    assert should_accept_push(t0, t0 + timedelta(seconds=2), 1000, 5000)
    # backward write after window -> accept (genuine rewind/seek)
    assert should_accept_push(t0, t0 + timedelta(seconds=30), 5000, 1000)


def test_apply_push_flap_and_orphan(session):
    from backend.app.models import utcnow
    r1 = apply_push(session, 1, 0, 3, 9000, "hashA")
    assert r1["accepted"] and not r1["orphan"]
    # stale rewind within 5s -> rejected
    r2 = apply_push(session, 1, 0, 0, 1000, "hashA",
                    incoming_at=r1["row"].updated_at + timedelta(seconds=1))
    assert r2["accepted"] is False
    # rescraped text -> orphan flagged on accept
    r3 = apply_push(session, 1, 0, 0, 1000, "hashB",
                    incoming_at=utcnow() + timedelta(seconds=60))
    assert r3["accepted"] and r3["orphan"]


def test_validate_push_rejects_negative():
    with pytest.raises(ValueError):
        validate_push(0, -1, 100)


# --- UTC enforce + import lock + tab leader ---------------------------------------
def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))
    assert parse_utc_iso("2026-01-01T00:00:00Z").tzinfo is not None


def test_import_lock_blocks_writes(session):
    set_import_lock(True)
    try:
        with pytest.raises(HTTPException) as e:
            check_import_lock()
        assert e.value.status_code == 423
        with pytest.raises(HTTPException):
            apply_push(session, 9, 0, 0, 0)
    finally:
        set_import_lock(False)
    assert apply_push(session, 9, 0, 0, 0)["accepted"]


def test_tab_leader_hook():
    assert is_leader_write("a", "a") is True
    assert is_leader_write("b", "a") is False
    assert is_leader_write("solo", None) is True  # single-tab client


# --- voice: cast LWW, pron validate/preview, auto_synth default OFF ---------------
def test_cast_lww_per_char_key(session):
    from backend.app.voice import get_cast, put_cast
    from backend.app.models import utcnow
    put_cast(session, 1, {"narrator": "af_heart", "hero": "bf_emma"})
    t0 = utcnow()
    # stale write loses
    put_cast(session, 1, {"narrator": "stale_voice"},
             now=t0 - timedelta(seconds=60))
    assert get_cast(session, 1)["narrator"] == "af_heart"
    put_cast(session, 1, {"narrator": "new_voice"},
             now=t0 + timedelta(seconds=60))
    assert get_cast(session, 1)["narrator"] == "new_voice"


def test_merge_cast_pure_lww():
    from backend.app.voice import merge_cast
    t0 = datetime.now(timezone.utc)
    base = {"a": ("v1", t0)}
    out = merge_cast(base, {"a": ("v0", t0 - timedelta(seconds=5)),
                            "b": ("v2", t0 + timedelta(seconds=5))})
    assert out["a"] == ("v1", t0) and out["b"][0] == "v2"
    with pytest.raises(ValueError):
        merge_cast({}, {"x": ("v", datetime(2026, 1, 1))})  # naive


def test_pron_validate_and_preview():
    from backend.app.voice import preview_pron, validate_pron
    with pytest.raises(ValueError):
        validate_pron("", "x")
    hits = preview_pron("New York is big. New York!", [("New York", "NYC")])
    assert hits == {"New York": 2}


def test_auto_synthesize_default_off(session):
    from backend.app.voice import (get_auto_synthesize, set_auto_synthesize)
    assert get_auto_synthesize(session, 42) is False
    set_auto_synthesize(session, 42, True)
    assert get_auto_synthesize(session, 42) is True


def test_sample_preview_shape():
    from backend.app.voice import sample_preview
    out = sample_preview("Hello world.", "af_heart", 1.0)
    assert out["voice"] == "af_heart" and "render_key" in out


# --- routes wired ---------------------------------------------------------------
def test_router_paths_registered():
    from backend.app.routes_playback import router
    paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
    assert any(p[0] == "/api/v1/books/{book_id}/progress" for p in paths)
    assert any("playback" in p[0] for p in paths)
    assert any("voice-cast" in p[0] for p in paths)
