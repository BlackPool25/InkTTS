"""Frozen table shapes. Task 1 owns this file; later workers read, don't reshape.

Conventions (locked):
- Audio file:  audio/{novel_id}/{shard}/{idx}.{hash8}.opus, shard = idx // 1000
- Opus: 32k mono 24kHz, immutable (hash-in-URL, ETag = render_key hash).
- Sidecar: {idx}.{hash8}.json v2, sent_id positional (NOT stable across
  re-scrapes), render_key = voice+speed+model, timings measured from shipped
  Opus bytes, gapless invariant next.t_start == prev.t_end.
- render_key pinned per chapter: voice/speed/model change never re-renders
  old chapters (lazy re-render + explicit button only).
- Progress merge: server-time LWW. Timestamps UTC everywhere.
"""
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Field, SQLModel

MODEL_VER = "kokoro-82M-onnx-v1"  # surfaced in /health + render_key
SIDECAR_SCHEMA_VER = 2


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def audio_relpath(novel_id: int, idx: int, hash8: str) -> str:
    """audio/{novel}/{shard}/{idx}.{hash8}.opus, shard = idx // 1000."""
    return f"audio/{novel_id}/{idx // 1000}/{idx}.{hash8}.opus"


def sidecar_relpath(novel_id: int, idx: int, hash8: str) -> str:
    return f"audio/{novel_id}/{idx // 1000}/{idx}.{hash8}.json"


def render_key_for(voice: str, speed: float, model_ver: str = MODEL_VER) -> str:
    return f"{voice}@{speed}x:{model_ver}"


class Novel(SQLModel, table=True):
    __tablename__ = "novels"
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    author: str = Field(default="", index=True)
    fingerprint: str = Field(default="", index=True)  # title+author identity key
    source_url: str = Field(default="")
    source_aliases: str = Field(default="")  # JSON list of URL aliases
    cover_path: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Chapter(SQLModel, table=True):
    __tablename__ = "chapters"
    id: Optional[int] = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    idx: int = Field(index=True)
    title: str = Field(default="")
    text_hash: str = Field(default="", index=True)  # full source text sha256
    text: str = Field(default="")  # source text (synth reads this; audio is derived)
    render_key: str = Field(default="", index=True)  # voice+speed+model
    voice: str = Field(default="")
    speed: float = Field(default=1.0)
    audio_path: str = Field(default="")  # relpath under /data
    sidecar_path: str = Field(default="")  # relpath under /data
    status: str = Field(default="ready", index=True)  # ready|synthesizing|blocked|empty
    updated_at: datetime = Field(default_factory=utcnow)


class Voice(SQLModel, table=True):
    __tablename__ = "voices"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # e.g. kokoro voice id
    lang: str = Field(default="en")
    is_default: bool = Field(default=False)
    updated_at: datetime = Field(default_factory=utcnow)


class VoiceMap(SQLModel, table=True):
    """Per-novel character -> voice cast. Merged per-character-key in backups."""
    __tablename__ = "voice_map"
    id: Optional[int] = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    char_key: str = Field(index=True)
    voice: str
    updated_at: datetime = Field(default_factory=utcnow)


class PronEntry(SQLModel, table=True):
    """Pronunciation dict. Regex pre-replace: \\b + longest-first. Record-level union on merge."""
    __tablename__ = "pron_dict"
    id: Optional[int] = Field(default=None, primary_key=True)
    pattern: str = Field(unique=True, index=True)
    replacement: str
    updated_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    """Queue truth (no broker). Atomic claim+lease; error_class decides retry."""
    __tablename__ = "jobs"
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # synth | crawl | refetch | backup | repair
    ref: str = Field(default="", index=True)  # e.g. "novel:3:ch:12"
    status: str = Field(default="queued", index=True)  # queued|running|done|failed|blocked
    error_class: str = Field(default="")  # blocked|empty|parse|transient
    attempts: int = Field(default=0)
    lease_until: Optional[datetime] = Field(default=None)
    payload: str = Field(default="")  # JSON
    error: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Progress(SQLModel, table=True):
    """Sentence-granularity position. Server-time LWW + 5s flap. UTC."""
    __tablename__ = "progress"
    id: Optional[int] = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    chapter_idx: int = Field(default=0)
    sent_id: int = Field(default=0)
    position_ms: int = Field(default=0)
    text_hash: str = Field(default="")  # orphan flag when chapter text changed
    updated_at: datetime = Field(default_factory=utcnow)


class OpdsToken(SQLModel, table=True):
    """Per-device OPDS exemption tokens (readers can't log in). Long random."""
    __tablename__ = "opds_tokens"
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True)
    device: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
