"""Text pipeline: split -> normalize -> pron-dict -> gate -> LRC.

Ported from backend/app (split/normalize/pron/gate), new home. English v1.
Request budget: <=200 chars / <=40 words per synth request (sentences are
grouped, never split). Turnstile/modern challenge signals hard-block.
TNs are already dropped at extraction; extract_notes keeps any residue
out of narration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .pron import apply, extract_notes, load_rules, normalise

ABBREV = ("Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Jr.", "Sr.", "vs.",
          "etc.", "e.g.", "i.e.", "Prof.", "Gen.", "Sgt.", "Lt.")
_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+(?=[\"“‘\(\[]?[A-Z0-9\u4e00-\u9fff])")
_MAX_SENT_CHARS = 300  # synth-request budget: ~20s audio max, keeps each
_MIN_MERGE_CHARS = 60  # fragments shorter than this merge into the next
# sentence (dialogue interjections like "My Lord!" must not become their
# own overlay line + audio file — they flash past and feel like skips).
_CLAUSE_CUT = ",;:—–-"
# render under the per-sentence timeout on CPU voices.
MIN_CHARS = 20  # extension text is user-visible: lower floor than scraper
REQ_MAX_CHARS = 200
REQ_MAX_WORDS = 40

BLOCK_MARKERS = (
    "chapter locked", "please subscribe", "continue reading on",
    "this chapter is locked", "privilege chapter",
)
# Modern signals (audit: stale IUAM copy-strings banned as fixtures AND
# as sole gate — iframe/title signals are the real detectors).
CHALLENGE_SIGS = (
    "cloudflare", "cf-challenge", "challenges.cloudflare",
    "cf-turnstile", "turnstile", "__cf_chl", "are you human", "recaptcha",
    "access denied", "just a moment", "verify you are human",
    "attention required", "verifying you are human",
    "datadome", "perimeterx",
)


@dataclass
class Gate:
    ok: bool
    error_class: str = ""
    reason: str = ""


def gate_paragraph(text: str) -> Gate:
    lowered = (text or "").lower()
    for sig in CHALLENGE_SIGS:
        if sig in lowered:
            return Gate(False, "challenge", f"challenge signature: {sig}")
    for marker in BLOCK_MARKERS:
        if marker in lowered:
            return Gate(False, "blocked", f"block marker: {marker}")
    if len((text or "").strip()) < MIN_CHARS:
        return Gate(False, "empty", "too short")
    letters = sum(1 for c in text if c.isalpha())
    if letters / max(1, len(text)) < 0.10:
        return Gate(False, "parse", "speakable ratio < 0.10")
    return Gate(True)


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    prot = text
    for i, ab in enumerate(ABBREV):
        prot = prot.replace(ab, f"⟦AB{i}⟧")
    prot = re.sub(r"([。！？])(?=[^\s])", r"\1 ", prot)
    parts = _SENT_SPLIT.split(prot)
    out: list[str] = []
    for p in parts:
        for i, ab in enumerate(ABBREV):
            p = p.replace(f"⟦AB{i}⟧", ab)
        p = p.strip()
        while len(p) > _MAX_SENT_CHARS:
            cut = -1
            for cc in _CLAUSE_CUT:
                cut = max(cut, p.rfind(cc, 0, _MAX_SENT_CHARS))
            cut = cut + 1 if cut > 0 else _MAX_SENT_CHARS
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    # Merge stub fragments so one breath = one line = one audio file.
    # Forward (stub joins the next sentence, keeping dialogue together);
    # a trailing stub joins the previous one. Never exceed the max budget.
    res: list[str] = []
    pending = ""
    for frag in out:
        if pending:
            combined = f"{pending} {frag}"
            if len(combined) <= _MAX_SENT_CHARS:
                frag = combined
                pending = ""
            else:
                res.append(pending)  # next is full: lone stub stays (rare)
                pending = ""
        if len(frag) < _MIN_MERGE_CHARS:
            pending = frag
        else:
            res.append(frag)
    if pending:
        if res and len(res[-1]) + 1 + len(pending) <= _MAX_SENT_CHARS:
            res[-1] = f"{res[-1]} {pending}"
        elif res:
            res.append(pending)  # previous full: lone stub stays (rare)
        else:
            res.append(pending)  # only fragment: keep it
    return [m for m in res if m]


def process_chapter(paragraphs: list[str],
                    rules=None) -> tuple[list[dict], list[dict]]:
    """Returns (sentences, problems). sentences: [{idx, text}]; problems:
    [{para, error_class, reason}] for gated-out paragraphs (never narrated,
    never blocking siblings). Title must be stripped BEFORE this call."""
    if rules is None:
        rules = []
    sentences: list[dict] = []
    problems: list[dict] = []
    for para in paragraphs:
        g = gate_paragraph(para)
        if not g.ok:
            problems.append({"para": para[:80], "error_class": g.error_class,
                             "reason": g.reason})
            continue
        clean, _notes = normalise(para, rules)
        for sent in split_sentences(clean):
            if sent.strip():
                sentences.append({"idx": len(sentences), "text": sent})
    return sentences, problems


def request_chunks(sentences: list[dict]) -> list[list[dict]]:
    """Group sentences into synth requests within the engine budget."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    chars = words = 0
    for s in sentences:
        w = len(s["text"].split())
        if cur and (chars + len(s["text"]) > REQ_MAX_CHARS
                   or words + w > REQ_MAX_WORDS):
            chunks.append(cur)
            cur, chars, words = [], 0, 0
        cur.append(s)
        chars += len(s["text"])
        words += w
    if cur:
        chunks.append(cur)
    return chunks


def build_lrc(entries: list[dict]) -> str:
    """Enhanced-LRC: [mm:ss.cc] line + <s>word tags when word timings known.
    entries: [{idx, text, words: [{w,start,end}]|None, file}]. Chatterbox
    entries (words=None) degrade to plain lines (line-sweep client-side)."""
    def fmt(t: float) -> str:
        m, s = divmod(max(0.0, t), 60)
        return f"[{int(m):02d}:{s:05.2f}]"
    lines = []
    for e in entries:
        words = e.get("words")
        if words:
            tagged = "".join(f"<{w['start']:.2f}>{w['w']}" for w in words)
            lines.append(f"{fmt(words[0]['start'])}{tagged}")
        else:
            lines.append(f"{fmt(e.get('start', 0.0))}{e['text']}")
    return "\n".join(lines) + "\n"
