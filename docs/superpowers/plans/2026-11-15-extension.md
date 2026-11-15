# Read-Along Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Manifest V3 Chrome extension that extracts novel chapters in-page, plays server-rendered audio with card-lyrics highlight, and seeks by tap — against the TTS stack plan's orchestrator.

**Architecture:** WXT + Preact overlay in Shadow DOM (content script), service worker as stateless router, offscreen document for gapless audio, popup for transport + server URL, options for voices/per-site rules. Content script never fetches LAN (mixed-content); worker/offscreen own all server calls.

**Tech Stack:** WXT (Vite), TypeScript, Preact, `liricle`-style Enhanced-LRC parse (MIT), `@mozilla/readability`, vitest.

**Spec:** `docs/superpowers/specs/2026-11-15-extension-tts-design.md`

## Global Constraints

- `en`-only strings to start; MV3 `manifest.json`, no remote-executed code (LAN fetch is data only).
- Permissions minimal: `storage, scripting, activeTab, offscreen` + `<all_urls>` (locked call).
- Every commit `git commit -s`.
- Unlisted CWS listing; immersive toggle OFF by default; TNs dropped; stop at chapter end; OCR out of scope.

## Review Focus

- HTTPS novel page → `http://LAN:port` fetch from content script → must NOT attempt (mixed-content); all LAN calls from worker/offscreen only (test in Task 5).
- Cloudflare challenge page → overlay banner "solve it, playback paused", never narrates challenge text (test in Task 2).
- Service worker killed mid-playback → state rehydrates from `chrome.storage`, audio continues via offscreen doc (test in Task 5).
- Long chapter (500 sentences) overlay → transform/opacity animation only, no scroll jank (manual check in Task 3).
- Seek storm (tap 3 sentences in 1s) → last tap wins, no overlapping audio (test in Task 4).

---

### Task 1: WXT scaffold that builds

**Files:**
- Create: `extension/` (WXT scaffold: `wxt.config.ts`, `entrypoints/{background,content,offscreen,popup,options}`, `package.json`)
- Test: `pnpm build` + `pnpm typecheck`.

**Interfaces:**
- Consumes: nothing.
- Produces: loadable unpacked extension, empty popup/options pages, `host_permissions` covering the LAN URL pattern, storage contract: `storage.local` (+`unlimitedStorage`) for rules/pron-dict/cache-index, `storage.session` for ephemeral `{hash, idx, offsetMs}` (never blob URLs, never `sync` for large data).

**Interfaces:**
- Consumes: nothing.
- Produces: loadable unpacked extension, empty popup/options pages, `http://*` host perms.

- [ ] **Step 1: Scaffold** — Run: `pnpm dlx wxt@latest init` (or manual entries) with TypeScript + Preact; entries: background worker, content script (`matches https://*/*`, `run_at document_idle`), offscreen page, popup, options.
- [ ] **Step 2: Verify** — Run: `pnpm install && pnpm build && pnpm typecheck`. Expected: dist/ builds clean.
- [ ] **Step 3: Commit** — `git add extension && git commit -s -m "feat: WXT extension scaffold"`.

### Task 2: Extraction pipeline (Readability + rules + loud gates)

**Files:**
- Create: `extension/entrypoints/content/extract.ts`, `site-rules.ts`
- Test: `extension/tests/extract.test.ts` (vitest, saved fixture HTML: novel page, challenge page, comments-heavy page).

**Interfaces:**
- Consumes: nothing.
- Produces: `extractChapter(doc) -> {title, paragraphs[], nextUrl} | {error: 'empty'|'boilerplate'|'challenge'|'image-only'}`; pipeline: **challenge check (Turnstile signals) → site-rule container FIRST → Readability scoped to that subtree (never whole-doc fallback)** → pre-strip ads/comments → promo-regex sweep → next-page stitch (cap 5, `new URL(href,base)`, visited-set, same-chapter-slug guard, per-page challenge gate aborts whole stitch) → gates. Settle-wait (child-count stable N×500ms) + re-extract at press-play with count diff; output sorted to strict DOM order; title node excluded from paragraphs; block-level TN/promo strip only (never inline-substring-delete); rule version + hit/fallback counter; soft-nav (`onHistoryStateUpdated`) re-extracts + tears down overlay.

- [ ] **Step 1: Write the failing test** — fixture novel page → paragraphs without nav/comments/TN; promo-in-container fixture → zero promo sentences; interleaved-ad fixture → strict DOM order; challenge fixture (**real Turnstile HTML**) → `{error:'challenge'}`; image-only fixture → `{error:'image-only'}`; short page → `{error:'empty'}`; dialogue with "read more"/"(she thought…)" survives; `paragraphs[0] !== title`; broken-selectors fixture → Readability fallback path + counter increments.
- [ ] **Step 3: Implement `extract.ts` + NovelBuddy-first `site-rules.ts`** — domain-keyed `{match, content[], strip[], challenge[]}`, NovelBuddy selectors verified live at build time; MutationObserver watches challenge-node removal → auto re-run + clear `PAGE_BLOCKED`.
- [ ] **Step 2: Run it** — Run: `pnpm vitest run tests/extract.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement `extract.ts` + NovelBuddy-first `site-rules.ts`** — domain-keyed `{match, content[], strip[], challenge[]}`, NovelBuddy selectors verified live at build time.
- [ ] **Step 4: Run it** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -s -m "feat: chapter extraction with loud gates"`.

### Task 3: Card-lyrics overlay (takeover + immersive toggle)

**Files:**
- Create: `extension/entrypoints/content/overlay.tsx`, `themes.css`
- Test: `extension/tests/overlay.test.ts` (render states) + manual NovelBuddy check.

**Interfaces:**
- Consumes: Task 2 `extractChapter`.
- Produces: `<Overlay sentences[], activeIdx, immersive, theme, focusSpan, onSeek>`; full-takeover fixed layer, ambient-blur bg, Esc restores scroll; immersive = prev/current/next only, OFF default; themes dark/ambient/sepia/light; focus 1/3/5. Overlay owns immersive/theme/focus/karaoke controls inline (reachable without leaving the page). One error overlay per `extractChapter` error code with distinct copy + exit action. End card on `complete`: `Finished — [Play next chapter]` (uses extracted `nextUrl`).

- [ ] **Step 1: Write the failing test** — renders active sentence white/promoted, neighbors dimmed; immersive hides all but ±1; Esc calls `onExit`; theme switch changes root class; each error code renders its copy; end card renders `nextUrl` CTA.
- [ ] **Step 4: Run it** — vitest PASS + manual screenshot on NovelBuddy fixture. Blocked state auto-minimizes overlay with `[Retry]` that re-runs extraction (challenge→solve→retry round-trip tested, not just the fixture return).

- [ ] **Step 1: Write the failing test** — renders active sentence white/promoted, neighbors dimmed; immersive hides all but ±1; Esc calls `onExit`; theme switch changes root class.
- [ ] **Step 2: Run it** — Expected: FAIL.
- [ ] **Step 3: Implement `overlay.tsx`** — Shadow DOM mount, transform/opacity-only animation, smooth-follow to 40% anchor, 750ms user-scroll cooldown, keyboard (space/arrows/Esc).
- [ ] **Step 4: Run it** — vitest PASS + manual screenshot on NovelBuddy fixture.
- [ ] **Step 5: Commit** — `git commit -s -m "feat: card-lyrics overlay with immersive toggle"`.

### Task 4: Sync engine (LRC → highlight + tap seek)

**Files:**
- Create: `extension/entrypoints/content/sync.ts`
- Test: `extension/tests/sync.test.ts`.

**Interfaces:**
- Consumes: Task 3 overlay; orchestrator Enhanced-LRC (sentence lines, Kokoro word tags, Chatterbox plain).
- Produces: sync clock lives in **offscreen/player** (it owns the `<audio>` clock, not the content script); overlay receives `lineAt`-level pushes (no per-frame message round-trips). `createSync(lrcText, rate)` with rate-scaled lookups; binary-search anchor, sustain-not-gap interpolation, 80ms visual minimum, 300ms seek-early compensation, synthetic flag → line-sweep. **Word tap = sentence restart** (no intra-file word offsets; `wordIdx` param dropped from the seek contract).

- [ ] **Step 1: Write the failing test** — LRC with word tags → `wordFill` mid-word ≈ 0.5 at 1.0x; rate 1.5x scales lookups (same audio instant maps further into LRC); gap between words holds prior fill (never blank); plain line → line-sweep progress; seek for sentence 10 = its file start.
- [ ] **Step 2: Run it** — Expected: FAIL.
- [ ] **Step 3: Implement `sync.ts`** — rAF loop reading `audio.currentTime` once per frame, change-gated style writes.
- [ ] **Step 4: Run it** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -s -m "feat: LRC sync engine with tap seek"`.

### Task 5: Worker + offscreen audio + popup/options

**Files:**
- Create: `extension/entrypoints/background/worker.ts`, `extension/entrypoints/offscreen/player.ts`, `extension/entrypoints/popup/*`, `extension/entrypoints/options/*`
- Test: `extension/tests/worker.test.ts` + manual with mock orchestrator.

**Interfaces:**
- Consumes: Tasks 2–4; orchestrator `POST /manifest|/tap`, `GET /audio/{hash}`, `GET /events`.
- Produces: worker = thin relay + `chrome.alarms` wakeup (stateless, rehydratable); **offscreen owns SSE + prefetch loop + audio** (Web Audio `AudioBufferSourceNode` chaining — NOT two `<audio>` tags); popup transport + server URL with Test-connection + first-run setup screen + per-site override affordance; options voices/speed/model global+per-site, pron-dict editor, site-rule JSON override. Failure states with copy + Retry for unreachable/timeout/bad-manifest/audio-404/challenge; `degraded:true` renders persistent badge; settings change mid-play → pause + re-manifest + resync. Content script contains ZERO `fetch(` to `http://` (test asserts).

- [ ] **Step 1: Write the failing test** — worker routes SEEK → offscreen plays new URL, old source stopped; worker restart → resumes from stored `{hash, idx, offsetMs}`; orchestrator down → overlay/banner error copy (not silence); offscreen-killed-while-paused → resume at offset; no LAN fetch from content-script context.
- [ ] **Step 2: Run it** — Expected: FAIL.
- [ ] **Step 3: Implement worker/player/popup/options** — message envelope `{type,payload,requestId}`; auto-continue + prefetch K=3; playbackRate 0.8–2.5x as pure client preview (server `speed` is the render param — never both); `PAGE_BLOCKED` banner with `[Retry]` that re-runs extraction; end-of-chapter `complete` → end card teardown.
- [ ] **Step 4: Run it** — vitest PASS + manual: mock orchestrator → play/pause/seek/rate works, survives worker kill AND 60s pause; gapless boundaries inaudible on 20-sentence sample.
- [ ] **Step 5: Commit** — `git commit -s -m "feat: worker, offscreen audio, settings UI"`.

### Task 6: End-to-end proof (NovelBuddy + real stack)

**Files:** none (verification only).

**Interfaces:**
- Consumes: stack plan Tasks 1–6 done; extension Tasks 1–5 done.

- [ ] **Step 1: Load unpacked extension**, set server URL, open NovelBuddy chapter, press play.
- [ ] **Step 2: Verify** — highlight follows audio, tap sentence 10 while 1 renders → 10 plays fast, prefetch continues, immersive toggles, Esc restores page; end card offers next chapter; two-tab + kill-reconnect pass; save screenshot + console log to `extension/proof/`.
- [ ] **Step 3: Commit proof** — `git commit -s -m "proof: NovelBuddy read-along end-to-end"`.

### Task 7: Expert hardening (5-agent audit — implement all)

Files (create): `entrypoints/shared/messaging.ts` (targeted envelope), `entrypoints/content/selftest.ts`, `welcome.html` + `entrypoints/welcome/main.ts`.
Files (modify): `background.ts`, `offscreen/player.ts`, `offscreen/main.ts`, `content.ts`, `content/mount.ts`, `overlay.tsx`, `popup/main.ts`, `options/main.ts`, `content/extract.ts`, `tests/*`.
Tests: vitest keeps LOGIC tests; style assertions move to in-browser self-test + screenshots.

- [ ] Step 1 — Targeted messaging: add `target:'sw'|'offscreen'|'content'` to every message; receivers ignore non-targets, never re-emit. SW keeps ONLY ENSURE_OFFSCREEN + PLAY-to-tab + targeted relay; no SW-held queue/timers (alarms only).
- [ ] Step 2 — Undegradable overlay CSS: `cssInjectionMode:'ui'` + `createShadowRootUi`, host on `document.documentElement`, ALL CSS in one bundled `<style>` string (px only, `:host` restore last, explicit font/color). Backdrop = solid rgba + blur enhancement. `?inkcast-selftest=1` asserts active styling/anchor; failure renders red STYLE MISSING banner. Screenshot per theme in E2E.
- [ ] Step 3 — Immersive as mask: prev/current/next over the SAME list (not a separate view); 220ms transitions; wheel/touch pauses follow + "Follow" pill, 5s re-lock; arrows step, Enter seeks, Esc exits; `role=log aria-live=polite`, roving tabindex.
- [ ] Step 4 — Karaoke component: gradient-mask word wipe via rAF; sentence-only sources degrade to line-sweep; 80ms visual minimum; never blank on gaps.
- [ ] Step 5 — Player hardening: SSE `Last-Event-ID` resume + 45s heartbeat watchdog (exp-backoff reconnect) + resync-on-gap; `ctx.resume()` on every PLAY; pause>30s death = expected resume path; position write on every LINE_AT + pagehide.
- [ ] Step 6 — Extraction upgrade: Readability `charThreshold:200, nbTopCandidates:10, linkDensityModifier:0.5, classesToPreserve:[ruby,rt,rp]`; Defuddle fallback #2; block-level promo decompose (incl. `<style>`-rule promos + author-note quarantine); MutationObserver settle-wait; soft-nav re-extract; rule JSON versioning + telemetry.
- [ ] Step 7 — Settings contract: popup = transport + ONE server-URL editor + named-error Test + "All settings" link. Options = everything else via single `settings.ts` (defaults/load/migrate/onChanged echo), validation-first editors, per-section reset, Backup export/import. `welcome.html` on install + `isConfigured()` gate (setup state, never silent).
- [ ] Step 8 — Verify: vitest + typecheck + build + full Playwright E2E green.
- [ ] Step 9 — Commit: `git commit -s -m "feat: extension expert hardening"`.

### Task 8: NovelPhoenix + Royal Road proof (second site class)
- [ ] Repeat the website E2E on a NovelPhoenix chapter (different DOM) + Royal Road (clean baseline). Both green before extraction is done.
