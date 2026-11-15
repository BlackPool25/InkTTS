export default defineBackground(() => {
  // Thin relay only: stateless, rehydratable via chrome.storage.
  // SSE + prefetch + audio live in the offscreen document (service workers
  // are killed ~30s idle and can hold neither SSE nor audio).
  const OFFSCREEN_PATH = 'offscreen.html';

  async function ensureOffscreen(): Promise<void> {
    const has = await (chrome.offscreen as unknown as {
      hasDocument?: () => Promise<boolean>;
    }).hasDocument?.();
    if (has) return;
    await chrome.offscreen.createDocument({
      url: chrome.runtime.getURL(OFFSCREEN_PATH),
      reasons: [chrome.offscreen.Reason.AUDIO_PLAYBACK],
      justification: 'Play server-synthesized chapter audio across tabs.',
    });
  }

  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (!msg || typeof msg.type !== 'string') return false;
    if (msg.type === 'PING') {
      respond({ type: 'PONG' });
      return false;
    }
    if (msg.type === 'ENSURE_OFFSCREEN') {
      ensureOffscreen().then(() => respond({ ok: true }));
      return true; // async respond
    }
    // Relay everything else to the offscreen document.
    (async () => {
      await ensureOffscreen();
      chrome.runtime.sendMessage(msg);
    })();
    return false;
  });
});
