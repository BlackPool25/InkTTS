export default defineBackground(() => {
  // Thin relay only: stateless, rehydratable via chrome.storage.
  // SSE + prefetch + audio live in the offscreen document (service workers
  // are killed ~30s idle and can hold neither SSE nor audio).
  // Routing: messages carry target; SW forwards offscreen-bound to the
  // offscreen doc, content-bound to the active tab. Never re-emits.
  const OFFSCREEN_PATH = 'offscreen.html';

  // First BOOT can arrive before the offscreen listener registers: keep the
  // last offscreen-bound message and flush it on OFFSCREEN_READY (or once
  // after 500ms when READY already passed).
  let pendingOffscreen: Record<string, unknown> | null = null;

  async function ensureOffscreen(): Promise<boolean> {
    const has = await (chrome.offscreen as unknown as {
      hasDocument?: () => Promise<boolean>;
    }).hasDocument?.();
    if (has) return false;
    await chrome.offscreen.createDocument({
      url: chrome.runtime.getURL(OFFSCREEN_PATH),
      reasons: [chrome.offscreen.Reason.AUDIO_PLAYBACK],
      justification: 'Play server-synthesized chapter audio across tabs.',
    });
    return true;
  }

  chrome.runtime.onInstalled.addListener((d) => {
    if (d.reason === 'install') {
      void chrome.tabs.create({ url: chrome.runtime.getURL('welcome.html') });
    }
  });

  chrome.runtime.onMessage.addListener((msg, sender, respond) => {
    if (!msg || typeof msg.type !== 'string') return false;
    const target = (msg as { target?: string }).target;
    if (msg.type === 'PING') {
      respond({ type: 'PONG' });
      return false;
    }
    if (msg.type === 'ENSURE_OFFSCREEN') {
      ensureOffscreen().then(() => respond({ ok: true }));
      return true; // async respond
    }
    if (msg.type === 'OFFSCREEN_READY') {
      if (pendingOffscreen) {
        const m = pendingOffscreen;
        pendingOffscreen = null;
        void chrome.runtime.sendMessage(m);
      }
      return false;
    }
    if (msg.type === 'PLAY' && !target) {
      // Popup transport: playback orders go to the page (extract+BOOT).
      // Report back when the tab has no content script (stale tab after
      // extension reload) so the popup can say "refresh the page".
      const forward = async (id: number | undefined): Promise<boolean> => {
        if (id === undefined) return false;
        try {
          await chrome.tabs.sendMessage(id, { ...msg, target: 'content' });
          return true;
        } catch {
          return false; // no content script in this tab
        }
      };
      const reply = async () => {
        const id = sender.tab?.id
          ?? (await chrome.tabs.query({ active: true, currentWindow: true }))[0]?.id;
        respond({ ok: await forward(id) });
      };
      void reply();
      return true; // async respond
    }
    if (target === 'offscreen' || msg.type === 'BOOT' || msg.type === 'PAUSE'
      || msg.type === 'SEEK' || msg.type === 'STOP' || msg.type === 'SET_RATE'
      || msg.type === 'STATE' || (msg.type === 'PLAY' && target === 'offscreen')) {
      // Page/popup -> player. Exactly-once: if the doc already lives,
      // send now; if we just created it, its listener isn't registered
      // yet — stash and flush on OFFSCREEN_READY (or once after 500ms).
      void (async () => {
        let created = false;
        try {
          created = await ensureOffscreen();
        } catch {
          created = false; // race: doc appeared between check and create
        }
        if (!created) {
          void chrome.runtime.sendMessage(msg);
          return;
        }
        pendingOffscreen = msg as Record<string, unknown>;
        setTimeout(() => {
          if (pendingOffscreen === msg) {
            pendingOffscreen = null;
            void chrome.runtime.sendMessage(msg);
          }
        }, 500);
      })();
      return false;
    }
    if (msg.type === 'GET_STATE' && !target) {
      // Popup mirror: ask the active tab, relay its answer back.
      const reply = async () => {
        try {
          const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
          const id = sender.tab?.id ?? tabs[0]?.id;
          if (id === undefined) { respond({ hasOverlay: false }); return; }
          const st = await chrome.tabs.sendMessage(id, { ...msg, target: 'content' });
          respond(st ?? { hasOverlay: false });
        } catch {
          respond({ hasOverlay: false });
        }
      };
      void reply();
      return true; // async respond
    }
    if (msg.type === 'RECREATE_OFFSCREEN') {
      // Self-heal: drop a possibly dead doc; next message recreates fresh.
      void (chrome.offscreen as unknown as { closeDocument?: () => Promise<void> })
        .closeDocument?.().catch(() => undefined);
      return false;
    }
    if (target === 'content' || msg.type === 'LINE_AT' || msg.type === 'MANIFEST_OK'
      || msg.type === 'BOOT_ACK'
      || msg.type === 'AUDIO_ERROR' || msg.type === 'AUDIO_DEGRADED'
      || msg.type === 'CHAPTER_COMPLETE' || msg.type === 'STATE_IS') {
      // Player -> page. Offscreen has no tabs access view; SW forwards.
      const forward = (id: number | undefined) => {
        if (id === undefined) return;
        void chrome.tabs.sendMessage(id, msg).catch(() => undefined);
      };
      if (sender.tab?.id !== undefined) {
        forward(sender.tab.id);
        return false;
      }
      void chrome.tabs.query({ active: true, currentWindow: true }).then((tabs) => forward(tabs[0]?.id));
      return false;
    }
    return false;
  });
});
