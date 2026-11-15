# InkCast Architecture (v2: GGUF read-along)

Single-user LAN system. Chrome extension (presentation + extraction) talks
to one Docker stack (fetch-nothing, synth-everything). Text flows one way
— page → orchestrator → engines → audio + timings → page.

```text
Chapter page ──extract──▶ content script ──POST /manifest──▶ orchestrator
                                                              ├─ queue (tap > next > prefetch + age, per-doc, 1+1 lanes)
                                                              ├─ textpipe (split/normalize/pron-dict/gates/budget)
                                                              ├─ cache (SQLite WAL + sharded .opus, canonical key)
                                                              ├─ render (Kokoro server / Chatterbox server, breaker)
                                                              └─ SSE /events + GET /audio + Enhanced-LRC
Content ◀── overlay (highlight/seek/modes) ◀── offscreen (SSE+prefetch+WebAudio)
```

## Services (`tts-stack/docker-compose.yml`)

| Service | Image | Role | GPU |
|---|---|---|---|
| kokoro | `inkcast-crisp` (CrispASR Vulkan) | Kokoro-82M Q8 server, `--voice` default + `--voice-dir`, always warm | CPU |
| chatterbox | `inkcast-crisp` | Chatterbox Turbo Q8 server, `FORCE_GPU=1` | Vulkan `/dev/dri` |
| orchestrator | built `orchestrator/` | queue/cache/LRC/SSE/OpenAI shim, only LAN-open port (`${ORCH_PORT:-8095}`) | none |
| cleaner | llama.cpp server (profile-gated, opt-in) | LLM semantic-clean for gate-tripped chapters only | none |

CrispASR image (`crisp/Dockerfile`): Ubuntu + Mesa Vulkan + prebuilt binary;
models auto-download once into the `crisp-models` volume. Both engines OpenAI-
compatible (`POST /v1/audio/speech`); orchestrator treats them as HTTP
backends keyed by `engine_tag` in the cache key.

## Orchestrator internals

- **Queue** (`queue.py`): heap `(prio, seq)` per doc; tap=0 preempts, next=1,
  prefetch=2+d; every 4th pop forces oldest prefetch (no starvation);
  seek bumps epoch (drops out-of-window jobs of that doc only); TTL purge +
  doc close. 1 Kokoro + 1 Chatterbox in flight max.
- **Cache** (`cache.py`): key = `sha1(norm_text|engine_tag|voice|speed_q|dict_ver|pipe_ver)`;
  2-level sharded `.opus`, atomic tmp+rename+fsync; thread-safe (lock);
  LRU evict 10% over cap; live-window pins; audio-miss re-queues with
  `rendering` re-emit (never 404-stalls); ENOSPC bypass (serve uncached).
- **Render** (`render.py`): retries 2× backoff, per-sentence timeout, poison
  (>400 chars/empty); circuit breaker (3 consecutive Chatterbox failures →
  chapter-sticky Kokoro + single `degraded` notice); voice mapping
  (`af_heart` → `kokoro-voice-af_heart.gguf`; Chatterbox = server default).
- **Text** (`textpipe.py`): abbrev-safe split, CJK boundaries, 25s cap;
  normalise (numbers/abbrevs/acronyms via `pron.py`), TN strip, gates
  (min-length 20, Turnstile/challenge hard-block, language-ratio warn);
  ≤200-char/≤40-word chunks; Enhanced-LRC emitter (word tags Kokoro,
  plain lines Chatterbox).
- **API** (`api.py`): `POST /manifest|/tap`, `GET /audio/{hash}` (Range +
  immutable ETag), `GET /events?doc_id=` (bounded 200-event ring, resync on
  gap, terminal `complete`), `POST /v1/audio/speech` shim, `/health`
  (ready/warming/degraded). CORS open for the extension origin.
- **Worker** (`server.py`): daemon thread drains queue → render → store →
  emit; cache lookups short-circuit before render.

## Extension internals (`extension/`)

WXT + TypeScript + Preact. MV3 hard constraints drive the shape: service
workers die ~30s idle and cannot play audio or hold SSE — so the **offscreen
document** owns SSE + prefetch + playback (Web Audio `AudioBufferSourceNode`
chaining, not `<audio>` tags); the **worker** is a thin relay + alarms;
**content scripts** never fetch LAN (mixed-content) — all server calls go
through worker/offscreen.

- `content/extract.ts`: challenge check (Turnstile iframe/title signals) →
  site-rule container first → Readability on cloned doc → strip ads/comments
  → promo-regex sweep → next-page stitch (cap 5, URL discipline, per-page
  challenge aborts) → loud gates. DOM-order paragraphs; title excluded.
- `content/overlay.tsx` + `mount.tsx`: Shadow-DOM takeover, ambient-blur
  themes, smooth-follow 40% anchor, 750ms scroll cooldown, keyboard
  (space/arrows/Esc), per-error overlays, end card, degraded badge.
- `content/sync.ts`: Enhanced-LRC parse → binary-search anchor + interpolated
  word fill (sustain-not-gap, 80ms minimum), rate-scaled lookups, line-sweep
  fallback, seek = file start (no intra-file word offsets).
- `offscreen/player.ts`: SSE subscribe, prefetch K=3, Range-resume by
  {hash,idx,offsetMs}, `degraded`/`complete` events, rate 0.8–2.5x.
- `popup/` + `options/`: transport + Test-connection + setup hint; full
  settings (voice/speed/model, themes, karaoke, focus, immersive default,
  per-site JSON, pron-dict, site-rule overrides) with save + notify.
- Storage: `storage.local` (+unlimitedStorage) for rules/pron/cache-index;
  `storage.session` for ephemeral playhead. Never blob URLs across restarts.

## Data & safety

- No accounts, no sync server, no wall: LAN-open plain HTTP (network-trust,
  like a printer). Extension validates server URL via Test-connection with
  distinct DNS/timeout/refused errors.
- Disk: 5 GB cache cap default (LRU); ~25 KB per Kokoro sentence; models
  volume separate from cache. `update.sh` = pull + rebuild + print LAN URL.
- Failure posture: engines fail loudly and independently (never silent voice
  swaps); challenge/image/empty pages fail loud in-page; GPU loss degrades
  Chatterbox to Kokoro with a persistent badge.
