"""Sentence audio cache: SQLite WAL metadata + sharded flat Opus files.

Key (canonical): sha1(norm_text | engine_id+image_tag | resolved_voice_id
| speed_quantized | dict_ver | pipe_ver). Anything that changes the bytes
changes the key; the old row ages out via LRU (never delete cascades).
Live-window sentences are pinned (never evict a key in ``pinned`` set).
ENOSPC or any write failure -> bypass path: return transient bytes uncached
and count it (cache must never take down a render).

Thread-safe: worker thread + API thread share one connection under a lock.
"""
from __future__ import annotations

import errno
import hashlib
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from collections.abc import Iterator

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

PIPE_VER = "pipe2"  # bumped: model joined the key (was missing -> cross-model false hits)


def canonical_key(norm_text: str, engine_tag: str, voice: str, speed: float,
                  dict_ver: str, model: str = "") -> str:
    speed_q = f"{float(speed):.2f}"
    raw = "|".join([norm_text, engine_tag, voice, speed_q, dict_ver,
                     PIPE_VER, model])
    return hashlib.sha1(raw.encode()).hexdigest()


class Cache:
    def __init__(self, root: str, cap_bytes: int):
        self.root = root
        self.cap = cap_bytes
        self._lock = threading.Lock()
        os.makedirs(root, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(root, "cache.db"), timeout=30,
                                 check_same_thread=False)
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.commit()
        self.pinned: set[str] = set()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self.db
            self.db.commit()

    def _path(self, key: str) -> str:
        rel = os.path.join("cache", key[:2], key[2:4], key[4:16] + ".opus")
        return os.path.join(self.root, rel)

    def lookup(self, key: str) -> str | None:
        with self._tx() as db:
            row = db.execute(
                "SELECT path FROM sentence_cache WHERE key=?",
                (key,)).fetchone()
            if row:
                db.execute(
                    "UPDATE sentence_cache SET hits=hits+1,last_access=? WHERE key=?",
                    (time.time(), key))
        if not row or not os.path.exists(row[0]):
            return None
        return row[0]

    def store(self, key: str, opus: bytes, dur_ms: int, meta: dict) -> str:
        """Store bytes; on ENOSPC return a transient path uncached (bypass)."""
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
                with self._tx() as db:
                    db.execute("UPDATE cache_stats SET total_bytes=-1 WHERE rowid=1")
                tmp = dest + f".bypass.{uuid.uuid4().hex}"
                with open(tmp, "wb") as f:
                    f.write(opus)
                return tmp
            raise
        now = time.time()
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO sentence_cache(key,voice,speed,model,"
                "path,bytes,dur_ms,hits,created_at,last_access)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, meta.get("voice", ""), meta.get("speed", ""),
                 meta.get("model", ""), dest, len(opus), dur_ms, 0, now, now))
            db.execute("UPDATE cache_stats SET total_bytes="
                       "(SELECT COALESCE(SUM(bytes),0) FROM sentence_cache)"
                       " WHERE rowid=1")
        self._enforce_cap()
        return dest

    def _enforce_cap(self) -> None:
        with self._tx() as db:
            total = db.execute(
                "SELECT total_bytes FROM cache_stats WHERE rowid=1").fetchone()
            if not total or total[0] < 0 or total[0] <= self.cap:
                return
            rows = db.execute(
                "SELECT key,path,bytes FROM sentence_cache"
                " ORDER BY last_access ASC LIMIT 50").fetchall()
            for key, path, nbytes in rows:
                if key in self.pinned:
                    continue
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                db.execute("DELETE FROM sentence_cache WHERE key=?", (key,))
                db.execute("UPDATE cache_stats SET total_bytes="
                           "total_bytes-? WHERE rowid=1", (nbytes,))
                total = (total[0] - nbytes,)
                if total[0] <= self.cap * 0.9:
                    break
