"""Progress: sentence-granularity position with server-time LWW + flap guard.

Client contract (mirrors frontend tabs.ts / player.ts):
- Push ~2s during play + on pause/background; snapshot on `pagehide` +
  `visibilitychange` (mobile `beforeunload` never fires).
- Pull on focus / playback start (GET wins over memory).
- Single writer: only the tab holding the leader lock PUTs; second tab is
  read-only for position (still plays audio locally).
- Import (backup restore) locks position writes: PUT while locked -> 423.
- UTC everywhere: naive datetimes are rejected, never guessed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

PUSH_INTERVAL_S = 2
PULL_ON = ("focus", "playback-start")
FLAP_WINDOW_S = 5
IMPORT_LOCK_STATUS = 423

_import_locked = False


def set_import_lock(locked: bool) -> None:
    global _import_locked
    _import_locked = locked


def is_import_locked() -> bool:
    return _import_locked


def check_import_lock() -> None:
    if _import_locked:
        raise HTTPException(status_code=IMPORT_LOCK_STATUS,
                            detail="backup import in progress; position writes locked")


def ensure_utc(dt: datetime) -> datetime:
    """Reject naive datetimes; normalise aware ones to UTC."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime rejected: UTC required")
    return dt.astimezone(timezone.utc)


def parse_utc_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return ensure_utc(dt)


def validate_push(chapter_idx: int, sent_id: int, position_ms: int) -> None:
    if chapter_idx < 0 or sent_id < 0 or position_ms < 0:
        raise ValueError("chapter_idx/sent_id/position_ms must be >= 0")


def is_leader_write(tab_id: str | None, leader_id: str | None) -> bool:
    """Tab-leader check hook. Single-tab clients pass leader_id=None (=self)."""
    if leader_id is None:
        return True
    return tab_id == leader_id


def should_accept_push(stored_at: datetime, incoming_at: datetime,
                       stored_pos_ms: int, incoming_pos_ms: int) -> bool:
    """Server-time LWW + 5s flap guard.

    - incoming older than stored -> reject (LWW).
    - incoming within 5s of stored AND moving backward -> reject (stale-tab
      flap: a background tab overwriting a fresh foreground write).
    - otherwise accept.
    """
    stored_at, incoming_at = ensure_utc(stored_at), ensure_utc(incoming_at)
    if incoming_at < stored_at:
        return False
    if incoming_at == stored_at:
        return incoming_pos_ms >= stored_pos_ms
    gap = (incoming_at - stored_at).total_seconds()
    if 0 <= gap < FLAP_WINDOW_S and incoming_pos_ms < stored_pos_ms:
        return False
    return True


def apply_push(session, novel_id: int, chapter_idx: int, sent_id: int,
               position_ms: int, text_hash: str = "",
               incoming_at: datetime | None = None) -> dict:
    """LWW apply against the Progress row. Returns {accepted, orphan, row}."""
    from .models import Progress, utcnow

    validate_push(chapter_idx, sent_id, position_ms)
    check_import_lock()
    now = ensure_utc(incoming_at) if incoming_at else utcnow()
    row = session.exec(
        __import__("sqlmodel", fromlist=["select"]).select(Progress).where(
            Progress.novel_id == novel_id)).first()
    orphan = False
    if row is None:
        from .models import Progress as _P
        row = _P(novel_id=novel_id, chapter_idx=chapter_idx,
                 sent_id=sent_id, position_ms=position_ms,
                 text_hash=text_hash, updated_at=now)
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"accepted": True, "orphan": False, "row": row}
    if row.text_hash and text_hash and row.text_hash != text_hash:
        orphan = True  # rescraped text: clamp + flag, never silently remap
    if not should_accept_push(row.updated_at, now,
                               row.position_ms, position_ms):
        return {"accepted": False, "orphan": orphan, "row": row}
    row.chapter_idx = chapter_idx
    row.sent_id = sent_id
    row.position_ms = position_ms
    if text_hash:
        row.text_hash = text_hash
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"accepted": True, "orphan": orphan, "row": row}
