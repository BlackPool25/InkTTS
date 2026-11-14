"""Playback helpers: manifest/audio resolve, Range/206, cold-synth, prefetch.

Cold/short/offline/multi-tab rules (locked):
- Cold chapter (no audio yet / status=synthesizing): HTTP 202 + client
  queue-seeks (never set currentTime on empty src). See audio.seekWhenReady.
- Short chapters (<60s): always prefetch N+1 (boundary crossings frequent).
- Opus padding: `ended` is the single chapter-advance trigger, even when
  currentTime overshoots manifest end.
- Orphan repair direction: audio without manifest is unusable (timings live
  in the manifest) -> drop orphan audio + resynth; manifest without audio
  -> resynth missing audio. Manifest is truth, never the reverse.

No second timing model: one mapping layer only (sentence binary search
mirrors frontend/src/lib/manifest.ts). No WS-PCM: control-plane only.
"""
from __future__ import annotations

import os

PREFETCH_BOUNDARY_S = 60


# --- prefetch (<60s rule) ----------------------------------------------------
def should_prefetch(duration_s: float, position_s: float) -> bool:
    """Mirror of frontend shouldPrefetchNext. Short chapters always prefetch."""
    if not (duration_s == duration_s):  # NaN duration -> not ready yet
        return False
    if duration_s < PREFETCH_BOUNDARY_S:
        return True
    return (duration_s - position_s) < PREFETCH_BOUNDARY_S


# --- Range/206 helper ----------------------------------------------------------
def parse_range(range_header: str | None, file_size: int) -> tuple[int, int]:
    """Parse `Range: bytes=start-end` -> (start, end-inclusive).

    Raises ValueError on unsatisfiable/invalid (caller maps to 416).
    Suffix ranges (`bytes=-500`) and open ends (`bytes=500-`) supported.
    """
    if not range_header or not range_header.startswith("bytes="):
        raise ValueError("missing or non-bytes range")
    spec = range_header[len("bytes="):].strip()
    if "," in spec:  # no multipart; single range only
        raise ValueError("multipart ranges unsupported")
    start_s, sep, end_s = spec.partition("-")
    if not sep:
        raise ValueError("bad range spec")
    if start_s == "":  # suffix: last N bytes
        n = int(end_s)
        if n <= 0:
            raise ValueError("bad suffix range")
        start = max(0, file_size - n)
        return start, file_size - 1
    start = int(start_s)
    end = int(end_s) if end_s != "" else file_size - 1
    if start < 0 or end < start or start >= file_size:
        raise ValueError("unsatisfiable range")
    return start, min(end, file_size - 1)


def content_range_header(start: int, end: int, size: int) -> str:
    return f"bytes {start}-{end}/{size}"


def if_range_allows(if_range: str | None, etag: str) -> bool:
    """If-Range gate: missing -> honor Range; match -> honor; stale -> full 200."""
    if not if_range:
        return True
    return if_range.strip().strip('"') == etag.strip().strip('"')


# --- sentence timing (single mapping layer) -----------------------------------
def sentence_index_at(sentences: list[dict], t: float) -> int:
    """Binary search, upper-bound tie-break (== frontend sentenceIndexAt)."""
    if not sentences:
        return -1
    if t < sentences[0]["t_start"]:
        return -1
    lo, hi, ans = 0, len(sentences) - 1, 0
    while lo <= hi:
        mid = (lo + hi) >> 1
        if sentences[mid]["t_start"] <= t:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def manifest_end_s(sentences: list[dict]) -> float:
    return sentences[-1]["t_end"] if sentences else 0.0


def is_past_manifest_end(sentences: list[dict], current_time: float) -> bool:
    """Opus padding: ended may fire past manifest end -> still chapter-done."""
    return current_time >= manifest_end_s(sentences)


# --- cold-synth 202 + queue-seek rule ------------------------------------------
def playback_status(chapter_status: str | None, audio_exists: bool) -> tuple[int, str]:
    """(http_status, action). Cold -> 202 + queue-seek, never set-on-empty-src."""
    if chapter_status == "ready" and audio_exists:
        return 200, "play"
    return 202, "queue-seek"


# --- orphan repair direction (manifest is truth) -------------------------------
def repair_direction(audio_exists: bool, manifest_exists: bool) -> str:
    if audio_exists and manifest_exists:
        return "ok"
    if audio_exists and not manifest_exists:
        return "drop-orphan-audio+resynth"  # timings unknown without manifest
    if manifest_exists and not audio_exists:
        return "resynth-missing-audio"
    return "cold-synth"


def resolve_playback(novel_id: int, idx: int, data_dir: str,
                     chapter_row=None) -> dict:
    """Resolve audio/sidecar paths for a chapter row (or cold 202 payload)."""
    if chapter_row is None:
        return {"status": 202, "action": "queue-seek", "repair": "cold-synth",
                "audio_url": None, "manifest_url": None, "audio_size": 0,
                "accept_ranges": "bytes", "render_key": "", "text_hash": "",
                "prefetch_boundary_s": PREFETCH_BOUNDARY_S}
    audio_abs = os.path.join(data_dir, chapter_row.audio_path or "")
    sc_abs = os.path.join(data_dir, chapter_row.sidecar_path or "")
    audio_ok = bool(chapter_row.audio_path) and os.path.exists(audio_abs)
    sc_ok = bool(chapter_row.sidecar_path) and os.path.exists(sc_abs)
    http, action = playback_status(
        getattr(chapter_row, "status", None), audio_ok and sc_ok)
    audio_size = os.path.getsize(audio_abs) if audio_ok else 0
    return {
        "status": http,
        "action": action,
        "repair": repair_direction(audio_ok, sc_ok),
        "audio_url": f"/api/v1/books/{novel_id}/chapters/{idx}/audio.opus"
                     if audio_ok else None,
        "manifest_url": f"/api/v1/books/{novel_id}/chapters/{idx}/chapter.json"
                        if sc_ok else None,
        "audio_size": audio_size,
        "accept_ranges": "bytes",
        "render_key": getattr(chapter_row, "render_key", ""),
        "text_hash": getattr(chapter_row, "text_hash", ""),
        "prefetch_boundary_s": PREFETCH_BOUNDARY_S,
    }
