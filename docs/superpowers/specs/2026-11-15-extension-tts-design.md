# Web Extension + Docker TTS — design (v2, supersedes LNReader plan)

Date: 2026-11-15. Status: draft, awaiting user approval.
Supersedes: `2026-11-15-lnreader-tts-design.md` (LNReader fork dropped —
no fork, no Expo toolchain, no APK builds).

## Intent and the why

Read light novels on their websites in desktop Chrome, narrated by our
models. A Manifest V3 extension extracts the chapter, a LAN Docker stack
renders audio, the page highlights as it reads. Why this shape: every
hard problem already has a proven answer (Readability extraction, ready-
made Kokoro/Chatterbox servers, MV3 offscreen audio), so the only code
we write is a thin orchestrator and the extension. Walled sources work
for free — the user is already logged in; the page IS the session.

## Architecture

```text
Chrome (MV3)                          Docker host (16GB+ GPU, LAN-open)
content script: Readability extract
  + sentence split + in-page          kokoro service (FastAPI image, CPU):
  highlight + click-to-seek             always warm, 0 VRAM, default model
service worker: preempt queue,
  server calls, settings              chatterbox service (Turbo):
popup: server URL, model/voice/          lazy-load on demand, idle-evict,
  speed global + per-site override     ~2.3GB only while rendering
offscreen doc: gapless audio play     orchestrator (ours, one port):
                                        route model, per-sentence cache
                                        (text-hash+voice+speed+model),
                                        preempt queue, timings manifest
```

- MV3 constraint (verified): service workers have no DOM and cannot play
  audio — playback lives in an offscreen document (`AUDIO_PLAYBACK`).
- Flow: press play → content script sends cleaned sentences → Kokoro
  streams first audio (~300ms on GPU image, fast on CPU too) with word
  timestamps → auto-continues with prefetch; any tap preempts (old render
  pauses/resumes, never blocks the new tap).
- Cache: orchestrator remembers every rendered sentence; repeats are free.
- Voice suite: voice-map + pron-dict + speed, global default + per-site
  override. No cloning ever (Chatterbox stock voices only).
- Proof site: NovelBuddy (hardest case first — bot-fight included).

## Goals / non-goals / anti-goals

- Goals: install extension, type `http://LAN:port`, press play on a
  NovelBuddy chapter, hands-free read-along with highlight + tap-seek.
- Non-goals: Android app, library/offline queue, OPDS/M4B/backup, auth
  wall, lncrawl, OpenReader. (Extension may cache played sentences for
  rereads — builder's call.)
- Anti-goals: GPU hogged at rest (Kokoro CPU + Chatterbox idle-evict, GPU
  empty when idle); taps waiting behind earlier sentences; silent voice
  swaps on model failure (fail loud).

## Left to the builder

- Compose files + image pins (Kokoro-FastAPI CPU, Chatterbox-TTS-Server
  Turbo/GPU), orchestrator routes (propose `POST /v1/render`,
  `GET /v1/audio/{hash}`, `GET /health`), per-sentence Opus params,
  Chatterbox idle-evict mechanism (server-native flag or wrapper).
- Extension file list: manifest, content script, service worker, offscreen
  doc, popup; Readability + splitter + highlight mapping from server
  word/sentence timings.
- First-boot proof: one NovelBuddy chapter end-to-end.
