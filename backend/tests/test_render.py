"""Render pipeline tests: gapless, hash-equal skip, full re-synth, pinning."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from sqlmodel import Session, SQLModel, select

from backend.app import db as _db
from backend.app.models import Chapter
from backend.app.render import render_chapter_text, split_sentences


class FakeEngine:
    """Deterministic stand-in: 0.2s 24kHz int16 PCM per sentence."""

    def synthesize(self, sentence):
        n = int(24000 * 0.2)
        return b"\x00\x10" * n, 24000


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    import backend.app.gate as G
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    from sqlalchemy import create_engine
    eng = create_engine(f"sqlite:///{db_file}")
    monkeypatch.setattr(_db, "engine", eng)
    monkeypatch.setattr(G, "record_failure", lambda *a, **k: None)
    monkeypatch.setattr(G, "quarantine_artifact",
                        lambda *a, **k: os.path.join(str(tmp_path), "q.txt"))
    SQLModel.metadata.create_all(eng)
    data = tmp_path / "data"
    data.mkdir()
    return eng, str(data)


LONG = ("Hello world. This is a test chapter with several sentences! "
        "Dr. Smith stayed home. Is it real? Yes, it is. " * 4)


def test_gapless_assert(isolated):
    _, data = isolated
    rc = render_chapter_text(1, 1, LONG, "af_heart", 1.0, data,
                             engine=FakeEngine())
    import json
    with open(os.path.join(data, rc.sidecar_path), encoding="utf-8") as f:
        m = json.load(f)
    assert m["schema_ver"] == 2
    assert [s["sent_id"] for s in m["sentences"]] == list(range(len(m["sentences"])))
    for a, b in zip(m["sentences"], m["sentences"][1:]):
        assert b["t_start"] == a["t_end"]
    assert m["render_key"].startswith("af_heart@")
    assert os.path.exists(os.path.join(data, rc.audio_path))


def test_hash_equal_no_resynth(isolated):
    _, data = isolated
    a = render_chapter_text(1, 2, LONG, "af_heart", 1.0, data,
                             engine=FakeEngine())
    b = render_chapter_text(1, 2, LONG, "af_heart", 1.0, data,
                             engine=FakeEngine())
    assert b.resynthed is False
    assert (a.audio_path, a.sidecar_path) == (b.audio_path, b.sidecar_path)


def test_changed_text_full_resynth(isolated):
    _, data = isolated
    a = render_chapter_text(1, 3, LONG, "af_heart", 1.0, data,
                             engine=FakeEngine())
    b = render_chapter_text(1, 3, LONG + " One more sentence here.",
                             "af_heart", 1.0, data, engine=FakeEngine())
    assert b.resynthed is True
    assert b.audio_path != a.audio_path  # new hash URL


def test_voice_change_pinning(isolated):
    eng, data = isolated
    a = render_chapter_text(1, 4, LONG, "af_heart", 1.0, data,
                             engine=FakeEngine())
    b = render_chapter_text(1, 4, LONG, "bf_emma", 1.0, data,
                             engine=FakeEngine())
    assert b.resynthed is False  # old chapter untouched by voice change
    assert b.render_key == a.render_key
    with Session(eng) as s:
        row = s.exec(select(Chapter).where(
            Chapter.novel_id == 1, Chapter.idx == 4)).first()
        assert row.render_key == a.render_key


def test_gate_rejects_challenge_no_chapter_row(isolated):
    eng, data = isolated
    with pytest.raises(ValueError):
        render_chapter_text(1, 5, "Cloudflare: verify you are human. " * 10,
                             "af_heart", 1.0, data, engine=FakeEngine())
    with Session(eng) as s:
        rows = s.exec(select(Chapter).where(
            Chapter.novel_id == 1, Chapter.idx == 5)).all()
        assert rows == []


def test_pron_longest_first_and_caps():
    from backend.app.pron import (apply, fold_allcaps, load_rules,
                                  preview_hit_count)
    rules = sorted([
        __import__("backend.app.pron", fromlist=["PronRule"]).PronRule(
            p, r, __import__("re").compile(r"\b" + __import__("re").escape(p) + r"\b"))
        for p, r in [("New York", "NYC"), ("York", "YORK-SHORT")]],
        key=lambda x: len(x.pattern), reverse=True)
    assert apply("I love New York", rules) == "I love NYC"
    assert preview_hit_count("New York York", rules) == {"New York": 1, "York": 2}
    assert fold_allcaps("THE DRAGON and RPG party") == "The Dragon and RPG party"
