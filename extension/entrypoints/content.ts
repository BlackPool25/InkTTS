import { extractChapter, isChallenge, setRuleOverrides, settleAndExtract, stitchChapterPages } from './content/extract';
import type { ExtractOk } from './content/extract';
import { mountOverlay, rerenderOverlay } from './content/mount';
import { consumeChapterHandoff, loadSettings, saveSettings, setChapterHandoff } from './shared/messaging';

export default defineContentScript({
  matches: ['http://*/*', 'https://*/*'],
  runAt: 'document_idle',
  main() {

  let teardown: (() => void) | null = null;
  let sentences: string[] = [];
  let active = 0;
  // Immersive-only reader: no separate non-immersive mode exists anymore.
  let setupNeeded = false;
  let degraded = false;
  let loading: false | 'boot' | 'manifest' = false;
  let playing = false;
  let bootTimer: ReturnType<typeof setTimeout> | null = null;
  let ackTimer: ReturnType<typeof setTimeout> | null = null;
  let bootAttempts = 0;
  let acked = false;
  let pendingSeek: number | null = null;
  let lastBoot: { docId: string; paragraphs: string[]; serverUrl: string; startIdx: number } | null = null;
  let recreateNext = false;
  let hasBooted = false;
  let focusSpan: 1 | 3 | 5 = 3;
  let autoNextTimer: ReturnType<typeof setTimeout> | null = null;

  /** Any deliberate interaction cancels a pending auto next-chapter. */
  function cancelAutoNext(): void {
    if (autoNextTimer) {
      clearTimeout(autoNextTimer);
      autoNextTimer = null;
    }
  }

  type Phase = 'idle' | 'loading' | 'playing' | 'paused' | 'error' | 'done';
  function phase(): Phase {
    if (audioError) return 'error';
    if (complete) return 'done';
    if (loading !== false) return 'loading';
    if (playing) return 'playing';
    if (hasBooted) return 'paused';
    return 'idle';
  }
  let audioError: string | null = null;
  let rate = 1.0;
  const SPEEDS = [0.8, 1.0, 1.25, 1.5, 2.0];
  let complete = false;
  let nextUrl: string | null = null;
  let prevUrl: string | null = null;
  let chapterTitle = '';
  let novelTitle: string | null = null;
  let authorName: string | null = null;
  let fallbackNotice = false;
  let lastError: string | null = null;
  let followPaused = false;
  let theme: 'dark' | 'ambient' | 'sepia' | 'light' = 'dark';
  const THEMES = ['dark', 'ambient', 'sepia', 'light'] as const;

  function currentProps() {
    return {
      sentences,
      activeIdx: active,
      chapterTitle,
      novelTitle,
      author: authorName,
      fallbackNotice,
      theme,
      degraded,
      loading: loading !== false,
      loadingStage: loading === false ? undefined : loading,
      playing,
      audioError,
      rate,
      focusSpan,
      phase: phase(),
      complete,
      nextUrl,
      prevUrl,
      setupNeeded,
      followPaused,
      onSeek: (idx: number) => {
        cancelAutoNext();
        complete = false;
        active = idx;
        if (hasBooted) {
          // Player alive: a tap means listen from here.
          playing = true;
          audioError = null;
        }
        if (loading !== false) pendingSeek = idx; // manifest in flight: apply on landing
        rerenderOverlay(currentProps());
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'SEEK', payload: { idx } });
      },
      onExit: () => {
        cancelAutoNext();
        const t = teardown;
        teardown = null;
        t?.();
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'STOP' });
      },
      onNextChapter: () => void goTo(nextUrl),
      onPrevChapter: () => void goTo(prevUrl),
      onCycleTheme: () => {
        theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
        rerenderOverlay(currentProps());
        void loadSettings()
          .then((s) => saveSettings({ ...s, theme }))
          .catch(() => undefined);
      },
      onSettingsChange: (patch: { theme?: string; speed?: number }) => {
        let changed = false;
        if (patch.theme && (THEMES as readonly string[]).includes(patch.theme)) {
          theme = patch.theme as typeof theme;
          changed = true;
        }
        if (typeof patch.speed === 'number' && patch.speed >= 0.8 && patch.speed <= 2.5) {
          rate = patch.speed;
          chrome.runtime.sendMessage({ target: 'offscreen', type: 'SET_RATE', payload: { rate } });
          changed = true;
        }
        if (changed) rerenderOverlay(currentProps());
        void loadSettings()
          .then((s) => saveSettings({
            ...s,
            ...(patch.theme ? { theme: patch.theme } : {}),
            ...(typeof patch.speed === 'number' ? { speed: patch.speed } : {}),
          }))
          .catch(() => undefined);
      },
      onResumeFollow: () => {
        followPaused = false;
        rerenderOverlay(currentProps());
      },
      onPauseFollow: () => {
        if (!followPaused) {
          followPaused = true;
          rerenderOverlay(currentProps());
        }
      },
      onPause: () => {
        playing = false;
        rerenderOverlay(currentProps());
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'PAUSE' });
      },
      onPlay: () => void startPlayback(),
      onNext: () => void goTo(nextUrl),
      onPrev: () => {
        if (sentences.length === 0) return;
        cancelAutoNext();
        complete = false;
        const idx = Math.max(0, active - 1);
        active = idx;
        if (hasBooted) {
          playing = true;
          audioError = null;
        }
        rerenderOverlay(currentProps());
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'SEEK', payload: { idx } });
      },
      onNextSentence: () => {
        if (sentences.length === 0) return;
        cancelAutoNext();
        complete = false;
        const idx = Math.min(sentences.length - 1, active + 1);
        active = idx;
        if (hasBooted) {
          playing = true;
          audioError = null;
        }
        rerenderOverlay(currentProps());
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'SEEK', payload: { idx } });
      },
      onCycleSpeed: () => {
        rate = SPEEDS[(SPEEDS.indexOf(rate) + 1) % SPEEDS.length] ?? 1.0;
        rerenderOverlay(currentProps());
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'SET_RATE', payload: { rate } });
      },
      onRetry: () => {
        if (teardown) {
          const t = teardown;
          teardown = null;
          t();
        }
        openOverlay(false);
      },
    };
  }

  async function serverUrl(): Promise<string | null> {
    try {
      const s = await loadSettings();
      return s.serverUrl.replace(/\/$/, '') || null;
    } catch {
      return null;
    }
  }

  async function startPlayback(fromIdx?: number): Promise<void> {
    // No LAN fetch here (mixed-content on https pages): offscreen owns /manifest.
    if (!teardown || sentences.length === 0) return;
    cancelAutoNext();
    complete = false;
    const base = await serverUrl();
    if (!base) {
      setupNeeded = true;
      rerenderOverlay(currentProps());
      return;
    }
    if (recreateNext) {
      // Previous attempt died in a dead doc: drop it, boot fresh.
      recreateNext = false;
      chrome.runtime.sendMessage({ target: 'sw', type: 'RECREATE_OFFSCREEN' });
      await new Promise((r) => setTimeout(r, 400));
    }
    setupNeeded = false;
    hasBooted = true;
    loading = 'boot';
    playing = true;
    audioError = null;
    bootAttempts = 1;
    acked = false;
    lastBoot = {
      docId: location.href,
      paragraphs: sentences,
      serverUrl: base,
      startIdx: fromIdx ?? active,
    };
    rerenderOverlay(currentProps());
    if (bootTimer) clearTimeout(bootTimer);
    if (ackTimer) clearTimeout(ackTimer);
    bootTimer = setTimeout(() => {
      // Bounded wait. Stage says which half is silent.
      if (loading !== false) {
        const stage = loading;
        loading = false;
        playing = false;
        if (stage === 'boot') {
          recreateNext = true; // player never acked: drop the dead doc
          audioError = 'Player did not answer — the audio engine looks dead. Check the server, then Retry.';
        } else {
          recreateNext = false; // player alive, server/fetch silent
          audioError = 'Server is not answering — is the LAN URL reachable? Check the host, then Retry.';
        }
        rerenderOverlay(currentProps());
      }
    }, 10000);
    sendBoot();
    scheduleAckCheck();
  }

  function sendBoot(): void {
    if (!lastBoot) return;
    chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'BOOT',
      payload: lastBoot,
    });
  }

  function scheduleAckCheck(): void {
    // First BOOT can land in a not-yet-ready audio doc and vanish
    // silently. Retry (recreating the doc on the 2nd try) until the
    // player acks — no user action needed.
    if (ackTimer) clearTimeout(ackTimer);
    ackTimer = setTimeout(() => {
      if (acked || loading === false || !lastBoot) return;
      if (bootAttempts >= 3) return; // 10s timer delivers the verdict
      bootAttempts += 1;
      if (bootAttempts === 2) {
        chrome.runtime.sendMessage({ target: 'sw', type: 'RECREATE_OFFSCREEN' });
        setTimeout(() => {
          sendBoot();
          scheduleAckCheck();
        }, 400);
        return;
      }
      sendBoot();
      scheduleAckCheck();
    }, 2000);
  }

  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (!msg || typeof msg.type !== 'string') return false;
    if (msg.target !== undefined && msg.target !== 'content' && msg.type !== 'TOGGLE_OVERLAY' && msg.type !== 'PLAY') return false;
    if (msg.type === 'GET_STATE') {
      // Popup transport mirror: answer with the live reader state.
      respond({
        phase: phase(),
        activeIdx: active,
        total: sentences.length,
        title: chapterTitle,
        novel: novelTitle,
        hasOverlay: teardown !== null,
      });
      return false;
    }
    switch (msg.type) {      case 'BOOT_ACK':
        // Listener alive; manifest still pending. Promote the stage so a
        // later timeout blames the server fetch, not the player.
        acked = true;
        if (ackTimer) { clearTimeout(ackTimer); ackTimer = null; }
        if (loading !== false) {
          loading = 'manifest';
          rerenderOverlay(currentProps());
        }
        return;
      case 'MANIFEST_OK': {
        if (bootTimer) { clearTimeout(bootTimer); bootTimer = null; }
        if (ackTimer) { clearTimeout(ackTimer); ackTimer = null; }
        const list = (msg.payload as { sentences?: unknown })?.sentences;
        if (Array.isArray(list) && list.every((t) => typeof t === 'string')) {
          sentences = list as string[];
          active = 0;
          if (loading !== false) loading = 'manifest';
          // A tap that landed mid-manifest: honor it now that lines exist.
          if (pendingSeek !== null) {
            const ps = Math.max(0, Math.min(sentences.length - 1, pendingSeek));
            pendingSeek = null;
            active = ps;
            chrome.runtime.sendMessage({ target: 'offscreen', type: 'SEEK', payload: { idx: ps } });
          }
          rerenderOverlay(currentProps());
        }
        return;
      }
      case 'LINE_AT': {
        if (bootTimer) { clearTimeout(bootTimer); bootTimer = null; }
        if (ackTimer) { clearTimeout(ackTimer); ackTimer = null; }
        const idx = (msg.payload as { idx?: unknown })?.idx;
        if (typeof idx === 'number') {
          active = idx;
          loading = false as const;
          playing = true;
          rerenderOverlay(currentProps());
        }
        return;
      }
      case 'AUDIO_ERROR': {
        if (bootTimer) { clearTimeout(bootTimer); bootTimer = null; }
        if (ackTimer) { clearTimeout(ackTimer); ackTimer = null; }
        loading = false;
        playing = false;
        const pl = (msg.payload ?? {}) as { reason?: string; status?: number; hint?: string };
        const pidx = (pl as { idx?: unknown }).idx;
        audioError = pl.hint ?? pl.reason
          ?? (pl.status ? `server answered ${pl.status}` : null)
          ?? (typeof pidx === 'number' ? `sentence ${pidx + 1} is not ready yet` : 'unknown error');
        rerenderOverlay(currentProps());
        return;
      }
      case 'AUDIO_DEGRADED':
        degraded = true;
        rerenderOverlay(currentProps());
        return;
      case 'CHAPTER_COMPLETE':
        complete = true;
        playing = false;
        loading = false;
        rerenderOverlay(currentProps());
        // Graceful handoff: savor the end card for 4s so the finish
        // registers; any interaction cancels the auto-advance.
        if (autoNextTimer) clearTimeout(autoNextTimer);
        if (nextUrl) {
          autoNextTimer = setTimeout(() => {
            if (complete && phase() === 'done') void goTo(nextUrl);
          }, 4000);
        }
        return;
      case 'PLAY':
        if (!teardown) openOverlay(true);
        else void startPlayback();
        return;
      case 'PAUSE':
        // Player acks by silence; overlay keeps the frozen highlight.
        return;
      default:
        break;
    }
    if (msg.type !== 'TOGGLE_OVERLAY') return;
    if (teardown) {
      const t = teardown;
      teardown = null;
      t();
      return;
    }
    openOverlay(false);
  });

  function openOverlay(autoplay: boolean): void {
    let res;
    try {
      res = extractChapter(document, location.href);
    } catch (e) {
      // Extraction must never die silent: loud error, not nothing.
      sentences = [];
      lastError = 'crashed';
      teardown = mountOverlay({
        ...currentProps(),
        sentences: [],
        error: { code: 'boilerplate', detail: `extractor crashed: ${(e as Error).message}` },
      });
      return;
    }
    if ('error' in res) {
      sentences = [];
      lastError = res.error;
      const props = {
        ...currentProps(),
        sentences: [],
        error: { code: res.error, detail: res.detail },
      };
      teardown = mountOverlay(props);
      return;
    }
    // Mount instantly with local paragraphs (overlay lines), then BOOT the
    // offscreen player: it POSTs /manifest and MANIFEST_OK re-aligns lines
    // to the server's canonical split (overlay == audio files).
    sentences = res.paragraphs;
    active = 0;
    complete = false;
    degraded = false;
    nextUrl = res.nextUrl;
    prevUrl = res.prevUrl;
    chapterTitle = res.title;
    novelTitle = res.novel;
    authorName = res.author;
    fallbackNotice = res.fallback === true;
    lastError = null;
    teardown = mountOverlay(currentProps());
    // Settle + stitch in the background: the instant mount above may have
    // caught a lazy chapter mid-load or only page 1 of N. If the user has
    // not pressed Play yet, fold the fuller text in silently.
    void (async () => {
      try {
        const url = location.href;
        const settled = await settleAndExtract(document, url);
        if (!('paragraphs' in settled) || hasBooted) return;
        let paras = settled.paragraphs;
        const first = settled as ExtractOk;
        try {
          const st = await stitchChapterPages(first, document, url);
          if (hasBooted) return;
          paras = st.paragraphs;
        } catch { /* stitch failed: settled text still wins */ }
        if (!hasBooted && paras.length > sentences.length) {
          sentences = paras;
          active = 0;
          chapterTitle = first.title;
          novelTitle = first.novel;
          authorName = first.author;
          if (first.fallback === true) fallbackNotice = true;
          rerenderOverlay(currentProps());
        }
      } catch { /* background enrichment: never break the mounted reader */ }
    })();
    // Mount is instant with local paragraphs. BOOT (server manifest +
    // audio) only on explicit Play — opening the reader never auto-plays.
    // Apply stored prefs (theme/karaoke/speed) + user site-rule overrides.
    void (async () => {
      const s = await loadSettings().catch(() => null);
      if (!s) return;
      if (s.siteRules) setRuleOverrides(s.siteRules);
      let changed = false;
      if (s.theme) { theme = s.theme as typeof theme; changed = true; }
      if (typeof s.speed === 'number' && s.speed >= 0.8 && s.speed <= 2.5) {
        rate = s.speed;
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'SET_RATE', payload: { rate } });
        changed = true;
      }
      if (typeof s.focus === 'number' && [1, 3, 5].includes(s.focus)) {
        focusSpan = s.focus as 1 | 3 | 5;
        changed = true;
      }
      if (changed) rerenderOverlay(currentProps());
      if (autoplay) await startPlayback(0);
    })();
  }

  async function goTo(url: string | null): Promise<void> {
    // Continuous reading: the reader's own prev/next always carries the
    // immersive overlay to the new chapter (extension store survives the
    // navigation; sessionStorage alone does not, so set both).
    await setChapterHandoff().catch(() => undefined);
    if (url) location.href = url;
  }

  // Soft-nav (SPA readers): re-extract + tear down the stale overlay.
  let lastUrl = location.href;
  const navTimer = setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      if (teardown) {
        teardown();
        teardown = null;
      }
    }
  }, 1000);
  void navTimer;

  // Next-chapter handoff: landed via the reader's own prev/next, so
  // re-open the immersive overlay here too. The autoplay checkbox gates
  // audio only — the overlay itself always follows the inbuilt nav.
  void (async () => {
    const handoff = await consumeChapterHandoff().catch(() => false);
    if (!handoff) return;
    const s = await loadSettings().catch(() => null);
    const autoplay = s ? s.autoplayNext !== false : true;
    setTimeout(() => {
      if (!teardown) openOverlay(autoplay);
      else if (autoplay) void startPlayback();
    }, 800);
  })();

  // Challenge solved (user cleared the checkbox): auto re-extract once,
  // reusing the Retry path. Debounced so checkbox toggles don't thrash.
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  function reopen(): void {
    if (teardown) {
      const t = teardown;
      teardown = null;
      t();
    }
    openOverlay(false);
  }
  const obs = new MutationObserver(() => {
    if (!teardown) return; // overlay already up
    if (lastError !== 'challenge' && lastError !== 'locked') return;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      try {
        if (!isChallenge(document)) {
          lastError = null;
          reopen();
        }
      } catch { /* DOM unreadable: stay on the error card */ }
    }, 1000);
  });
  obs.observe(document.documentElement, { childList: true, subtree: true });
  void obs;
  },
});
