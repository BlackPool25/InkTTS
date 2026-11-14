"""Backup merge: field-level LWW applied in staging, never on the live DB.

Caller (backup.py) copies the live DB to a staging file, calls
merge_staging_into_live() against the backup DB, verifies, then atomically
promotes staging over live. Every timestamp is UTC; LWW compares
``updated_at`` with clock-skew tolerance handled as warn-only upstream.

Row granularity note: models carry one ``updated_at`` per row, so
"field-level" LWW reduces to row-level LWW (newer row wins wholesale),
except where natural sub-row keys exist:
  - voice_map merges per (novel, char_key)
  - pron_dict merges per pattern (record union)
  - novels join on fingerprint with URL-alias union + join-candidate report
  - progress clamps to chapter text_hash (orphan flag on resynth drift)

Aux tables (created on demand, NOT in models.py which Task 1 froze):
  _inkcast_meta(key, value): app_version, last_import_hash, prune_watermark
  _tombstones(table_name, key, deleted_at): GC delete propagation
  _rerender_flags(novel_fp, idx, reason): lazy re-render queue for old sidecars
  _progress_orphans(novel_fp, flagged_at): resynth-drift orphan flags

jobs and opds_tokens are never merged (ephemeral queue / per-device secret).
"""
import json
import sqlite3
from datetime import datetime, timezone

from .models import SIDECAR_SCHEMA_VER

MERGED_TABLES = ("novels", "chapters", "voices", "voice_map", "pron_dict", "progress")
SKIPPED_TABLES = ("jobs", "opds_tokens")  # ephemeral queue / per-device secret

AUX_SCHEMA = """
CREATE TABLE IF NOT EXISTS _inkcast_meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS _tombstones(table_name TEXT, key TEXT, deleted_at TEXT,
  PRIMARY KEY(table_name, key));
CREATE TABLE IF NOT EXISTS _rerender_flags(novel_fp TEXT, idx INTEGER, reason TEXT,
  PRIMARY KEY(novel_fp, idx));
CREATE TABLE IF NOT EXISTS _progress_orphans(novel_fp TEXT PRIMARY KEY, flagged_at TEXT);
"""


def fingerprint(title: str, author: str) -> str:
    """Novel identity key: normalized title+author (sha256, hex)."""
    import hashlib

    norm = " ".join((title or "").strip().lower().split())
    auth = " ".join((author or "").strip().lower().split())
    return hashlib.sha256(f"{norm}\x00{auth}".encode()).hexdigest()


def _parse_ts(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if not v:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(conn: sqlite3.Connection, table: str) -> list:
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    except sqlite3.OperationalError:
        return []  # table absent on one side (e.g. fresh DB)


def _aliases(row: dict) -> set:
    try:
        return set(json.loads(row.get("source_aliases") or "[]"))
    except (ValueError, TypeError):
        return set()


def ensure_aux(conn: sqlite3.Connection) -> None:
    conn.executescript(AUX_SCHEMA)
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    ensure_aux(conn)
    r = conn.execute("SELECT value FROM _inkcast_meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    ensure_aux(conn)
    conn.execute("INSERT OR REPLACE INTO _inkcast_meta(key, value) VALUES(?,?)", (key, value))
    conn.commit()


def add_tombstone(conn: sqlite3.Connection, table: str, key: str) -> None:
    """Record a GC delete so other devices drop the row on next merge."""
    ensure_aux(conn)
    conn.execute(
        "INSERT OR REPLACE INTO _tombstones(table_name, key, deleted_at) VALUES(?,?,?)",
        (table, key, _now_iso()),
    )
    conn.commit()


def gc_tombstones(conn: sqlite3.Connection, older_than_days: int = 30) -> int:
    """Drop tombstones older than the window; advance the prune watermark."""
    from datetime import timedelta

    ensure_aux(conn)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    cur = conn.execute("DELETE FROM _tombstones WHERE deleted_at < ?", (cutoff,))
    conn.execute(
        "INSERT OR REPLACE INTO _inkcast_meta(key, value) VALUES('prune_watermark',?)",
        (_now_iso(),),
    )
    conn.commit()
    return cur.rowcount


def _novel_key(row: dict) -> str:
    return row.get("fingerprint") or fingerprint(row.get("title", ""), row.get("author", ""))


def _apply_tombstones(target: sqlite3.Connection, backup: sqlite3.Connection) -> int:
    """Apply backup tombstones newer than target's watermark. Returns kills."""
    ensure_aux(target)
    wm = _parse_ts(get_meta(target, "prune_watermark"))
    kills = 0
    key_col = {"novels": "fingerprint", "chapters": None, "voices": "name",
               "voice_map": None, "pron_dict": "pattern", "progress": None}
    for t in _rows(backup, "_tombstones"):
        if _parse_ts(t["deleted_at"]) <= wm:
            continue
        table, key = t["table_name"], t["key"]
        if table not in MERGED_TABLES:
            continue
        col = key_col[table]
        if col:  # simple natural key
            if table == "novels" and not key:
                continue
            target.execute(f"DELETE FROM {table} WHERE {col}=?", (key,))
            kills += target.total_changes and 1 or 0
        elif table == "chapters":  # key = "fp:idx"
            try:
                fp, idx = key.rsplit(":", 1)
                nid = target.execute("SELECT id FROM novels WHERE fingerprint=?", (fp,)).fetchone()
                if nid:
                    target.execute("DELETE FROM chapters WHERE novel_id=? AND idx=?", (nid[0], int(idx)))
            except (ValueError, TypeError):
                pass
        elif table == "voice_map":  # key = "fp:char_key"
            try:
                fp, char = key.split(":", 1)
                nid = target.execute("SELECT id FROM novels WHERE fingerprint=?", (fp,)).fetchone()
                if nid:
                    target.execute("DELETE FROM voice_map WHERE novel_id=? AND char_key=?", (nid[0], char))
            except (ValueError, TypeError):
                pass
        elif table == "progress":  # key = fp
            nid = target.execute("SELECT id FROM novels WHERE fingerprint=?", (key,)).fetchone()
            if nid:
                target.execute("DELETE FROM progress WHERE novel_id=?", (nid[0],))
        # union tombstone sets (keep newest)
        cur = target.execute("SELECT deleted_at FROM _tombstones WHERE table_name=? AND key=?",
                             (table, key)).fetchone()
        if not cur or _parse_ts(t["deleted_at"]) > _parse_ts(cur[0]):
            target.execute(
                "INSERT OR REPLACE INTO _tombstones(table_name, key, deleted_at) VALUES(?,?,?)",
                (table, key, t["deleted_at"]))
    target.commit()
    return kills


def merge_staging_into_live(target_path: str, backup_db_path: str,
                            backup_sidecar_ver: int = SIDECAR_SCHEMA_VER) -> dict:
    """Merge backup DB into target (a staging copy of live). Idempotent.

    Returns a report dict: per-table won/kept counts, joins needing UI,
    orphans flagged, rerender flags queued, tombstone kills.
    """
    target = sqlite3.connect(target_path)
    backup = sqlite3.connect(backup_db_path)
    try:
        ensure_aux(target)
        report: dict = {"won": {}, "kept": {}, "joins": [], "orphans": [],
                        "rerender": 0, "tombstone_kills": 0}
        report["tombstone_kills"] = _apply_tombstones(target, backup)

        # --- novels: fingerprint identity + URL-alias join + alias union ---
        live_by_fp = {_novel_key(r): r for r in _rows(target, "novels")}
        live_urls: dict[str, str] = {}  # url -> fp
        for fp, r in live_by_fp.items():
            live_urls[r.get("source_url") or ""] = fp
            for a in _aliases(r):
                live_urls[a] = fp
        fp_to_id: dict[str, int] = {fp: r["id"] for fp, r in live_by_fp.items()}

        for b in _rows(backup, "novels"):
            fp = _novel_key(b)
            urls = {b.get("source_url") or ""} | _aliases(b)
            urls.discard("")
            if fp not in live_by_fp:
                hit = next((live_urls[u] for u in urls if u in live_urls), None)
                if hit:  # same novel under new fingerprint -> join onto live id
                    report["joins"].append({"backup_fp": fp, "live_fp": hit,
                                           "urls": sorted(urls)})
                    fp = hit
            if fp in live_by_fp:
                cur = dict(live_by_fp[fp])
                merged_aliases = sorted(_aliases(cur) | _aliases(b))
                winner_newer = _parse_ts(b.get("updated_at")) > _parse_ts(cur.get("updated_at"))
                w = b if winner_newer else cur
                # fingerprint is a pure function of title+author: it follows the
                # winning row so joined novels converge to one identity.
                # created_at takes the min (commutative -> converges both ways).
                new_fp = _novel_key(w)
                new_created = (b.get("created_at") if _parse_ts(b.get("created_at")) <
                               _parse_ts(cur.get("created_at")) else cur.get("created_at"))
                if winner_newer:
                    target.execute(
                        """UPDATE novels SET title=?, author=?, fingerprint=?, source_url=?,
                           cover_path=?, source_aliases=?, created_at=?, updated_at=? WHERE id=?""",
                        (b["title"], b.get("author", ""), new_fp, b.get("source_url", ""),
                         b.get("cover_path", ""), json.dumps(merged_aliases),
                         new_created, b.get("updated_at"), cur["id"]))
                    report["won"]["novels"] = report["won"].get("novels", 0) + 1
                    live_by_fp[fp] = {**cur, **b, "fingerprint": new_fp,
                                      "created_at": new_created,
                                      "source_aliases": json.dumps(merged_aliases)}
                else:
                    target.execute(
                        "UPDATE novels SET fingerprint=?, source_aliases=?, created_at=? WHERE id=?",
                        (new_fp, json.dumps(merged_aliases), new_created, cur["id"]))
                    report["kept"]["novels"] = report["kept"].get("novels", 0) + 1
                    live_by_fp[fp] = {**cur, "fingerprint": new_fp, "created_at": new_created,
                                      "source_aliases": json.dumps(merged_aliases)}
                fp_to_id[new_fp] = cur["id"]
            else:
                if not b.get("fingerprint"):
                    b["fingerprint"] = fp
                cur = target.execute(
                    """INSERT INTO novels(title, author, fingerprint, source_url,
                       source_aliases, cover_path, created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (b["title"], b.get("author", ""), fp, b.get("source_url", ""),
                     b.get("source_aliases") or "[]", b.get("cover_path", ""),
                     b.get("created_at"), b.get("updated_at")))
                fp_to_id[fp] = cur.lastrowid
                live_by_fp[fp] = {**b, "id": cur.lastrowid}
                report["won"]["novels"] = report["won"].get("novels", 0) + 1
        target.commit()

        def novel_id(backup_novel_id: int) -> int | None:
            b = next((r for r in _rows(backup, "novels") if r["id"] == backup_novel_id), None)
            if not b:
                return None
            return fp_to_id.get(_novel_key(b))

        # --- chapters: (novel, idx) LWW; old sidecar schema -> lazy re-render ---
        for b in _rows(backup, "chapters"):
            nid = novel_id(b["novel_id"])
            if nid is None:
                continue
            cur = target.execute("SELECT * FROM chapters WHERE novel_id=? AND idx=?",
                                 (nid, b["idx"])).fetchone()
            cur = dict(cur) if cur else None
            cols = ("title", "text_hash", "render_key", "voice", "speed",
                    "audio_path", "sidecar_path", "status", "updated_at")
            if cur is None:
                target.execute(
                    """INSERT INTO chapters(novel_id, idx, title, text_hash, render_key,
                       voice, speed, audio_path, sidecar_path, status, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (nid, b["idx"]) + tuple(b.get(c) for c in cols))
                report["won"]["chapters"] = report["won"].get("chapters", 0) + 1
            elif _parse_ts(b.get("updated_at")) > _parse_ts(cur.get("updated_at")):
                target.execute(
                    """UPDATE chapters SET title=?, text_hash=?, render_key=?, voice=?,
                       speed=?, audio_path=?, sidecar_path=?, status=?, updated_at=?
                       WHERE id=?""",
                    tuple(b.get(c) for c in cols) + (cur["id"],))
                report["won"]["chapters"] = report["won"].get("chapters", 0) + 1
            else:
                report["kept"]["chapters"] = report["kept"].get("chapters", 0) + 1
            if backup_sidecar_ver < SIDECAR_SCHEMA_VER:
                fp = target.execute("SELECT fingerprint FROM novels WHERE id=?", (nid,)).fetchone()
                target.execute(
                    "INSERT OR IGNORE INTO _rerender_flags(novel_fp, idx, reason) VALUES(?,?,?)",
                    (fp[0] if fp else "", b["idx"], f"sidecar-v{backup_sidecar_ver}-lazy"))
                report["rerender"] += 1
        target.commit()

        # --- voices: name LWW ---
        for b in _rows(backup, "voices"):
            cur = target.execute("SELECT * FROM voices WHERE name=?", (b["name"],)).fetchone()
            cur = dict(cur) if cur else None
            if cur is None:
                target.execute("INSERT INTO voices(name, lang, is_default, updated_at) VALUES(?,?,?,?)",
                               (b["name"], b.get("lang", "en"), b.get("is_default", 0), b.get("updated_at")))
                report["won"]["voices"] = report["won"].get("voices", 0) + 1
            elif _parse_ts(b.get("updated_at")) > _parse_ts(cur.get("updated_at")):
                target.execute("UPDATE voices SET lang=?, is_default=?, updated_at=? WHERE id=?",
                               (b.get("lang", "en"), b.get("is_default", 0), b.get("updated_at"), cur["id"]))
                report["won"]["voices"] = report["won"].get("voices", 0) + 1
            else:
                report["kept"]["voices"] = report["kept"].get("voices", 0) + 1
        target.commit()

        # --- voice_map: per (novel, char_key) LWW ---
        for b in _rows(backup, "voice_map"):
            nid = novel_id(b["novel_id"])
            if nid is None:
                continue
            cur = target.execute("SELECT * FROM voice_map WHERE novel_id=? AND char_key=?",
                                 (nid, b["char_key"])).fetchone()
            cur = dict(cur) if cur else None
            if cur is None:
                target.execute("INSERT INTO voice_map(novel_id, char_key, voice, updated_at) VALUES(?,?,?,?)",
                               (nid, b["char_key"], b["voice"], b.get("updated_at")))
                report["won"]["voice_map"] = report["won"].get("voice_map", 0) + 1
            elif _parse_ts(b.get("updated_at")) > _parse_ts(cur.get("updated_at")):
                target.execute("UPDATE voice_map SET voice=?, updated_at=? WHERE id=?",
                               (b["voice"], b.get("updated_at"), cur["id"]))
                report["won"]["voice_map"] = report["won"].get("voice_map", 0) + 1
            else:
                report["kept"]["voice_map"] = report["kept"].get("voice_map", 0) + 1
        target.commit()

        # --- pron_dict: record-level union, per-pattern LWW ---
        for b in _rows(backup, "pron_dict"):
            cur = target.execute("SELECT * FROM pron_dict WHERE pattern=?", (b["pattern"],)).fetchone()
            cur = dict(cur) if cur else None
            if cur is None:
                target.execute("INSERT INTO pron_dict(pattern, replacement, updated_at) VALUES(?,?,?)",
                               (b["pattern"], b["replacement"], b.get("updated_at")))
                report["won"]["pron_dict"] = report["won"].get("pron_dict", 0) + 1
            elif _parse_ts(b.get("updated_at")) > _parse_ts(cur.get("updated_at")):
                target.execute("UPDATE pron_dict SET replacement=?, updated_at=? WHERE id=?",
                               (b["replacement"], b.get("updated_at"), cur["id"]))
                report["won"]["pron_dict"] = report["won"].get("pron_dict", 0) + 1
            else:
                report["kept"]["pron_dict"] = report["kept"].get("pron_dict", 0) + 1
        target.commit()

        # --- progress: LWW then clamp on text_hash drift + orphan flag ---
        for b in _rows(backup, "progress"):
            nid = novel_id(b["novel_id"])
            if nid is None:
                continue
            cur = target.execute("SELECT * FROM progress WHERE novel_id=?", (nid,)).fetchone()
            cur = dict(cur) if cur else None
            if cur is None:
                target.execute(
                    """INSERT INTO progress(novel_id, chapter_idx, sent_id, position_ms,
                       text_hash, updated_at) VALUES(?,?,?,?,?,?)""",
                    (nid, b.get("chapter_idx", 0), b.get("sent_id", 0),
                     b.get("position_ms", 0), b.get("text_hash", ""), b.get("updated_at")))
                report["won"]["progress"] = report["won"].get("progress", 0) + 1
            elif _parse_ts(b.get("updated_at")) > _parse_ts(cur.get("updated_at")):
                target.execute(
                    """UPDATE progress SET chapter_idx=?, sent_id=?, position_ms=?,
                       text_hash=?, updated_at=? WHERE id=?""",
                    (b.get("chapter_idx", 0), b.get("sent_id", 0), b.get("position_ms", 0),
                     b.get("text_hash", ""), b.get("updated_at"), cur["id"]))
                report["won"]["progress"] = report["won"].get("progress", 0) + 1
            else:
                report["kept"]["progress"] = report["kept"].get("progress", 0) + 1
            # clamp on the EFFECTIVE row: whichever side won, a text_hash
            # mismatch against the merged chapter means resynced-under-us drift
            final = target.execute("SELECT * FROM progress WHERE novel_id=?", (nid,)).fetchone()
            final = dict(final) if final else None
            if final:
                ch = target.execute(
                    "SELECT text_hash FROM chapters WHERE novel_id=? AND idx=?",
                    (nid, final.get("chapter_idx", 0))).fetchone()
                if ch and ch[0] and final.get("text_hash") and ch[0] != final["text_hash"]:
                    # text rescraped under us: clamp to chapter start, flag orphan
                    target.execute("UPDATE progress SET sent_id=0, position_ms=0 WHERE novel_id=?", (nid,))
                    fp = target.execute("SELECT fingerprint FROM novels WHERE id=?", (nid,)).fetchone()
                    target.execute(
                        "INSERT OR REPLACE INTO _progress_orphans(novel_fp, flagged_at) VALUES(?,?)",
                        (fp[0] if fp else "", _now_iso()))
                    report["orphans"].append({"novel_id": nid, "chapter_idx": final.get("chapter_idx", 0)})
        target.commit()

        # advance prune watermark to newest tombstone seen
        r = backup.execute("SELECT MAX(deleted_at) FROM _tombstones").fetchone()
        if r and r[0]:
            cur_wm = get_meta(target, "prune_watermark")
            if not cur_wm or _parse_ts(r[0]) > _parse_ts(cur_wm):
                set_meta(target, "prune_watermark", r[0])
        return report
    finally:
        target.close()
        backup.close()
