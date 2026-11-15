import { extractChapter } from './content/extract';
import { mountOverlay, rerenderOverlay } from './content/mount';

export default defineContentScript({
  matches: ['http://*/*', 'https://*/*'],
  runAt: 'document_idle',
  main() {

  let teardown: (() => void) | null = null;
  let sentences: string[] = [];
  let active = 0;
  let immersive = false;
  let karaoke = false;
  let theme: 'dark' | 'ambient' | 'sepia' | 'light' = 'dark';
  const THEMES = ['dark', 'ambient', 'sepia', 'light'] as const;

  function currentProps() {
    return {
      sentences,
      activeIdx: active,
      immersive,
      karaoke,
      theme,
      onSeek: (idx: number) => {
        active = idx;
        rerenderOverlay(currentProps());
        chrome.runtime.sendMessage({ type: 'SEEK', payload: { idx } });
      },
      onExit: () => {
        teardown = null;
      },
      onToggleImmersive: () => {
        immersive = !immersive;
        rerenderOverlay(currentProps());
      },
      onToggleKaraoke: () => {
        karaoke = !karaoke;
        rerenderOverlay(currentProps());
      },
      onCycleTheme: () => {
        theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
        rerenderOverlay(currentProps());
      },
    };
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg?.type === 'LINE_AT' && typeof msg.payload?.idx === 'number') {
      active = msg.payload.idx;
      rerenderOverlay(currentProps());
      return;
    }
    if (msg?.type !== 'TOGGLE_OVERLAY') return;
    if (teardown) {
      teardown();
      teardown = null;
      return;
    }
    const res = extractChapter(document, location.href);
    if ('error' in res) {
      const props = {
        ...currentProps(),
        sentences: [],
        error: { code: res.error, detail: res.detail },
      };
      teardown = mountOverlay(props);
      return;
    }
    // Canonical split owner: orchestrator. Overlay lines == server
    // sentences == audio files. Fall back to local paragraphs offline.
    sentences = res.paragraphs;
    active = 0;
    teardown = mountOverlay(currentProps());
    void (async () => {
      try {
        const cfg = await chrome.storage.local.get('inkcast.settings');
        const base = (cfg['inkcast.settings'] as { serverUrl?: string } | undefined)?.serverUrl?.replace(/\/$/, '');
        if (!base) return;
        const r = await fetch(`${base}/manifest`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ doc_id: location.href, sentences: res.paragraphs.map((text) => ({ text })) }),
        });
        const man = await r.json();
        if (Array.isArray(man.sentences) && man.sentences.length > 0) {
          sentences = man.sentences.map((s: { text: string }) => s.text);
          active = 0;
          rerenderOverlay(currentProps());
        }
      } catch {
        /* offline: keep local paragraphs */
      }
    })();
    });
  },
});