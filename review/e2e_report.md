# InkCast live end-to-end audit (report ONLY, no product code changed)

Date: 2026-11-14 ~09:23–09:45 UTC. Host 192.168.29.101. Stack via `docker compose`
(caddy :8085/:8443/:8081, authelia, backend, lncrawl healthy).
**Mid-audit environment change (not by me):** at ~09:28 UTC somebody/something ran
`docker compose up` — caddy restarted (now also publishes :8090, serves the current
disk cert), and `openreader` + `kokoro-tts` containers appeared. Findings below
distinguish pre/post-restart where it matters. Raw logs: `/tmp/e2e_full.log`
(died on restart), `/tmp/e2e_full2.log` (complete: 10 pass / 7 fail), `/tmp/e2e_full3.log`.

## 0. TLS cert reconciliation — RESOLVED (externally, during audit)

- Disk `ops/lan.crt` fp `EC:B0…A7:E5`, SAN `IP 192.168.29.101 + inkcast/auth/read nip.io`
  (regen 09:19 UTC, key matches: modulus `6579150d…` both files).
- Served at audit start: `2A:F4…E9:D1`, SAN **without** `read.*` (old cert, Caddy
  started 09:07 UTC, never reloaded after regen).
- Served now (both :8085 and :8443): `EC:B0…A7:E5` == disk. `diff` of fetched
  certs: identical. **disk == served, nothing to do. Keys untouched.**
- `ops/e2e_live.py` SSL trust fixed (allowed edit): trusts disk CA **plus**
  `/tmp/inkcast-served-*.crt`, so it survives either cert. Verified working
  against both certs during the audit.

## 1. Per-check results (cmd + output; UTC times)

| # | Check | Verdict | Evidence |
|---|-------|---------|----------|
| a | TOC crawl job ever runs | **FAIL (P0)** | Job 1 `novel:1:toc` born 09:24:41 UTC, job 2 `novel:2:toc` born 09:27:06. `sqlite3 … "SELECT id,status,attempts,updated_at FROM jobs"` at 09:31 → both `queued,0`, `updated_at == created_at` (never touched). `GET /api/v1/books/add/status/1` → `{"status":"queued","attempts":0…}` at 6+ min age. Queue-only: nothing in the fleet can transition a job. |
| b | Scheduler tick runner exists | **FAIL (P0)** — no runner | `tick()` defined `backend/app/scheduler.py:172`, zero callers (`grep claim_next(` → only the def; `grep tick` → only tests/docs). `lifespan()` (`main.py:25`) only `db.create_all()`. No BackgroundScheduler/asyncio task/thread anywhere. Live: `/data/db` (via `docker compose exec backend ls`) contains **no** `scheduler.heartbeat` and no `scheduler_state.json`; `GET /api/v1/health` → `"scheduler":"never"` — while top-level `"status":"ok"` (health masks it, P1 aide). |
| c | OpenReader :8090 | **PASS (serving), build done** | Pre-restart: `curl -sk https://…:8090/` → exit 7 connection refused; no container, caddy had no :8090 mapping (stale container). Post-restart: `inkcast-openreader Up`, Next.js log `✓ Ready`; anon `/` → `302` to portal (wall ✓); authed (session cookie) `/` → `200 text/html` Next.js. Phase-2 bridge (chapter-JSON import) not tested. Report-only, untouched. |
| d | UI root Content-Type | **PASS** | `curl -sk -o /dev/null -w` → `http=200 ct=text/html; charset=utf-8`. Body = Vite PWA shell; hashed asset `/assets/index-DsnYClNj.js` → `200 text/javascript` (161 kB). SPA fallback `/nope` → 200 text/html. Portal `/` → 200 text/html. Typo-catcher `:8081` → `302 https://…:8085/`. |
| e | WS bad token rejected | **PASS (fail-closed, wrong layer)** | No cookie → `401 + Location: …:8443/…` (wall). Cookie + `token=WRONG` → `403` (today from StaticFiles close, pre-app-check). Never upgrades (`101` absent in all negative probes). |
| f1 | pron-dict round trip | **PASS** | `PUT /api/v1/pron-dict {rules:[e2eprobe-xyz→eh]}` + token → 200 echo; `GET` → contains rule. Session-only PUT → `401` (by design: PWA sends `Bearer inkcast_token` from localStorage, `frontend/src/lib/search.ts:21`, `sync-client.ts:8`). Test artifact left in live DB (no DELETE endpoint) — see fix list. |
| f2 | voice-map round trip | **PASS for existing novel; FAIL 500 for missing (P1)** | `PUT /books/1/voice-map` + token → 200, `GET` echoes cast. `PUT /books/999…` + token → **500** (`sqlalchemy.exc.IntegrityError: FOREIGN KEY constraint failed` on `voice_map`, backend log) — should be 404. `GET` for missing book → 200 `{}` (inconsistent, P2). |
| g | Backup export + import drill on COPIES | **PASS** | `POST /api/v1/admin/backup` (cookie+token) → 200 `{"path":"/data/backups/inkcast-….bak","sha256":"c354…"}`. No restore route exists (`grep restore|import` in routes → nothing; `import_backup` uncalled by HTTP). Drill via `docker compose exec backend python3` on `/tmp/drill/*` copies: import#1 `{'noop': False,'restored_files':0,'warnings':[]}`, import#2 `{'noop':True,'content-hash already imported'}`; live sha `e59963bc…` identical before/after. |
| h | `GET /api/v1/books`, `/api/v1/jobs` with rows | **FAIL (P0)** | Empty tables → 200 `[]` (masks bug). With ≥1 row → `500 Internal Server Error`, backend: `ValueError: dictionary update sequence element #0 has length 11` in `jsonable_encoder`. Root cause: `main.py:17` uses `sqlalchemy.select` with `sqlmodel.Session.exec` → returns raw Row tuples, unserializable. Affects `list_books`, `list_jobs` (`/api/v1/voices` same pattern, latent until a voice row exists). `routes_*` files correctly use `sqlmodel.select` (OPDS catalog 200 with 2 entries ✓). So: add one novel → book list **and** job list break fleet-wide. |
| i | WS upgrade with valid creds | **FAIL (P0)** | Proper Host + valid session cookie + valid token → `403`, backend log `"WebSocket /api/v1/ws?token=…" 403` + `connection rejected`. Direct-to-backend (bypass Caddy) identical → Caddy innocent. Root cause: `main.py` mounts `StaticFiles` at `/` **before** `@app.websocket("/api/v1/ws")` is registered; `Mount.matches` covers websocket scope and `/` matches everything, so `StaticFiles.__call__` (`staticfiles.py:92-94`: websocket scope → `WebSocketClose()`) kills every handshake before `ws_control` runs. The `Mount LAST` comment is wrong for WS routes. e2e script also fixed: it sent `Host: x` (Caddy empty-200, never routed) — now sends proper Host + cookie. |
| j | Duplicate add (no dedup) | **FAIL (P1)** | Same URL added twice → novels 1 AND 2, identical `source_url`, jobs `novel:1:toc` + `novel:2:toc`. `routes_search` add does no `source_url` lookup. |
| k | OPDS | **PASS + note** | Bad token → 401 `invalid or missing OPDS token` ✓. Valid token (pre-seeded row) → 200 feed, 2 `<entry>` ✓. Note (P2): `create_opds_token` has no HTTP caller — no way to onboard a device; one row pre-seeded. |
| l | Sidecar operability | **PASS** | `docker exec inkcast-lncrawl` works; `/app/.venv/bin/python -m lncrawl --version` OK (exact argv backend uses); `health.lncrawl == "running"` (proves backend→socket). Never exercised with a real crawl — **can't be**: no runner (item a). |
| m | Anon wall (API + 8090) | **PASS (script fixed, not product)** | curl (any UA/header) → `302` portal; Python-urllib → `401 + Location: :8443` (Authelia UA-sniffs; both block). e2e expected strict 302 → amended to accept either (both = walled; 200+JSON would be the failure). `:8090` anon → 302 portal ✓. `Host: x` garbage → Caddy empty `200 Content-Length: 0`, no backend contact, no leak (P2 curiosity). |

Full-script tally (run #3 FINAL, `python3 ops/e2e_live.py`, exit 1):
**16 passed, 4 failed** — `books-authed-200-list` [http=500],
`job-actually-runs` [last=queued], `jobs-accepts-token` [http=500],
`ws-upgrade-with-token` [HTTP/1.1 403 Forbidden].
All four are product bugs (items a/b, h, i); everything else passes, including
`voice-map-put`/`sample-preview`/`progress-push` with token (200),
`ws-rejects-bad-token` (403, fail-closed), `live-search` (35 hits),
`backup-export` (sha256). Run #2 (10/7) used pre-fix expectations; run #1 died
on the external Caddy restart. Logs: `/tmp/e2e_full{,2,3}.log`.

## 2. Severity rollup

- **P0 (blocks real use):** (a) jobs queue forever — no runner/tick loop, so add/search
  results can never become books; (h) book/job lists 500 once rows exist — the UI
  breaks on first successful add; (i) WS control channel 100% dead (route order).
- **P1 (degraded):** voice-map PUT 500 on missing novel (should 404); add creates
  duplicate novels; health reports `ok` while scheduler never ran (masks P0-a).
- **P2 (polish):** GET voice-map 200 `{}` for missing book; no OPDS-token onboarding;
  no pron-rule DELETE (audit probe row `e2eprobe-xyz` left in live DB); `Host: x`
  empty-200 oddity.

## 3. Fix list (max 8, one sentence each)

1. `backend/app/main.py`: register `@app.websocket("/api/v1/ws")` before
   `app.mount("/", StaticFiles…)` (or exclude ws path) so handshakes reach `ws_control`.
2. `backend/app/main.py`: import `select` from `sqlmodel`, not `sqlalchemy`
   (fixes 500s in `list_books`/`list_jobs`/`list_voices` once rows exist).
3. Add the missing job-runner: call `claim_next` + execute crawls and `scheduler.tick()`
   on a loop (lifespan task or sidecar cron) — without this the product ingests nothing.
4. `backend/app/routes_search.py` (`books/add`): look up existing novel by
   `source_url` and reuse/coalesce instead of inserting duplicates.
5. `backend/app/main.py` `put_voice_map`: check novel exists → 404 before insert
   (kills the FK 500); same guard anywhere else inserting child rows.
6. `backend/app/main.py` `health`: report `scheduler: never/stale` as `ok=False`
   (or `status: degraded`) so the dead scheduler pages instead of hiding.
7. `backend/app/routes_catalog.py` (or new admin route): expose `create_opds_token`
   behind API_TOKEN so devices can be onboarded; add pron-rule DELETE.
8. `ops/e2e_live.py`: keep (already fixed this audit: dual-cert trust, UA-aware wall
   check, token-variant voice/progress checks, proper-Host WS check); gate CI on it.

## 4. Audit hygiene

- Product files untouched (`git status`: only pre-existing `M` entries + untracked
  `ops/e2e_live.py`; allowed edits: `ops/e2e_live.py`, this report). No
  up/down/restart/rebuild by me; the ~09:28 UTC `compose up` (caddy/openreader/kokoro)
  was external. No keys deleted. Live DB test artifacts left intentionally and
  documented: novels 1–2 + jobs 1–2 (script's own add flow), pron rule
  `e2eprobe-xyz`, voice cast + progress row on novel 1.

Skipped / not verified: real crawl output quality (impossible — no runner); Phase-2
OpenReader chapter-JSON bridge; Tailscale remote profile; sustained soak past 6 min
per job (structurally moot: no code path can pick a job up).
