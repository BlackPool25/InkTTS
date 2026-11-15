import { extractChapter } from './content/extract';
import { mountOverlay, rerenderOverlay } from './content/mount';

let teardown: (() => void) | null = null;
let sentences: string[] = [];
let active = 0;

function currentProps() {
  return {
    sentences,
    activeIdx: active,
    onSeek: (idx: number) => {
      active = idx;
      rerenderOverlay(currentProps());
      chrome.runtime.sendMessage({ type: 'SEEK', payload: { idx } });
    },
    onExit: () => {
      teardown = null;
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
  sentences = res.paragraphs;
  active = 0;
  teardown = mountOverlay(currentProps());
  // Prototype pipeline: POST paragraphs to orchestrator for render.
  // (Full manifest flow lands with server URL settings in Task 5 review.)
});
