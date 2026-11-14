"""Refetch lifecycle: per-chapter, per-novel repair, per-source unquarantine.

All three enqueue jobs-table rows (queue truth) — no direct crawling here.
Validate-then-commit still applies downstream: refetched text passes gate.py
before it can touch any chapter row.
"""

from __future__ import annotations

import json

from . import jobs as _jobs


def refetch_chapter(session, novel_id: int, idx: int):
    """Per-chapter button: bypass the text-hash equality skip and force a
    full re-crawl + re-synth of exactly this chapter."""
    return _jobs.enqueue(
        session, "refetch", f"novel:{novel_id}:ch:{idx}",
        payload=json.dumps({"novel_id": novel_id, "idx": idx,
                            "bypass_hash": True}))


def repair_diff(toc_idxs: list[int], existing_idxs, flagged=()) -> list[int]:
    """TOC-diff: chapters in TOC but missing locally, plus flagged ones.
    sorted, deduped. Closing gaps only — good chapters are never re-fetched."""
    existing = set(existing_idxs)
    return sorted((set(toc_idxs) - existing) | (set(flagged) & set(toc_idxs)))


def repair_novel(session, novel_id: int, toc_idxs: list[int],
                 existing_idxs, flagged=()):
    """Per-novel repair button: one job covering gaps+flagged only.
    Returns the job, or None when there is nothing to repair."""
    idxs = repair_diff(toc_idxs, existing_idxs, flagged)
    if not idxs:
        return None
    return _jobs.enqueue(
        session, "repair", f"novel:{novel_id}:repair",
        payload=json.dumps({"novel_id": novel_id, "idxs": idxs}))


def unquarantine(state: dict, session, source: str, probe_idx: int = 1):
    """Per-source unquarantine button: clear the flag and enqueue a probe
    crawl of one chapter. If the probe succeeds the source stays clear;
    if it fails, scheduler.record_result re-quarantines after N consec."""
    entry = state.setdefault(source, {})
    entry.pop("quarantined", None)
    entry["consec"] = 0
    return _jobs.enqueue(
        session, "crawl", f"probe:{source}",
        payload=json.dumps({"source": source, "probe_idx": probe_idx,
                            "unquarantine_probe": True}))
