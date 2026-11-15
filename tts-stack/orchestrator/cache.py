"""Sentence audio cache: SQLite WAL metadata + sharded flat Opus files.

Key (canonical): sha1(norm_text | engine_id+image_tag | resolved_voice_id
| speed_quantized | dict_ver | pipe_ver). Anything that changes the bytes
changes the key; the old row ages out via LRU (never delete cascades).
Live-window sentences are pinned (never evict a key in ``pinned`` set).
ENOSPC or any write failure -> bypass path: return bytes uncached and
count it (cache must never take down a render).
"""
from __future__ import annotations

import errno
import hashlib
import os
import sqlite3
import uuid

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sentence_cache(
  key TEXT PRIMARY KEY, voice TEXT, speed TEXT, model TEXT,
  path TEXT, bytes INTEGER, dur_ms INTEGER,
  hits INTEGER DEFAULT 0, created_at REAL, last_access REAL);
CREATE TABLE IF NOT EXISTS cache_stats(total_bytes INTEGER);
INSERT INTO cache_stats(rowid, total_bytes)
  SELECT 1, 0 WHERE NOT EXISTS (SELECT 1 FROM cache_stats WHERE rowid=1);
"""

PIPE_VER = "pipe1"


def canonical_key(norm_text: str, engine_tag: str, voice: str, speed: float,
                  dict_ver: str) -> str:
    speed_q = f"{float(speed):.2f}"
    raw = "|".join([norm_text, engine_tag, voice, speed_q, dict_ver,
                     PIPE_VER])
    return hashlib.sha1(raw.encode()).hexdigest()


class Cache:
    def __init__(self, root: str, cap_bytes: int):
        self.root = root
        self.cap = cap_bytes
        os.makedirs(root, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(root, "cache.db"), timeout=5)
        self.db.executescript(SCHEMA)
        self.pinned: set[str] = set()

    def _path(self, key: str) -> str:
        rel = os.path.join("cache", key[:2], key[2:4], key[4:16] + ".opus")
        return os.path.join(self.root, rel)

    def lookup(self, key: str) -> str | None:
        import time
        row = self.db.execute(
            "SELECT path FROM sentence_cache WHERE key=?", (key,)).fetchone()
        if not row or not os.path.exists(row[0]):
            return None
        self.db.execute(
            "UPDATE sentence_cache SET hits=hits+1,last_access=? WHERE key=?",
            (time.time(), key))
        self.db.commit()
        return row[0]

    def store(self, key: str, opus: bytes, dur_ms: int, meta: dict) -> str:
        """Store bytes; on ENOSPC return a transient path uncached (bypass)."""
        import time
        dest = self._path(key)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + f".tmp.{uuid.uuid4().hex}"
            with open(tmp, "wb") as f:
                f.write(opus)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, dest)
            fd = os.open(os.path.dirname(dest), os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                self.db.execute(
                    "UPDATE cache_stats SET total_bytes=-1 WHERE rowid=1")
                self.db.commit()
                tmp = dest + f".bypass.{uuid.uuid4().hex}"
                with open(tmp, "wb") as f:
                    f.write(opus)
                return tmp
            raise
        now = time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO sentence_cache(key,voice,speed,model,"
            "path,bytes,dur_ms,hits,created_at,last_access)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (key, meta.get("voice", ""), meta.get("speed", ""),
             meta.get("model", ""), dest, len(opus), dur_ms, 0, now, now))
        self.db.execute("UPDATE cache_stats SET total_bytes="
                        "(SELECT COALESCE(SUM(bytes),0) FROM sentence_cache)"
                        " WHERE rowid=1")
        self.db.commit()
        self._enforce_cap()
        return dest

    def _enforce_cap(self) -> None:
        total = self.db.execute(
            "SELECT total_bytes FROM cache_stats WHERE rowid=1").fetchone()
        if not total or total[0] < 0 or total[0] <= self.cap:
            return
        rows = self.db.execute(
            "SELECT key,path,bytes FROM sentence_cache"
            " ORDER BY last_access ASC LIMIT 50").fetchall()
        for key, path, nbytes in rows:
            if key in self.pinned:
                continue
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            self.db.execute("DELETE FROM sentence_cache WHERE key=?", (key,))
            self.db.execute("UPDATE cache_stats SET total_bytes="
                            "total_bytes-? WHERE rowid=1", (nbytes,))
            total = (total[0] - nbytes,)
            if total[0] <= self.cap * 0.9:
                break
        self.db.commit()
