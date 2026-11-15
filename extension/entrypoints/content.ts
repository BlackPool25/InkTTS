export default defineContentScript({
  matches: ['https://*/*'],
  runAt: 'document_idle',
  main() {
    // Extraction + overlay mount in Task 2/3. No LAN fetch from this
    // context (mixed-content) — all server calls go via worker/offscreen.
    console.debug('[inkcast] content script loaded');
  },
});
