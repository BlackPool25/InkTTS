"""Content gate: validate-then-commit. Failures -> jobs row, NEVER chapter row.

Runs BEFORE any chapter row exists. Two-tier: micro-chapters (short
interludes / single-line breaks) pass a lower bar instead of being dropped.

Error classes (written to jobs.error_class):
- blocked: challenge/captcha/paywall signatures -> backoff, quarantine signal
- empty: below min-length / no speakable content
- parse: mostly-symbols / language-ratio failure (fail LOUD, no retry)
- transient: reserved for fetcher faults (not raised here, listed for truth)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MIN_CHARS = 50
MICRO_CHARS = 8  # two-tier: micro-chapters pass at >= 8 chars
MICRO_HINT = re.compile(r"^\s*(?:interlude|break|—+\s*\*?\s*—+|\*\s*\*\s*\*)\b",
                        re.IGNORECASE)

BLOCK_MARKERS = (
    "chapter locked", "please subscribe", "continue reading on",
    "this chapter is locked", "privilege chapter",
)
CHALLENGE_SIGS = (
    "cloudflare", "cf-challenge", "are you human", "recaptcha",
    "access denied", "just a moment", "verify you are human",
    "datadome", "perimeterx",
)

_QUARANTINE_DIR = "data/quarantine"


@dataclass
class GateResult:
    ok: bool
    error_class: str = ""  # blocked|empty|parse
    reason: str = ""
    notes: str = ""
    clean_text: str = ""


def _speakable_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = sum(1 for c in text if c.isalpha() or "\u4e00" <= c <= "\u9fff"
                  or "\u3040" <= c <= "\u30ff" or "\uac00" <= c <= "\ud7a3")
    return letters / max(1, len(text))


def gate_text(text: str, title: str = "") -> GateResult:
    from .pron import extract_notes

    raw = text or ""
    lowered = raw.lower()

    for sig in CHALLENGE_SIGS:
        if sig in lowered:
            return GateResult(False, "blocked", f"challenge signature: {sig}")
    for marker in BLOCK_MARKERS:
        if marker in lowered:
            return GateResult(False, "blocked", f"block marker: {marker}")

    body, notes = extract_notes(raw)
    stripped = body.strip()
    micro = bool(MICRO_HINT.match(title or "") or MICRO_HINT.match(stripped))
    floor = MICRO_CHARS if micro else MIN_CHARS
    if len(stripped) < floor:
        return GateResult(False, "empty",
                           f"too short ({len(stripped)} < {floor})", notes)
    if _speakable_ratio(stripped) < 0.10:
        return GateResult(False, "parse",
                           f"speakable ratio {_speakable_ratio(stripped):.2f} < 0.10",
                           notes)
    return GateResult(True, "", "ok", notes, body)


def quarantine_artifact(ref: str, text: str, reason: str,
                        base_dir: str = _QUARANTINE_DIR) -> str:
    """Persist rejected content for review; returns artifact path."""
    import os
    import re as _re

    os.makedirs(base_dir, exist_ok=True)
    safe = _re.sub(r"[^A-Za-z0-9_.-]+", "_", ref)[:80]
    path = os.path.join(base_dir, f"{safe}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<!-- {reason} -->\n")
        f.write(text)
    return path


def record_failure(session, kind: str, ref: str, error_class: str,
                   error: str, payload: str = "") -> None:
    """Write the failure to the jobs table truth (never a chapter row)."""
    from .models import Job, utcnow

    session.add(Job(kind=kind, ref=ref, status="blocked"
                    if error_class == "blocked" else "failed",
                    error_class=error_class, error=error, payload=payload,
                    updated_at=utcnow()))
    session.commit()
