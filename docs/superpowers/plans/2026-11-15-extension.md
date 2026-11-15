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
- Produces: `extractChapter(doc) -> {title, paragraphs[], nextUrl} | {error: 'empty'|'boilerplate'|'challenge'|'image-only'}`; pipeline: challenge check → site-rule container → Readability fallback (charThreshold ~100) → pre-strip ads/comments → promo-regex sweep → next-page stitch (cap 5) → gates.

- [ ] **Step 1: Write the failing test** — fixture novel page → paragraphs without nav/comments/TN; challenge fixture → `{error:'challenge'}`; image-only fixture → `{error:'image-only'}`; short page → `{error:'empty'}`.
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
- Produces: `<Overlay sentences[], activeIdx, immersive, theme, focusSpan, onSeek>`; full-takeover fixed layer, ambient-blur bg, Esc restores scroll; immersive = prev/current/next only, OFF default; themes dark/ambient/sepia/light; focus 1/3/5.

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
- Produces: `createSync(lrcText) -> {lineAt(t), wordFill(lineIdx,t), seekOffset(lineIdx,wordIdx)}`; binary-search anchor, sustain-not-gap interpolation, 80ms visual minimum, 300ms seek-early compensation, synthetic flag → line-sweep.

- [ ] **Step 1: Write the failing test** — LRC with word tags → `wordFill` mid-word ≈ 0.5; gap between words holds prior fill (never blank); plain line → line-sweep progress; `seekOffset` for sentence 10 = its file start.
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
- Produces: worker state machine (idle/playing/paused, rehydratable from storage); offscreen two-`<audio>` crossfade chaining on `ended`; popup transport + server URL; options voices/speed/model global+per-site, pron-dict editor.

- [ ] **Step 1: Write the failing test** — worker routes SEEK → offscreen plays new URL, old element paused; worker restart → resumes from stored `{url, idx}`; LAN fetch attempted only from worker/offscreen context (assert content script has zero `fetch(` to `http://`).
- [ ] **Step 2: Run it** — Expected: FAIL.
- [ ] **Step 3: Implement worker/player/popup/options** — message envelope `{type,payload,requestId}`; auto-continue + prefetch K=3; playbackRate 0.8–2.5x; `PAGE_BLOCKED` banner path.
- [ ] **Step 4: Run it** — vitest PASS + manual: mock orchestrator → play/pause/seek/rate works, survives worker kill.
- [ ] **Step 5: Commit** — `git commit -s -m "feat: worker, offscreen audio, settings UI"`.

### Task 6: End-to-end proof (NovelBuddy + real stack)

**Files:** none (verification only).

**Interfaces:**
- Consumes: stack plan Tasks 1–6 done; extension Tasks 1–5 done.

- [ ] **Step 1: Load unpacked extension**, set server URL, open NovelBuddy chapter, press play.
- [ ] **Step 2: Verify** — highlight follows audio, tap sentence 10 while 1 renders → 10 plays fast, prefetch continues, immersive toggles, Esc restores page; save screenshot + console log to `extension/proof/`.
- [ ] **Step 3: Commit proof** — `git commit -s -m "proof: NovelBuddy read-along end-to-end"`.
