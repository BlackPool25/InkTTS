"""Voice suite: per-novel cast map, pron-dict editor, sample preview.

- Cast map: per-character-key LWW (backup merge unions on char_key too).
- Pron-dict: literal `\\b`-bound longest-first rules (see pron.py); editor
  validates + previews hit counts before save.
- Sample preview: returns the normalised text + render_key that WOULD be
  synthesised (no audio bytes here; synth stays in render.py).
- Per-novel auto_synthesize flag, default OFF (explicit button only, so a
  setting change never storms the library).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

from .models import render_key_for


class NovelVoicePref(SQLModel, table=True):
    """Own table (models.py frozen by Task 1): per-novel voice prefs."""
    __tablename__ = "novel_voice_pref"
    novel_id: int = Field(primary_key=True)
    auto_synthesize: bool = Field(default=False)
    default_voice: str = Field(default="")
    default_speed: float = Field(default=1.0)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


def get_auto_synthesize(session, novel_id: int) -> bool:
    row = session.get(NovelVoicePref, novel_id)
    return bool(row.auto_synthesize) if row else False  # default OFF


def set_auto_synthesize(session, novel_id: int, enabled: bool) -> NovelVoicePref:
    row = session.get(NovelVoicePref, novel_id)
    if row is None:
        row = NovelVoicePref(novel_id=novel_id,
                             auto_synthesize=enabled)
        session.add(row)
    else:
        row.auto_synthesize = enabled
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
    session.commit()
    session.refresh(row)
    return row


def merge_cast(existing: dict[str, tuple[str, datetime]],
               incoming: dict[str, tuple[str, datetime]]) -> dict[str, tuple[str, datetime]]:
    """Per-character-key LWW: newest updated_at wins per char_key (UTC)."""
    out = dict(existing)
    for k, (voice, ts) in incoming.items():
        if ts.tzinfo is None:
            raise ValueError(f"naive timestamp for char_key {k!r}: UTC required")
        cur = out.get(k)
        if cur is None or ts > cur[1]:
            out[k] = (voice, ts)
    return out


def get_cast(session, novel_id: int) -> dict[str, str]:
    from .models import VoiceMap
    from sqlmodel import select
    rows = session.exec(select(VoiceMap).where(
        VoiceMap.novel_id == novel_id)).all()
    return {r.char_key: r.voice for r in rows}


def put_cast(session, novel_id: int, mapping: dict[str, str],
             now: datetime | None = None) -> dict[str, str]:
    """Upsert per char_key with LWW timestamp (one row per novel+char)."""
    from .models import VoiceMap, utcnow
    from sqlmodel import select
    now = now or utcnow()
    if now.tzinfo is None:
        raise ValueError("naive timestamp rejected: UTC required")
    for char_key, voice in mapping.items():
        if not char_key or not voice:
            raise ValueError("char_key and voice must be non-empty")
        row = session.exec(select(VoiceMap).where(
            VoiceMap.novel_id == novel_id,
            VoiceMap.char_key == char_key)).first()
        if row is None:
            row = VoiceMap(novel_id=novel_id, char_key=char_key,
                           voice=voice, updated_at=now)
        elif now >= row.updated_at:
            row.voice = voice
            row.updated_at = now
        # else: stale write loses (LWW), keep stored row
        session.add(row)
    session.commit()
    return get_cast(session, novel_id)


def validate_pron(pattern: str, replacement: str) -> None:
    if not pattern or not pattern.strip():
        raise ValueError("pron pattern must be non-empty")
    if len(pattern) > 200:
        raise ValueError("pron pattern too long (max 200)")
    if len(replacement) > 500:
        raise ValueError("pron replacement too long (max 500)")


def preview_pron(text: str, pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Hit counts for candidate rules without saving (editor preview)."""
    import re
    from .pron import PronRule, preview_hit_count
    rules = [PronRule(p, r, re.compile(r"\b" + re.escape(p) + r"\b"))
             for p, r in sorted(pairs, key=lambda x: len(x[0]), reverse=True)]
    return preview_hit_count(text, rules)


def sample_preview(text: str, voice: str, speed: float = 1.0) -> dict:
    """What WOULD be synthesised for a voice sample (no audio bytes)."""
    from .pron import load_rules, normalise
    rules = load_rules()
    clean, _notes = normalise(text, rules)
    return {"text": clean[:500], "voice": voice, "speed": speed,
            "render_key": render_key_for(voice, speed)}
