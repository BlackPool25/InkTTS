# Crawl error lifecycle + refetch (locked)

Principle: **failed content never touches chapters.** Validate-then-commit in
one transaction: content gate runs on lncrawl artifact BEFORE any row write.
Failure = `jobs` row with error class, never a chapter row.

```mermaid
flowchart TD
  X[docker exec crawl] --> Z[artifact zip]
  Z --> G{content gate}
  G -->|pass| C[write chapters + enqueue synth]
  G -->|blocked CF/403| B[blocked: backoff 2^n cap 48h, health++]
  G -->|empty| E[empty: same + quarantine 7d]
  G -->|parse shape drift| P[parse: FAIL LOUD, no retry]
  Z -->|timeout/DNS| T[transient: retry x3 then blocked]
  B --> Q{N consec?}
  Q -->|yes| SQ[source quarantined + UI flag]
  Q -->|no| W[requeue at backoff]
```

## Refetch (explicit buttons)

```mermaid
flowchart LR
  RC[per-chapter refetch<br/>bypass hash, re-gate, re-synth] --> C2[chapter updated]
  RN[per-novel repair<br/>TOC diff, gaps+flagged only] --> C2
  RS[per-source unquarantine<br/>reset backoff + probe 1 ch] --> Q2[queue resumes]
```

- Per-job artifact dirs (no zip races); parse zips only, never stdout.
- Exec timeout -> SIGKILL tied to job lease; boot sweep of stale tmp.
- Serialize challenge-solves (max_instances=1/origin) or per-job profiles.
