"""M4B export: concat chapter Opus -> AAC-LC 64k mono + ffmetadata chapters.

- Chapters are the shipped per-chapter Opus files (never re-synth for export).
- Chapter marks carry a +43ms priming shift so players compensate the
  encoder priming ffmpeg adds at the concat head. NOTE: the mp4/ipod muxer
  clamps chapter[0].START to media start (0) — the shift survives on
  chapters 2..N and as END[i]==START[i+1] contiguity, which is what players
  consume.
- Evictable-first: encode to `<out>.tmp`, rename only on success — a failed
  encode never clobbers the last good .m4b (same rule as backup rotate).
- No ffmpeg on PATH -> M4BUnavailable (route maps to 503 + loud jobs row,
  never a crash, never a half file).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

PRIMING_MS = 43  # encoder-priming compensation applied to chapter STARTs
M4B_MIME = "audio/mp4"


class M4BError(Exception):
    """Export cannot proceed (missing inputs, encode failure)."""


class M4BUnavailable(M4BError):
    """ffmpeg absent: loud 503 + jobs row, never a crash."""


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def require_ffmpeg() -> None:
    if not have_ffmpeg():
        raise M4BUnavailable("ffmpeg not on PATH: M4B export unavailable")


def m4b_relpath(novel_id: int) -> str:
    return f"audio/{novel_id}/book.m4b"


def chapter_starts_ms(durations_ms: list[int],
                      priming_ms: int = PRIMING_MS) -> list[int]:
    """Cumulative STARTs, each shifted +priming (players skip priming)."""
    starts, total = [], 0
    for dur in durations_ms:
        starts.append(total + priming_ms)
        total += dur
    return starts


def ffmetadata_text(titles: list[str], durations_ms: list[int]) -> str:
    """ffmetadata with contiguous chapters: END[i] == START[i+1]."""
    if len(titles) != len(durations_ms):
        raise M4BError("titles/durations length mismatch")
    lines = [";FFMETADATA1"]
    for title, start, dur in zip(titles, chapter_starts_ms(durations_ms),
                                 durations_ms):
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}",
                  f"END={start + dur}", f"title={title}"]
    return "\n".join(lines) + "\n"


def concat_list_text(audio_paths: list[str]) -> str:
    return "".join(f"file '{p}'\n" for p in audio_paths)


def ffmpeg_m4b_cmd(list_path: str, meta_path: str, out_tmp: str) -> list[str]:
    # -f ipod pinned: tmp outputs end in .tmp so extension-sniffing can't
    # pick the muxer (that mis-guess fails the encode). ipod == the muxer
    # .m4b gets by default, and it preserves sub-second chapter STARTs.
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-i", meta_path, "-map", "0:a", "-map_metadata", "1",
            "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-f", "ipod", out_tmp]


def sidecar_duration_ms(sidecar_abs: str) -> int:
    """Chapter duration from shipped sidecar: last sentence t_end -> ms."""
    try:
        with open(sidecar_abs, encoding="utf-8") as f:
            m = json.load(f)
        return int(round(float(m["sentences"][-1]["t_end"]) * 1000))
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        raise M4BError(f"unreadable sidecar {sidecar_abs}: {e}") from e


def build_m4b(audio_abs_paths: list[str], titles: list[str],
              durations_ms: list[int], out_abs: str) -> str:
    """Concat -> AAC-LC 64k mono .m4b with chapters. Returns out_abs."""
    if not audio_abs_paths:
        raise M4BError("no chapter audio to export")
    require_ffmpeg()
    for p in audio_abs_paths:
        if not os.path.isfile(p):
            raise M4BError(f"missing chapter audio: {p}")
    work = tempfile.mkdtemp(prefix="inkcast-m4b-")
    try:
        list_path = os.path.join(work, "concat.txt")
        meta_path = os.path.join(work, "chapters.ffmeta")
        with open(list_path, "w", encoding="utf-8") as f:
            f.write(concat_list_text(audio_abs_paths))
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(ffmetadata_text(titles, durations_ms))
        tmp_out = out_abs + ".tmp"  # evictable: rotate only on success
        os.makedirs(os.path.dirname(os.path.abspath(out_abs)), exist_ok=True)
        try:
            subprocess.run(ffmpeg_m4b_cmd(list_path, meta_path, tmp_out),
                           capture_output=True, text=True, check=True,
                           timeout=3600)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            try:
                os.unlink(tmp_out)
            except OSError:
                pass
            raise M4BError(f"ffmpeg encode failed: {e}") from e
        os.replace(tmp_out, out_abs)
        return out_abs
    finally:
        shutil.rmtree(work, ignore_errors=True)
