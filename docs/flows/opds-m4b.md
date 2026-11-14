# OPDS catalog + M4B export

Why OPDS 1.2: KOReader speaks it natively — one token URL gives the e-reader
the whole library with no login. Why M4B: a single AAC audiobook file with
chapters for listeners who don't want the PWA.

```mermaid
sequenceDiagram
  participant K as KOReader
  participant C as Caddy :80
  participant F as FastAPI :8000
  K->>C: GET /api/v1/opds/catalog.xml?token=DEV
  C->>F: proxy (token-exempt path)
  F->>F: verify opds_tokens row
  F-->>K: 200 OPDS 1.2 XML (else 401)
  K->>C: GET /api/v1/opds/search?q=...&token=DEV
  F-->>K: 200 OPDS XML results
  K->>C: GET /api/v1/opds/books/3.m4b?token=DEV
  F->>F: concat chapter Opus -> AAC-LC 64k mono + ffmetadata
  F-->>K: 200 audio/mp4 download
```

M4B chapter math: per-chapter durations from sidecars, ffmetadata `START/END`
in ms. Encoder priming adds ~43ms of leading silence, so chapters 2..N shift
+43ms to stay contiguous (`END[i]==START[i+1]`, locked by
`test_m4b_chapter_math`); chapter[0].START clamps to media start (0) — known
muxer behavior, not a bug. Live round-trip covered by
`test_m4b_live_encode_roundtrip` (skipped when ffmpeg absent).

Cold chapters (no audio yet) are skipped from the M4B with the gap noted;
enqueue synth first via `POST .../synthesize` if the book is incomplete.
