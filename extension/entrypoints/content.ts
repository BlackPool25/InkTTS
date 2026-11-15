import { extractChapter, setRuleOverrides } from './content/extract';
import { mountOverlay, rerenderOverlay } from './content/mount';
import { loadSettings, saveSettings } from './shared/messaging';

export default defineContentScript({
  matches: ['http://*/*', 'https://*/*'],
  runAt: 'document_idle',
  main() {

  let teardown: (() => void) | null = null;
  let sentences: string[] = [];
  let active = 0;
  // Immersive-only reader: no separate non-immersive mode exists anymore.
  let karaoke = false;
  let setupNeeded = false;
  let degraded = false;
  let loading: false | 'boot' | 'manifest' = false;
  let playing = false;
  let bootTimer: ReturnType<typeof setTimeout> | null = null;
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
  let followPaused = false;
  let theme: 'dark' | 'ambient' | 'sepia' | 'light' = 'dark';
  const THEMES = ['dark', 'ambient', 'sepia', 'light'] as const;

  function currentProps() {
    return {
      sentences,
      activeIdx: active,
      chapterTitle,
      novelTitle,
      karaoke,
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
      onToggleKaraoke: () => {
        karaoke = !karaoke;
        rerenderOverlay(currentProps());
        void loadSettings()
          .then((s) => saveSettings({ ...s, karaoke }))
          .catch(() => undefined);
      },
      onCycleTheme: () => {
        theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
        rerenderOverlay(currentProps());
        void loadSettings()
          .then((s) => saveSettings({ ...s, theme }))
          .catch(() => undefined);
      },
      onSettingsChange: (patch: { theme?: string; karaoke?: boolean; speed?: number }) => {
        let changed = false;
        if (patch.theme && (THEMES as readonly string[]).includes(patch.theme)) {
          theme = patch.theme as typeof theme;
          changed = true;
        }
        if (typeof patch.karaoke === 'boolean') {
          karaoke = patch.karaoke;
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
            ...(typeof patch.karaoke === 'boolean' ? { karaoke: patch.karaoke } : {}),
            ...(typeof patch.speed === 'number' ? { speed: patch.speed } : {}),
          }))
          .catch(() => undefined);
      },
      onResumeFollow: () => {
        followPaused = false;
        rerenderOverlay(currentProps());
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
        rerenderOverlay(currentProps());
        chrome.runtime.sendMessage({ target: 'offscreen', type: 'SEEK', payload: { idx } });
      },
      onNextSentence: () => {
        if (sentences.length === 0) return;
        cancelAutoNext();
        complete = false;
        const idx = Math.min(sentences.length - 1, active + 1);
        active = idx;
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
    rerenderOverlay(currentProps());
    if (bootTimer) clearTimeout(bootTimer);
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
    chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'BOOT',
      payload: {
        docId: location.href,
        paragraphs: sentences,
        serverUrl: base,
        startIdx: fromIdx ?? active,
      },
    });
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
        if (loading !== false) {
          loading = 'manifest';
          rerenderOverlay(currentProps());
        }
        return;
      case 'MANIFEST_OK': {
        if (bootTimer) { clearTimeout(bootTimer); bootTimer = null; }
        const list = (msg.payload as { sentences?: unknown })?.sentences;
        if (Array.isArray(list) && list.every((t) => typeof t === 'string')) {
          sentences = list as string[];
          active = 0;
          if (loading !== false) loading = 'manifest';
          rerenderOverlay(currentProps());
        }
        return;
      }
      case 'LINE_AT': {
        if (bootTimer) { clearTimeout(bootTimer); bootTimer = null; }
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
      teardown = mountOverlay({
        ...currentProps(),
        sentences: [],
        error: { code: 'boilerplate', detail: `extractor crashed: ${(e as Error).message}` },
      });
      return;
    }
    if ('error' in res) {
      sentences = [];
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
    teardown = mountOverlay(currentProps());
    // Mount is instant with local paragraphs. BOOT (server manifest +
    // audio) only on explicit Play — opening the reader never auto-plays.
    // Apply stored prefs (theme/karaoke/speed) + user site-rule overrides.
    void (async () => {
      const s = await loadSettings().catch(() => null);
      if (!s) return;
      if (s.siteRules) setRuleOverrides(s.siteRules);
      let changed = false;
      if (s.theme) { theme = s.theme as typeof theme; changed = true; }
      if (s.karaoke !== undefined) { karaoke = !!s.karaoke; changed = true; }
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
    // Continuous reading: carry autoplay across the navigation so the
    // next chapter renders and plays without another press.
    try {
      const s = await loadSettings();
      if (s.autoplayNext !== false) sessionStorage.setItem('inkcast.autoplay', '1');
    } catch { /* storage blocked: plain nav */ }
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

  // Next-chapter autoplay: landed via goNext, open + play directly.
  try {
    if (sessionStorage.getItem('inkcast.autoplay') === '1') {
      sessionStorage.removeItem('inkcast.autoplay');
      setTimeout(() => {
        if (!teardown) openOverlay(true);
        else void startPlayback();
      }, 800);
    }
  } catch { /* storage blocked: manual play */ }

  // Challenge cleared (user solved the checkbox): auto re-extract once.
  const obs = new MutationObserver(() => {
    if (teardown) return; // overlay already up
  });
  obs.observe(document.documentElement, { childList: true, subtree: true });
  void obs;
  },
});
