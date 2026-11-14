# 27 Crawl error lifecycle + refetch (locked)

Principle: **failed content never touches the chapters table.** Ingest is
validate-then-commit in one transaction: content gate (min length, language
ratio, block markers, challenge signatures) runs on the lncrawl artifact BEFORE
any row is written. A failed/blocked/empty crawl produces a `jobs` row with an
error class — never a chapter row.

## Error classes → behavior
- `blocked` (CF/bot-fight/403/challenge): backoff per source (2^n, cap 48h),
  source health counter++. N consecutive → source quarantined + flag in UI.
- `empty` (gate rejected content): same as blocked, plus artifact kept 7d for
  inspection (`data/quarantine/`).
- `parse` (zip shape changed / missing fields): job fails LOUD (alert in jobs
  panel) — likely lncrawl version drift, needs builder attention, not retries.
- `transient` (timeout/DNS/522): plain retry ×3, then `blocked` handling.

## Refetch paths (user-initiated, all explicit)
1. Per-chapter "refetch": forces re-crawl of one chapter, bypasses hash check,
   re-runs gate, re-synths on change. For "this chapter looks wrong".
2. Per-novel "repair": diffs TOC vs chapters, re-crawls gaps + flagged rows only.
   This is the post-import and post-outage button.
3. Per-source "unquarantine + retry": clears quarantine, resets backoff, runs
   one probe chapter before resuming the queue.
4. Scheduler: `blocked` jobs re-enter automatically when backoff expires; no
   manual action needed for transient bans.

## Why lncrawl-only (no "better way" found)
Options were: raw per-site scrapers (rejected — 338 sites, weekly breakage,
your old NovelCool parser is exactly what died), LNReader-style JS plugin
ecosystem (rejected — second codebase in JS, community maintenance burden for
one dev), lncrawl CLI sidecar (picked — 361 crawlers maintained by others,
JSON output we validate, GPL boundary clean). No fourth option exists at this
scale; the leverage is entirely in the gate + retry + quarantine layer above it.
