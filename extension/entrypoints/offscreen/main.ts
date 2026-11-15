// Offscreen host: owns the single Player. All playback orders arrive as
// chrome.runtime messages with target:'offscreen' (relayed by the worker).
import { Player } from './player';

const player = new Player();

// First BOOT can race doc creation: signal readiness so the worker can
// flush (or once-retry) the pending offscreen-bound message.
chrome.runtime.sendMessage({ target: 'sw', type: 'OFFSCREEN_READY' });

interface BootPayload {
  docId: string;
  paragraphs?: string[];
  sentences?: { idx: number; text: string }[] | string[];
  serverUrl: string;
  startIdx: number;
}

function paragraphsOf(p: BootPayload): string[] {
  if (Array.isArray(p.paragraphs)) return p.paragraphs.filter((t) => typeof t === 'string');
  if (Array.isArray(p.sentences)) {
    return (p.sentences as unknown[]).map((s) =>
      typeof s === 'string' ? s : (s as { text?: unknown }).text).filter((t): t is string => typeof t === 'string');
  }
  return [];
}

function isBootPayload(p: unknown): p is BootPayload {
  if (typeof p !== 'object' || p === null) return false;
  const o = p as Record<string, unknown>;
  return typeof o.docId === 'string' && (Array.isArray(o.paragraphs) || Array.isArray(o.sentences))
    && typeof o.serverUrl === 'string' && typeof o.startIdx === 'number';
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || typeof msg.type !== 'string') return;
  // Targeted envelope: ignore anything not meant for the player.
  if (msg.target !== undefined && msg.target !== 'offscreen') return;
  const payload = (msg as { payload?: unknown }).payload;
  switch (msg.type) {
    case 'BOOT':
      if (isBootPayload(payload)) {
        // Provable delivery: ack receipt BEFORE the slow manifest render,
        // so the page can tell "player dead" from "server slow".
        chrome.runtime.sendMessage({ target: 'content', type: 'BOOT_ACK',
          payload: { docId: (payload as BootPayload).docId } });
        void player.boot(payload.docId, paragraphsOf(payload), payload.startIdx, payload.serverUrl);
      }
      break;
    case 'PLAY':
      player.resume();
      break;
    case 'PAUSE':
      player.pause();
      break;
    case 'SEEK':
      if (typeof (payload as { idx?: unknown } | undefined)?.idx === 'number') {
        player.seek((payload as { idx: number }).idx);
      }
      break;
    case 'SEEK_PREV':
      player.step(-1);
      break;
    case 'SEEK_NEXT':
      player.step(1);
      break;
    case 'STOP':
      player.stop();
      break;
    case 'SET_RATE':
      if (typeof (payload as { rate?: unknown } | undefined)?.rate === 'number') {
        player.setRate((payload as { rate: number }).rate);
      }
      break;
    case 'STATE': {
      const s = player.state();
      chrome.runtime.sendMessage({ target: 'content', type: 'STATE_IS', payload: s });
      break;
    }
    default:
      break;
  }
});
