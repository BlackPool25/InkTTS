# Locked Decisions (user ballots 2026-11-14 — source of truth)

1. Users: single user + premade login wall (Authelia sidecar + Caddy forward-auth; zero auth code). 30-day sessions.
2. Sources: lncrawl Docker sidecar ONLY (NovelCool scraping removed). Pinned ghcr image, idles; backend `docker exec` jobs; artifacts via shared volume (builder confirms data-dir path). Scheduler ours; server mode unused. Chromium baked in for built-in challenge solver.
3. Anti-bot: tiered built-ins only (curl_cffi impersonation → built-in browser solver → quarantine). No FlareSolverr.
4. Scheduler: 6h+jitter hardened daemon (jobs-table truth, atomic claim+lease, coalesce, max_instances=1, misfire 1800s, boot stagger, per-host caps).
5. Quality gate: validate-then-commit; failures quarantine + flag, never overwrite good bytes. Refetch: per-chapter, per-novel repair, per-source unquarantine (see 27_refetch_lifecycle.md).
6. TTS: Kokoro-82M ONNX only (54 fixed voices; cast + pron-dict, no cloning). GPU/Chatterbox deferred to expressiveness trigger.
7. Audio: per-chapter Opus 32k mono 24kHz, hash-in-URL, immutable, sharded dirs. M4B export on demand.
8. Sentences: positional IDs + full-chapter re-synth on text change (no diff). Timings measured from shipped bytes.
9. Caching: PWA CacheStorage 500MB LRU + backend disk cap + HTTP semantics; prefetch N+1. Queue: SQLite jobs table, no broker.
10. Progress: sentence granularity; server-time LWW + 5s flap; field-level merge in backups.
11. Sync: portable backup file (ZIP: library.db + sidecars + covers, no audio, age-encrypted by default). Merge verified in staging. No accounts, no sync server.
12. Novel identity: title+author fingerprint + URL aliases, join UI.
13. Client: web UI only (Flutter deleted) — fork OpenReader (MIT). Wire library → /api/v1, narration → Opus+manifest. Fallback: Chapter → minimal reader.
14. Playback: tab leader election; sleep timer cancels prefetch; pagehide snapshots; UTC everywhere.
15. Transport: HTTP Range for bytes + manifest; WS control-plane only (or HTTP poll — builder's call).
16. OPDS 1.2 + M4B export in v1; OPDS via per-device token exemption (readers can't log in).
17. Stack: Python/FastAPI, SQLite single-file (PG deferred), single compose (caddy + authelia + backend + lncrawl sidecar; tailscale optional profile).
18. Voice/speed/model changes: pin render keys, lazy re-render + explicit button (no library-wide storms).
19. LAN: plain port default + optional Tailscale. Host: desktop/laptop. Targets: Android + PC. Narrator voice: user ABX at build.
20. Docker: backend image ships ffmpeg + espeak-ng; socket mount accepted (documented); OMP threads budgeted.

## Amendments (live-boot 2026-11-14, forced by implementation reality)
21. LAN is https via Caddy `tls internal` + nip.io names (Authelia 4.39 validator
    rejects non-https authelia_url/redirection — plain-http LAN is unsupported).
    One-time CA install per device. DHCP IP change = 3 one-line edits (Caddyfile,
    authelia-config, .env). Tailscale profile unchanged.
22. lncrawl exec uses the venv interpreter (`/app/.venv/bin/python -m lncrawl`);
    system python lacks deps. Sidecar data path is `/data` (4.14.0 ENV).
23. New crawl error class `impassable` (L19 identity-provider gate): terminal per
    novel, no retries, no source quarantine. Observed live on search egress.
