# LNReader + Server TTS (thin client) — design

Date: 2026-11-15. Status: draft, awaiting user approval.
Approach picked by user: **thin client** (2026-11-15).

## Intent and the why

Scrap InkCast's crawler, web UI, OpenReader fork, Authelia/Caddy wall,
OPDS/M4B/backup-ZIP. New shape: a fork of BlackPool25 `lnreader`
(Expo/React Native Android app, plugin ecosystem stays as the only source
of chapters) plus a single Docker TTS service on the LAN. The phone app
becomes a thin HTTP client for audio: it sends a plugin URL + sentence
range + voice settings, the server returns per-sentence Opus + timing so
the reader keeps sentence-highlight read-along. Why: LNReader's readers,
library, and per-source plugins already work and already rot slower than
anything one dev maintains; our edge was never fetching, it was
high-quality server TTS (Kokoro fidelity + now Chatterbox expressiveness)
with instant seeks.

## Goals

- Fork BlackPool25 `lnreader` at a pinned commit; app builds and runs as
  before with zero behavior change when the TTS server is unreachable
  (falls back to existing on-device `nitro-tts` path).
- Docker TTS service on LAN: Kokoro-82M ONNX (default) + Chatterbox
  (expressive), GPU-backed, direct-LAN HTTP, no login wall.
- Flow: app sends plugin URL + sentence range → server refetches text via
  plugin logic (Node sidecar), caches text locally → synths per-sentence
  Opus, caches audio keyed by (url, text hash, voice, speed, model) →
  app streams gaplessly with sentence highlight from the timing manifest.
- Seek rule: a new tap **preempts** (old render pauses, resumes after);
  the requested sentence renders first and streams fast, the rest
  backfills progressively — nobody waits through sentences 1–9 to hear 10.
- Model choice is a setting in the app: global default + per-novel
  override (Kokoro vs Chatterbox).
- Full voice suite survives: per-novel voice-map, pron-dict, speed.
  No cloning ever (both models use stock voices only).

## Non-goals

- No PC reader (Android only). No OPDS, M4B export, backup-ZIP, Caddy,
  Authelia, lncrawl, scheduler, gates, OpenReader fork.
- No on-device Kokoro, no native-module rewrite of `nitro-tts`.
- No second timing model: one timing manifest shape (our v2 positional
  `sent_id`, timings measured from shipped bytes).

## Anti-goals (failure even if it works)

- Seeking feels sluggish (renders from chapter start blocking the tapped
  sentence).
- Library-wide re-render storms on voice/model change (pinned render keys
  per sentence stay: same text+voice+speed+model never re-synths).
- Server refetch hammers sources (text cache must make repeat plays free).

## Constraints

- Locked from before: Kokoro-82M ONNX CPU path as-is (phoneme→vocab→style
  vectors, Opus 32k mono 24kHz, session recycle ~20); `is_stub` logging;
  MP: single SQLite-file lessons don't apply — server cache is content
  keyed, app library stays LNReader's on-device DB.
- Chatterbox runs on the host GPU in Docker; Kokoro keeps working if the
  GPU path is down (models must fail independently and loudly, never
  silently swap voices).
- LAN-only bind, no auth wall (user call) — document that the tradeoff is
  network-trust, same as a printer.
- LNReader repo rules for app-side changes: `pnpm run check`
  (format+lint+type-check) green, path aliases (`@services/*` etc.),
  `en/strings.json` is the only hand-edited locale, tests only for
  user-facing behavior + core logic.
- Our repo rule: every commit `git commit -s`.

## Approaches considered

1. **Thin client (picked).** App TTS tab becomes an HTTP client to the LAN
   server; server owns fetch + both models + priority queue + caches.
   For: smallest app diff, reuses our Kokoro pipeline almost untouched,
   no native risk. Against: first play of a sentence needs LAN.
2. **Native session replacement.** Same UI contract, remote synth behind a
   `TtsSession`-shaped object. For: cleaner long-term code. Against:
   touches Nitro/Kotlin/Swift, slowest payoff, biggest regression risk.
3. **Bundled text.** App always sends full chapter text; server never
   fetches, no Node sidecar. For: no JS-on-server problem. Against:
   contradicts the refetch-and-cache decision, resends text constantly.
   Rejected for that reason.

## What the user decided (locked)

- Base: BlackPool25 `lnreader` fork, pinned commit.
- Backend becomes TTS-only: single Docker service (Kokoro + Chatterbox +
  plugin fetch + text/audio cache). Dropped: crawl scheduler, gates,
  wall, OPDS, M4B, backup-ZIP, OpenReader fork, PWA.
- Text path: client sends plugin chapter URL + tapped range; server
  refetches via plugin logic (Node sidecar running the JS plugins),
  caches text locally for repeats.
- Audio shape: per-sentence Opus + timing manifest; gapless app-side
  stitch; server + device caches (offline replays free).
- Preemption: newest tap renders first; older renders pause/resume, never
  cancel-and-lose work, never block the new tap.
- Model setting: global default + per-novel override, in-app.
- Voice suite: voice-map + pron-dict + speed survive; 54 Kokoro voices;
  Chatterbox stock voices only, zero cloning/reference uploads.
- Server remembers (content-keyed audio+text cache); app holds library +
  offline audio.
- Compose: one TTS service; status/health endpoint included.
- Targets: Android only. GPU present on host.

## Left to the builder

- Proof target: builder proposes one public English plugin novel+chapter
  before building; user confirms (user chose "you name it").
- Walled sources: server reuses the app's plugin-ecosystem path — same
  plugin logic sidecar-side, app forwards whatever session/cookies the
  plugin flow needs (user: "use the same"). Public-only is NOT the
  fallback; credential pass-through is in scope for v1.
- Language: English only v1; non-English fails loud until later.

- Single image (Python+Node+CUDA) vs two containers (tts + plugin
  sidecar) in one compose — builder picks, bias to one image if the
  plugin runner stays small.
- Exact HTTP routes (propose: `POST /v1/render {url, range, voice, speed,
  model}`, `GET /v1/audio/{hash}.opus` with Range/206 + immutable ETag,
  `GET /v1/timings/{hash}.json`, `GET /health`), cache key + eviction cap.
- Chatterbox model id/variant + per-sentence encode params matching
  Kokoro's Opus shape.
- Preemptive scheduler design (per-sentence jobs, priority = recency,
  pause/resume, coalesce duplicate requests).
- App diff list: new `@services/ttsServer/*` client, TTS tab settings
  (server URL, model global/per-novel, voice, speed), highlight wiring
  from server timings, offline cache policy, fallback when server down.
- Fork pin commit + first-boot proof (render one chapter from a real
  plugin URL through the server into the app).
