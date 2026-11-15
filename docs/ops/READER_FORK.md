# READER_FORK — OpenReader phase 1 (stock slim behind our wall)

## Upstream pin

- Repo: https://github.com/richardr1126/openreader
- Tag: v5.4.1 (`reader/.upstream-pin`), commit `7b44de7928983757e4716a318afaac8758434e6c`
- Mode: **stock slim, zero in-tree changes**. `reader/` is a pristine
  `git clone --depth 1 --branch v5.4.1`; the image builds from upstream's own
  `reader/Dockerfile`. No repointing of its internals at our API in phase 1.

## Services (all in `docker-compose.yml`, none publish ports to LAN)

| Service    | Image / build              | Notes |
|------------|----------------------------|-------|
| openreader | `build: ./reader` (Next.js + embedded worker/nats/weed-mini) | `BASE_URL=https://192.168.29.101:8090`, `USE_ANONYMOUS_AUTH_SESSIONS=true`, `COMPUTE_WORKER_PUBLIC_URL=https://192.168.29.101:8090` (same-origin audio, no mixed content), `RUNTIME_SEED_JSON` kokoro provider -> `http://kokoro-tts:8880/v1` (as upstream slim compose). Secrets: `OPENREADER_AUTH_SECRET` (`.env`, see `.env.example`). Leave `COMPUTE_WORKER_URL` unset -> embedded worker. |
| kokoro-tts | `ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.4` (pinned) | No ports published; reached only by openreader over the compose net. ONNX env copied from upstream slim compose. |
| caddy      | `:8090` added               | `https://192.168.29.101:8090` + `https://read.192-168-29-101.nip.io:8090`, same `lan.crt` (regen'd 10y self-signed to add the `read.*` DNS SAN — **reinstall `ops/lan.crt` on devices**). Uniform Authelia `forward_auth` wall on every path incl. `/v1/*`; `/v1/*` -> `openreader:8081` (worker audio, HMAC playback token still required), all else -> `openreader:3003`. No OPDS exemption here. |

Volume: `openreader-docstore` (its sqlite + weed-mini + nats jetstream). Our
backup-ZIP story does NOT cover it in phase 1.

## URLs

- Reader: https://192.168.29.101:8090 (nip fallback: https://read.192-168-29-101.nip.io:8090)
- Wall: Authelia `one_factor` on both (verified `access-control check-policy`).
- Anon `curl https://192.168.29.101:8090/` -> 302 to portal; authed -> 200 app.

## Proof (phase 1 acceptance) — executed 2026-11-14 ~09:20–09:45 UTC

Wall (all via `curl --cacert ops/lan.crt`):
- `GET https://192.168.29.101:8090/` anon -> **302** to
  `https://192.168.29.101:8443/?rd=...` (portal). No content leaks pre-login.
- `POST https://192.168.29.101:8443/api/firstfactor`
  (user `reader`, 30-day `keepMeLoggedIn`) -> `{"status":"OK"}`; same jar
  `GET /` -> **200** (Next.js app shell). Nip URL behaves identically.
- `access-control check-policy --url https://192.168.29.101:8090/` -> **one_factor**
  (rule #1 hit); same for `https://read.192-168-29-101.nip.io:8090/`.

Read-along (authed jar + app anonymous session
`POST /api/auth/sign-in/anonymous` with `Origin` header):
- `GET /api/gutenberg/books?search=alice&language=en` -> 283 hits, id 11 first.
- `POST /api/gutenberg/import {id:11}` -> temp token (server downloads the
  EPUB itself; 189,249 bytes, illustrated edition).
- `POST /api/documents/blob/upload/finalize {uploads:[...]}` ->
  `stored.id 4f81245d…f849a4f784036` ("Alice's Adventures in Wonderland.epub").
- `POST /api/documents/<id>/reader-bootstrap` -> `pending`, then `ready`
  (~20 s: EPUB parse + 965-segment plan, `planId bfd6ada1-…`, voice `af_alloy`
  via `kokoro` custom-openai provider -> `kokoro-tts:8880`).
- `POST /api/tts/stream/sessions {documentId,settings,planning,
  startIntent:{selectedOrdinal:0},planId,planObjectKey}` -> 202, `sessionId
  tts-live-17e756f8…`, `audioUrl https://192.168.29.101:8090/v1/tts-playback/
  sessions/<id>/audio?token=…` (**same-origin**: served through Caddy `/v1/*`
  -> `openreader:8081`).
- `POST .../cursor {ordinal:0, sessionInstanceId, playbackActive:true}` ->
  `workerOpId d4127459-…` (before any cursor the worker idles: "Playback
  session has no worker operation yet" — cursor heartbeat is the ignition).
- Timeline SSE state: `queued` (pending/estimated) -> `running`
  (38 segments `ready`/`exact` in ~45 s, first job `succeeded` in 70 s compute).
- Audio: `GET $audioUrl -r 0-200000` -> **HTTP 206**, `content-type:
  audio/mpeg`, 200,001 bytes, `file` = MPEG ADTS layer III 44.1 kHz mono.
  Kokoro synth confirmed end-to-end through wall + Caddy + worker.
- Word highlight: `timeline?minOrdinal=0&limit=5&rows=window` seg0
  `alignment.words[0..2]` = The(0.32-0.78s, chars 0-3),
  project(0.78-1.18s), Gutenberg(1.18-1.58s) — Whisper `tiny.en` backfill
  (alignments arrive ~1 job behind audio; first run had `alignment:null`
  until the whisper model + ordered alignment lane caught up on the next
  cursor run — expected per `segment-generation.ts` audio-first design).

Timings (LAN, 12c/30GB host, cold caches incl. kokoro model already warm):
clone ~10 s; image build ~4 min; Next ready + worker ready ~10 s after start;
bootstrap ready ~20 s; first 38 segs ~45 s after cursor; 206 audio immediate
thereafter. Full 965-seg novel = hours of audio; ahead-window generation is by
design (refill follows cursor), not a defect.

Repro (cookies in /tmp/orjar, cert ops/lan.crt): login (§Wall) ->
`sign-in/anonymous` (+Origin) -> import id 11 -> finalize -> bootstrap (poll
to ready) -> sessions -> cursor(playbackActive) -> timeline/audioUrl.

## Phase 2 bridge (NOT done — explicit follow-ups)

1. Our chapter-JSON novels -> their document import API (push EPUB/TXT or drive
   their upload endpoint; decide bulk vs lazy per-novel).
2. Voice-map parity: our per-novel voice settings -> their provider/voice model
   (slug mapping + default).__SLOT__3. Library sync direction (our DB source of truth? read progress back?).
4. Backup story for `openreader-docstore` (or accept re-synth).
5. Decide: keep two libraries (ours + theirs) or converge on one reader UI.
