"""Pronunciation dictionary: regex pre-replace, longest-first, hit-count preview.

Format (pron_dict.yaml): one `pattern: replacement` per line (comments with #).
Matching is literal, word-boundaried (\\b...\\b), longest pattern first.
Also owns text normalisation shared by render: numbers golden table,
ALL-CAPS fold with acronym safelist, TL/A-N note extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ACRONYM_SAFELIST = frozenset({
    "RPG", "HP", "MP", "NPC", "AI", "UI", "XP", "DM", "GM", "PVP", "PVE",
    "URL", "API", "ID", "OK", "TV", "CD", "DVD", "LN", "WN", "TL",
})

# shortcut: tiny golden table covers the common cases; upgrade to num2words if QA shows gaps.
NUMBER_GOLDEN = {
    "1st": "first", "2nd": "second", "3rd": "third",
    "1-1": "one one",
}

_TL_NOTE_RE = re.compile(r"^(?:TL|T/L|A-?N|Translator|Author)[\s:：\-—].*$",
                         re.IGNORECASE | re.MULTILINE)
_CAPS_RUN = re.compile(r"\b[A-Z]{2,}(?:['’][A-Z]+)?\b")


@dataclass
class PronRule:
    pattern: str
    replacement: str
    compiled: re.Pattern


def parse_yaml(text: str) -> list[tuple[str, str]]:
    """Minimal `pattern: replacement` parser (no PyYAML dep)."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        pat, rep = line.split(":", 1)
        pat, rep = pat.strip(), rep.strip().strip("'\"")
        if pat:
            out.append((pat, rep))
    return out


def rules_from_text(text: str) -> list[PronRule]:
    """Build rules from UI text: `pattern => say-as` lines (or `pattern: say-as`)."""
    conv: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            pat, rep = line.split("=>", 1)
            conv.append(f"{pat.strip()}: {rep.strip()}")
        else:
            conv.append(line)
    pairs = parse_yaml("\n".join(conv))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)  # longest-first
    return [PronRule(p, r, re.compile(r"\b" + re.escape(p) + r"\b"))
            for p, r in pairs]


def load_rules(path: str = "pron_dict.yaml") -> list[PronRule]:
    try:
        with open(path, encoding="utf-8") as f:
            pairs = parse_yaml(f.read())
    except FileNotFoundError:
        return []
    pairs.sort(key=lambda p: len(p[0]), reverse=True)  # longest-first
    return [PronRule(p, r, re.compile(r"\b" + re.escape(p) + r"\b"))
            for p, r in pairs]


def apply(text: str, rules: list[PronRule]) -> str:
    for rule in rules:
        text = rule.compiled.sub(rule.replacement, text)
    return text


def preview_hit_count(text: str, rules: list[PronRule]) -> dict[str, int]:
    """Per-pattern hit counts for the UI preview (no mutation)."""
    return {rule.pattern: len(rule.compiled.findall(text)) for rule in rules}


def fold_allcaps(text: str) -> str:
    def _fix(m: re.Match) -> str:
        word = m.group(0)
        if word in ACRONYM_SAFELIST:
            return word
        return word.capitalize()
    return _CAPS_RUN.sub(_fix, text)


def normalize_numbers(text: str) -> str:
    for k in sorted(NUMBER_GOLDEN, key=len, reverse=True):
        text = text.replace(k, NUMBER_GOLDEN[k])
    return text


def extract_notes(text: str) -> tuple[str, str]:
    """Split TL/A-N lines into a notes field (never synthesised)."""
    notes = _TL_NOTE_RE.findall(text)
    body = _TL_NOTE_RE.sub("", text)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, "\n".join(n.strip() for n in notes)


def normalise(text: str, rules: list[PronRule] | None = None) -> tuple[str, str]:
    """Full pre-synth pipeline: notes split -> pron -> numbers -> caps fold."""
    body, notes = extract_notes(text)
    if rules:
        body = apply(body, rules)
    body = normalize_numbers(body)
    body = fold_allcaps(body)
    return body, notes
