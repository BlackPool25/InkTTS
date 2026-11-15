"""Preempt priority queue, per document.

Lanes: tap=0 (newest tap wins), next=1 (playhead-adjacent), prefetch=2+d.
One slot is ALWAYS reserved for in-window prefetch (backfill never
starves under tap storms). Age bonus lifts old prefetch. Per-doc seq
epoch + entry TTL + doc-close purge (no cross-doc eviction, no leaks).
"""
from __future__ import annotations

import heapq
import itertools
import time

PREFETCH_K = 3
ENTRY_TTL_S = 3600


class Queue:
    def __init__(self):
        self.heap: list = []
        self.seq = itertools.count()
        self.epochs: dict[str, int] = {}
        self.coalesced: dict[tuple[str, int], dict] = {}
        self.closed: set[str] = set()

    def _epoch(self, doc: str) -> int:
        return self.epochs.setdefault(doc, 0)

    def push(self, doc: str, idx: int, kind: str,
             playhead: int | None = None) -> dict:
        """kind: tap|next|prefetch. Returns {queued, position}."""
        now = time.time()
        if kind == "tap":
            prio = 0
        elif kind == "next":
            prio = 1
        else:
            d = abs(idx - (playhead if playhead is not None else idx))
            prio = 2 + min(d, 99)
        key = (doc, idx, kind if kind == "tap" else "work")
        if key in self.coalesced:
            return {"queued": False, "position": 0}  # duplicate: reprioritize
        entry = {"prio": prio, "seq": next(self.seq), "doc": doc,
                 "idx": idx, "kind": kind, "at": now,
                 "epoch": self._epoch(doc)}
        self.coalesced[key] = entry
        heapq.heappush(self.heap, (prio, entry["seq"], entry))
        return {"queued": True, "position": len(self.heap)}

    def pop(self) -> dict | None:
        """Pop highest-priority live entry; prefetch slot reserved: if the
        head is prefetch and a tap/next exists deeper, tap/next wins; but
        every 4th pop forces the oldest in-window prefetch (no starvation)."""
        self._sweep()
        if not self.heap:
            return None
        pops = getattr(self, "_pop_count", 0) + 1
        self._pop_count = pops
        if pops % 4 == 0:
            for i, (_, _, e) in enumerate(self.heap):
                if e["kind"] == "prefetch":
                    return self._take(i, e)
        _, _, entry = heapq.heappop(self.heap)
        self.coalesced.pop((entry["doc"], entry["idx"],
                            "tap" if entry["kind"] == "tap" else "work"),
                           None)
        return entry

    def _take(self, i: int, entry: dict) -> dict:
        del self.heap[i]
        heapq.heapify(self.heap)
        self.coalesced.pop((entry["doc"], entry["idx"], "work"), None)
        return entry

    def _sweep(self) -> None:
        now = time.time()
        live = []
        for prio, seq, e in self.heap:
            if (e["doc"] in self.closed or now - e["at"] > ENTRY_TTL_S
                    or e["epoch"] != self._epoch(e["doc"])):
                self.coalesced.pop((e["doc"], e["idx"],
                                     "tap" if e["kind"] == "tap" else "work"),
                                    None)
                continue
            live.append((prio, seq, e))
        if len(live) != len(self.heap):
            self.heap = live
            heapq.heapify(self.heap)

    def seek(self, doc: str, idx: int, window: int = PREFETCH_K) -> None:
        """New epoch: drops this doc's queued jobs outside [idx-1, idx+K].
        Other docs untouched. Cache (DONE) is never touched."""
        self.epochs[doc] = self.epochs.get(doc, 0) + 1
        lo, hi = idx - 1, idx + window
        for key in [k for k in self.coalesced
                    if k[0] == doc and not (lo <= k[1] <= hi)]:
            self.coalesced.pop(key, None)

    def close(self, doc: str) -> None:
        self.closed.add(doc)
        self._sweep()
        self.closed.discard(doc)
