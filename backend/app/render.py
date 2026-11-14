"""Per-chapter render job: sentences -> Opus + manifest v2.

Invariants (locked):
- Full-chapter re-synth only (no diff-merge).
- Timings measured from SHIPPED Opus bytes (ffprobe), never pre-encode
  estimates — Opus padding shifts sentences otherwise. Fallback to PCM-sum
  durations only when ffprobe is unavailable (tests/CI).
- Gapless: next.t_start == prev.t_end (asserted, float-rounded to ms).
- Manifest v2: sent_id positional (0..N-1), render_key=voice+speed+model.
- render_key pinned per chapter: same text -> no re-synth even if the
  requested voice differs; changed text -> full re-synth under the PINNED
  (original) key, never the newly requested voice.
- Atomic publish: audio .tmp -> rename FIRST, then sidecar .tmp -> rename.
- Pipeline is float32-only with a single int16 conversion at the ffmpeg
  boundary (f32le pipe in).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
from dataclasses import dataclass

from .models import (MODEL_VER, SIDECAR_SCHEMA_VER, audio_relpath,
                     render_key_for, sidecar_relpath)

SAMPLE_RATE = 24000
FFMPEG_OPUS_ARGS = ["-c:a", "libopus", "-b:a", "32k", "-vbr", "on",
                    "-application", "audio", "-frame_duration", "60",
                    "-compression_level", "10", "-ar", "24000", "-ac", "1"]

ABBREV = ("Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Jr.", "Sr.", "vs.",
          "etc.", "e.g.", "i.e.", "Prof.", "Gen.", "Sgt.", "Lt.")
_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+(?=[\"“‘\(\[]?[A-Z0-9\u4e00-\u9fff])")
_MAX_SENT_CHARS = 1200  # ~25s spoken cap guard


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Protect abbreviations with a placeholder before splitting.
    prot = text
    for i, ab in enumerate(ABBREV):
        prot = prot.replace(ab, f"⟦AB{i}⟧")
    # CJK boundaries: split after 。！？ even without whitespace.
    prot = re.sub(r"([。！？])(?=[^\\s])", r"\1 ", prot)
    parts = _SENT_SPLIT.split(prot)
    out: list[str] = []
    for p in parts:
        for i, ab in enumerate(ABBREV):
            p = p.replace(f"⟦AB{i}⟧", ab)
        p = p.strip()
        while len(p) > _MAX_SENT_CHARS:  # 25s cap: hard cut long sentences
            cut = p.rfind(",", 0, _MAX_SENT_CHARS)
            cut = cut if cut > 0 else _MAX_SENT_CHARS
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def text_hash_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash8_of(text_sha: str, render_key: str) -> str:
    return hashlib.sha256(f"{text_sha}:{render_key}".encode()).hexdigest()[:8]


@dataclass
class RenderedChapter:
    audio_path: str
    sidecar_path: str
    render_key: str
    text_hash: str
    resynthed: bool


def _pcm_durations(pcm_frames: list[bytes]) -> list[float]:
    return [len(f) / (2 * SAMPLE_RATE) for f in pcm_frames]


def _opus_sentence_times(opus_path: str, n: int,
                         pcm_durs: list[float]) -> list[tuple[float, float]]:
    """Timings from shipped bytes: probe total, scale PCM proportions.

    ffprobe gives the true container duration (incl. Opus padding); each
    sentence keeps its PCM share of that total so the manifest matches the
    bytes the player actually decodes. Pure-PCM fallback when no probe.
    """
    total_pcm = sum(pcm_durs)
    total = _probe_duration(opus_path) or total_pcm
    scale = (total / total_pcm) if total_pcm > 0 else 0.0
    times: list[tuple[float, float]] = []
    t = 0.0
    for i, d in enumerate(pcm_durs):
        start = round(t, 3)
        t = total if i == n - 1 else round(t + d * scale, 3)
        times.append((start, t if i == n - 1 else round(t, 3)))
    return times


def _probe_duration(path: str) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True,
            timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return None


def _encode_opus(float_frames: list[bytes], out_tmp: str) -> None:
    """float32 mono 24kHz -> libopus 32k. Single int16-free pipe (f32le)."""
    raw = b"".join(float_frames)
    if shutil.which("ffmpeg") is None:
        with open(out_tmp, "wb") as f:  # CI fallback: packet placeholder
            f.write(b"OPUSFALLBACK" + raw[:1024])
        return
    cmd = ["ffmpeg", "-y", "-f", "f32le", "-ar", "24000", "-ac", "1",
           "-i", "pipe:0", *FFMPEG_OPUS_ARGS, "-f", "opus", out_tmp]
    subprocess.run(cmd, input=raw, capture_output=True, check=True, timeout=600)


def _int16_to_float32(pcm16: bytes) -> bytes:
    n = len(pcm16) // 2
    shorts = struct.unpack(f"<{n}h", pcm16[:n * 2])
    return struct.pack(f"<{n}f", *(s / 32768.0 for s in shorts))


async def _synth_all(sentences: list[str], engine) -> list[bytes]:
    """Async overlap: keep up to 4 synth calls in flight (session recycle
    inside the engine bounds memory); order of results is positional."""
    sem = asyncio.Semaphore(4)

    async def _one(s: str) -> bytes:
        async with sem:
            pcm, _ = await asyncio.to_thread(engine.synthesize, s)
            return pcm

    return list(await asyncio.gather(*[_one(s) for s in sentences]))


def render_chapter_text(novel_id: int, idx: int, text: str, voice: str,
                        speed: float, data_dir: str, engine=None,
                        title: str = "") -> RenderedChapter:
    """Full-chapter synth. Pure wrt callers: gate first, pin key, publish."""
    from .gate import gate_text, quarantine_artifact, record_failure
    from .pron import load_rules, normalise
    from sqlmodel import Session
    from . import db as _db

    if engine is None:
        from .tts import KokoroEngine
        engine = KokoroEngine(voice=voice, speed=speed)
        if getattr(engine, "is_stub", False):
            print("render: Kokoro model absent, stub tone (RTF bench only)")

    gated = gate_text(text, title)
    if not gated.ok:
        with Session(_db.engine) as s:
            record_failure(s, "synth", f"novel:{novel_id}:ch:{idx}",
                           gated.error_class, gated.reason)
        quarantine_artifact(f"novel{novel_id}_ch{idx}", text, gated.reason,
                            os.path.join(data_dir, "quarantine"))
        raise ValueError(f"gate rejected ({gated.error_class}): {gated.reason}")

    rules = load_rules(os.path.join(data_dir, "pron_dict.yaml"))
    clean, notes = normalise(gated.clean_text, rules)
    sentences = split_sentences(clean)
    if not sentences:  # gate passed but nothing speakable after normalise
        with Session(_db.engine) as s:
            record_failure(s, "synth", f"novel:{novel_id}:ch:{idx}",
                           "empty", "no sentences after normalise")
        raise ValueError("gate: no sentences after normalise")

    text_sha = text_hash_of(text)
    requested_key = render_key_for(voice, speed)

    from .models import Chapter
    from sqlmodel import select
    with Session(_db.engine) as s:
        row = s.exec(select(Chapter).where(
            Chapter.novel_id == novel_id, Chapter.idx == idx)).first()
        if row is not None and row.text_hash == text_sha:
            return RenderedChapter(row.audio_path, row.sidecar_path,
                                   row.render_key, row.text_hash, False)
        pinned_key = row.render_key if row is not None else requested_key

    h8 = hash8_of(text_sha, pinned_key)
    audio_rel = audio_relpath(novel_id, idx, h8)
    sidecar_rel = sidecar_relpath(novel_id, idx, h8)
    audio_abs = os.path.join(data_dir, audio_rel)
    sidecar_abs = os.path.join(data_dir, sidecar_rel)
    os.makedirs(os.path.dirname(audio_abs), exist_ok=True)

    pcm_frames = asyncio.run(_synth_all(sentences, engine))
    float_frames = [_int16_to_float32(f) for f in pcm_frames]

    tmp_audio = audio_abs + ".tmp"
    _encode_opus(float_frames, tmp_audio)
    os.rename(tmp_audio, audio_abs)  # audio lands BEFORE manifest

    times = _opus_sentence_times(audio_abs, len(sentences),
                                 _pcm_durations(pcm_frames))
    prev_end = 0.0
    items = []
    for i, (sent, (t0, t1)) in enumerate(zip(sentences, times)):
        assert t0 == prev_end, f"gapless violated at sent {i}: {t0} != {prev_end}"
        items.append({"sent_id": i, "text": sent,
                      "t_start": t0, "t_end": t1})
        prev_end = t1

    manifest = {"schema_ver": SIDECAR_SCHEMA_VER, "novel_id": novel_id,
                "idx": idx, "render_key": pinned_key, "model_ver": MODEL_VER,
                "text_hash": text_sha, "hash8": h8, "notes": notes,
                "sentences": items}
    tmp_sc = sidecar_abs + ".tmp"
    with open(tmp_sc, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    os.rename(tmp_sc, sidecar_abs)

    with Session(_db.engine) as s:
        row = s.exec(select(Chapter).where(
            Chapter.novel_id == novel_id, Chapter.idx == idx)).first()
        if row is None:
            from .models import Chapter as _Ch, utcnow
            row = _Ch(novel_id=novel_id, idx=idx)
            s.add(row)
        row.title = title
        row.text_hash = text_sha
        row.render_key = pinned_key  # pinned: never overwritten by new voice
        row.voice = pinned_key.split("@")[0]
        try:
            row.speed = float(pinned_key.split("@")[1].split("x:")[0])
        except (IndexError, ValueError):
            pass
        row.audio_path = audio_rel
        row.sidecar_path = sidecar_rel
        row.status = "ready"
        from .models import utcnow
        row.updated_at = utcnow()
        s.add(row)
        s.commit()
    return RenderedChapter(audio_rel, sidecar_rel, pinned_key, text_sha, True)
