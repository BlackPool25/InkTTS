"""Backup-sync tests: round-trip converge, double-import no-op, full-disk
keeps last good, older-version refuse."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from backend.app import backup as B
from backend.app import merge as M
from backend.app.models import Chapter, Novel, Progress, PronEntry, Voice, VoiceMap

PASS = "correct horse battery staple"


def _engine(path):
    eng = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(eng)
    return eng


def _seed(path, data_dir, tag, progress_ms, voice_for_narrator, extra_pron=None,
          novel_url="https://ex.com/n1", aliases=(), chapter_hash="aaa",
          title="The Wandering Inn"):
    """One novel + chapter + voice-map + pron + progress + cover + sidecar."""
    eng = _engine(path)
    with Session(eng) as s:
        n = Novel(title=title, author="pirateaba",
                  fingerprint=M.fingerprint(title, "pirateaba"),
                  source_url=novel_url, source_aliases=json.dumps(list(aliases)),
                  cover_path="covers/1/cover.jpg")
        s.add(n)
        s.commit()
        s.refresh(n)
        s.add(Chapter(novel_id=n.id, idx=1, title="Ch 1", text_hash=chapter_hash,
                      render_key="af_heart@1.0x:kokoro-82M-onnx-v1",
                      sidecar_path="audio/1/0/1.deadbeef.json"))
        s.add(Voice(name="af_heart", lang="en", is_default=True))
        s.add(VoiceMap(novel_id=n.id, char_key="narrator", voice=voice_for_narrator))
        s.add(PronEntry(pattern="Ryu", replacement="ree-YOO"))
        if extra_pron:
            s.add(PronEntry(pattern=extra_pron[0], replacement=extra_pron[1]))
        s.add(Progress(novel_id=n.id, chapter_idx=1, sent_id=3,
                       position_ms=progress_ms, text_hash=chapter_hash))
        s.commit()
    eng.dispose()
    # sidecar + cover files on disk
    sc = os.path.join(data_dir, "audio/1/0/1.deadbeef.json")
    os.makedirs(os.path.dirname(sc), exist_ok=True)
    with open(sc, "w") as f:
        json.dump({"schema_ver": 2, "tag": tag}, f)
    cv = os.path.join(data_dir, "covers/1/cover.jpg")
    os.makedirs(os.path.dirname(cv), exist_ok=True)
    with open(cv, "w") as f:
        f.write(f"cover-{tag}")
    return path


def _dump(path):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = {}
    for t in ("novels", "chapters", "voices", "voice_map", "pron_dict", "progress"):
        try:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
        except Exception:
            rows = []
        for r in rows:
            r.pop("id", None)
            # novel ids differ across devices: normalize via fingerprint
            if t in ("chapters", "voice_map", "progress"):
                r.pop("novel_id", None)
        out[t] = rows
    conn.close()
    return out


def test_roundtrip_converge(tmp_path):
    da, db = str(tmp_path / "da"), str(tmp_path / "db")
    os.makedirs(da)
    os.makedirs(db)
    pa, pb = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    _seed(pa, da, "A", 1000, "af_heart")
    # device B: same novel under a different fingerprint (retitled mirror),
    # joinable via URL alias; newer progress, changed cast, extra pron
    _seed(pb, db, "B", 9000, "bf_emma", extra_pron=("Erin", "EH-rin"),
          novel_url="https://mirror.com/n1", aliases=["https://ex.com/n1"],
          title="The Wandering Inn [mirror]")

    za = str(tmp_path / "a.bak")
    B.export_backup(pa, da, za, passphrase=PASS)
    rep = B.import_backup(za, pb, db, passphrase=PASS)
    assert rep["noop"] is False
    assert rep["joins"]  # alias join recorded

    with Session(_engine(pb)) as s:
        import select as _s  # noqa
        from sqlmodel import select
        novels = s.exec(select(Novel)).all()
        assert len(novels) == 1  # joined, not duplicated
        assert novels[0].source_url == "https://mirror.com/n1"  # newer kept
        assert json.loads(novels[0].source_aliases) == ["https://ex.com/n1"]  # union
        prog = s.exec(select(Progress)).all()
        assert prog[0].position_ms == 9000  # newer LWW kept
        vm = s.exec(select(VoiceMap)).all()
        assert vm[0].voice == "bf_emma"  # per-char-key LWW kept newer
        prons = {p.pattern for p in s.exec(select(PronEntry)).all()}
        assert {"Ryu", "Erin"} <= prons  # record union

    # converge the other way: B -> A must reach the same state
    zb = str(tmp_path / "b.bak")
    B.export_backup(pb, db, zb, passphrase=PASS)
    B.import_backup(zb, pa, da, passphrase=PASS)
    assert _dump(pa) == _dump(pb)


def test_double_import_noop(tmp_path):
    dd = str(tmp_path / "d")
    os.makedirs(dd)
    p = str(tmp_path / "c.db")
    _seed(p, dd, "C", 1000, "af_heart")
    z = str(tmp_path / "c.bak")
    B.export_backup(p, dd, z, passphrase=PASS)
    first = B.import_backup(z, p, dd, passphrase=PASS)
    assert first["noop"] is False
    before = _dump(p)
    second = B.import_backup(z, p, dd, passphrase=PASS)
    assert second["noop"] is True
    assert _dump(p) == before


def test_full_disk_keeps_last_good(tmp_path, monkeypatch):
    dd = str(tmp_path / "d")
    os.makedirs(dd)
    p = str(tmp_path / "e.db")
    _seed(p, dd, "E", 1000, "af_heart")
    z = str(tmp_path / "e.bak")
    B.export_backup(p, dd, z, passphrase=PASS)
    with open(z, "rb") as f:
        good = f.read()

    class Tiny:
        free = 1
    monkeypatch.setattr(B.shutil, "disk_usage", lambda *_a, **_k: Tiny())
    with pytest.raises(B.DiskFullError):
        B.export_backup(p, dd, z, passphrase=PASS)
    with open(z, "rb") as f:
        assert f.read() == good  # last good intact


def test_older_version_refuse(tmp_path):
    dd = str(tmp_path / "d")
    os.makedirs(dd)
    p = str(tmp_path / "f.db")
    _seed(p, dd, "F", 1000, "af_heart")
    z = str(tmp_path / "f.bak")
    B.export_backup(p, dd, z, passphrase=PASS, app_version="99.0.0")
    before = _dump(p)
    with pytest.raises(B.OlderAppError):
        B.import_backup(z, p, dd, passphrase=PASS)
    assert _dump(p) == before  # live untouched


def test_no_audio_in_zip_and_wrong_passphrase(tmp_path):
    dd = str(tmp_path / "d")
    os.makedirs(dd)
    p = str(tmp_path / "g.db")
    _seed(p, dd, "G", 1000, "af_heart")
    opus = os.path.join(dd, "audio/1/0/1.deadbeef.opus")
    with open(opus, "wb") as f:
        f.write(b"fake-opus")
    z = str(tmp_path / "g.bak")
    B.export_backup(p, dd, z, passphrase=PASS)
    import zipfile

    raw = B.decrypt_bytes(open(z, "rb").read(), PASS)
    with zipfile.ZipFile(__import__("io").BytesIO(raw)) as zh:
        assert not any(n.endswith(".opus") for n in zh.namelist())
    with pytest.raises(B.PassphraseError):
        B.import_backup(z, p, dd, passphrase="wrong")


def test_progress_orphan_on_rescrape(tmp_path):
    dd = str(tmp_path / "d")
    os.makedirs(dd)
    pa, pb = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    _seed(pa, dd, "A", 5000, "af_heart", chapter_hash="aaa")
    _seed(pb, dd, "B", 100, "af_heart", chapter_hash="bbb")  # rescraped text
    # make B's chapter+progress newer so they win, then import A(old hash,newer ts)? No:
    # simplest: bump B progress updated_at past A by re-seeding order is same-time;
    # force: import B into A where A progress is newer but chapter text differs.
    import sqlite3
    from datetime import datetime, timezone, timedelta
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    conn = sqlite3.connect(pb)
    conn.execute("UPDATE chapters SET updated_at=?", (future,))  # rescrape wins
    conn.commit()
    conn.close()
    conn = sqlite3.connect(pa)
    conn.execute("UPDATE progress SET updated_at=?", (later,))  # stale-hash progress wins
    conn.commit()
    conn.close()
    z = str(tmp_path / "b.bak")
    B.export_backup(pb, dd, z, passphrase=PASS)
    rep = B.import_backup(z, pa, dd, passphrase=PASS)
    assert rep["orphans"]  # drift detected
    with Session(_engine(pa)) as s:
        from sqlmodel import select
        prog = s.exec(select(Progress)).all()
        assert prog[0].position_ms == 0  # clamped to chapter start
