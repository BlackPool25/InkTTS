export default defineBackground(() => {
  // Thin relay: stateless, rehydratable. SSE + prefetch + audio live in
  // the offscreen document (service workers die ~30s idle and cannot
  // hold SSE or play audio). This worker only routes messages.
  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg?.type === 'PING') respond({ type: 'PONG' });
    return false;
  });
});
