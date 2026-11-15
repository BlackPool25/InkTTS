# Extension UX Design (complete) — InkCast Read-Along

Date: 2026-11-15. Status: draft, awaiting user approval.
Supersedes: all prior implicit UI decisions. Nothing here is built yet.

## Intent

One extension, three surfaces with zero overlap:
- **Overlay** (in-page takeover): reading + listening. Everything about the
  *current chapter* lives here and only here.
- **Popup** (toolbar card): transport only — play/pause + server status.
  No settings beyond the server URL + Test connection (needed once).
- **Options page** (`chrome://extensions` → Details → Extension options):
  ALL settings live here. The popup never duplicates them.

## Entry & trigger

- Clicking the toolbar icon toggles the overlay (extract → render takeover).
  No auto-open, no auto-play: the user is mid-reading when they click.
- First run with no server URL: overlay opens in a **setup state**
  ("Paste your LAN URL → Test connection → Play"), not a silent no-op.
- `Esc` or Close always exits to the exact prior scroll position.

## Overlay states (one visible at a time)

1. `reading` — sentences, active highlighted, dimmed neighbors, progress
   `i / n`, bar: Focus | Aa (karaoke) | Theme | Close.
2. `immersive` — toggle, not a separate screen: prev/current/next only,
   everything else hidden. OFF by default. Same controls stay visible.
3. `loading` — skeleton lines + "Rendering with <Voice>…" + cancel.
   Appears on first play and on seeks past the render frontier.
4. `paused` — dimmed play affordance over the frozen highlight.
5. `error:<kind>` — one full-overlay card per failure, each with copy +
   action: `empty` / `boilerplate` ("Open a chapter page"), `challenge`
   ("Solve the check on the page, then [Retry]" — overlay auto-minimizes
   so the checkbox is clickable, retry re-runs extraction), `image-only`
   ("Scanned pages — OCR not supported"), `unreachable` ("Server URL wrong
   or host asleep — [Open settings] [Retry]"), `audio-404` (auto re-queues,
   shows "Re-rendering…" not an error unless it repeats).
6. `degraded` — NOT a state: a persistent badge ("Backup voice — GPU
   offline") over whatever state is active. Never a silent voice swap.
7. `endcard` — "Finished — [Play next chapter]" (uses extracted next URL)
   or "Finished." Stop-at-end default; autoplay is an options toggle.

## Highlight & motion contract

- Active sentence: large + full-white + ~1.05 scale; neighbors opacity
  ladder by focus span (1/3/5). Only `opacity/transform/filter` animate
  (GPU compositing, no layout per frame).
- Karaoke fill is a left-to-right gradient wipe on the active line driven
  by word timestamps; missing word timings → line-sweep. Default OFF
  (sentence highlight default, per locked decision).
- Auto-scroll anchors the active line at ~40% viewport with smooth-follow;
  user wheel-scroll pauses follow for 3s, never fights the reader.
- Tap = seek to that sentence (restart it). Keyboard: space play/pause,
  arrows prev/next, Esc exit. Tap targets get generous padding.

## Themes (4, all full-contrast)

Dark (default, pure-black takeover), Ambient (blurred indigo gradient),
Sepia, Light. Theme affects overlay only, never the underlying page
(untouched DOM, restored on exit).

## Popup contract (transport only)

Server URL field + Test connection (explicit states: ok / DNS / timeout /
refused / wrong-service) + Play / Pause + "All settings →" link opening
the options page. Popup shows a one-line status mirroring the player
(playing chapter title + sentence i/n, or the current error in words).

## Options page contract (all settings, grouped)

1. **Server** — URL + Test connection (mirrors popup; single source of
   truth in storage).
2. **Voice** — global voice, speed, model (Kokoro CPU / Chatterbox GPU);
   per-site overrides table (domain → voice/model).
3. **Reading** — theme swatches, karaoke on/off, focus span, immersive
   on open, autoplay next chapter.
4. **Pronunciation** — `pattern => say-as` lines with preview count.
5. **Extraction** — site-rule JSON overrides + "reset to defaults".
Every section saves explicitly with confirmation; invalid JSON blocks
save with the offending line named.

## Failure copy (never silent, never technical)

- No TTS for a sentence after retries → skip it with an inline marker,
  keep playing; note count at chapter end ("3 sentences skipped").
- Server unreachable mid-chapter → pause + banner + Retry (position kept).
- GPU down mid-chapter → degraded badge + continue on Kokoro.
- Settings change mid-playback → pause + re-manifest + resume at the
  same sentence (never stale audio).

## Open questions for the user

1. Popup: transport + server URL only (this design), or strip it further
   to just Play/Pause with setup living entirely in options?
2. Autoplay next chapter default on or off?
3. On a bot-check page, should the overlay auto-retry extraction a few
   times after the challenge clears, or only on manual Retry?
