// Shared message envelope + storage contracts.
// Worker is a thin relay; offscreen owns SSE + prefetch + audio.
// Targeted envelope: every message carries target; receivers ignore
// non-targets and never re-emit (kills relay loops).
export type Target = 'sw' | 'offscreen' | 'content';

export type MsgType =
  | 'PLAY' | 'PAUSE' | 'SEEK' | 'SEEK_PREV' | 'SEEK_NEXT' | 'STOP' | 'SET_RATE' | 'STATE' | 'GET_STATE'
  | 'BOOT' | 'MANIFEST_OK'
  | 'OFFSCREEN_READY' | 'BOOT_ACK' | 'RECREATE_OFFSCREEN'
  | 'LINE_AT' | 'AUDIO_ERROR' | 'AUDIO_DEGRADED'
  | 'STATE_IS' | 'CHAPTER_COMPLETE'
  | 'PAGE_BLOCKED' | 'PAGE_CLEAR'
  | 'SETTINGS_CHANGED' | 'ENSURE_OFFSCREEN' | 'PING' | 'TOGGLE_OVERLAY';

export interface Msg {
  target: Target;
  type: MsgType;
  requestId?: string;
  payload?: Record<string, unknown>;
}

export function sendTo(target: Target, type: MsgType, payload?: Record<string, unknown>): void {
  chrome.runtime.sendMessage({ target, type, payload });
}

export interface PlayState {
  docId: string;
  hash: string;
  idx: number;
  offsetMs: number;
  rate: number;
}

export interface ServerSettings {
  serverUrl: string;
  voice: string;
  speed: number;
  model: 'kokoro' | 'chatterbox';
  voiceConsent?: boolean; // user accepts responsibility for non-default voices
  perSite: Record<string, { voice?: string; model?: 'kokoro' | 'chatterbox' }>;
  siteRules?: string;
  pron?: string;
  theme?: string;
  karaoke?: boolean;
  focus?: number;
  immersiveDefault?: boolean;
  autoplayNext?: boolean;
}

export const DEFAULT_SETTINGS: ServerSettings = {
  serverUrl: '',
  voice: 'af_heart',
  speed: 1.0,
  model: 'kokoro',
  perSite: {},
  theme: 'dark',
  karaoke: false,
  focus: 3,
  immersiveDefault: false,
  autoplayNext: true,
};

const EPHEMERAL_KEY = 'inktts.playstate';
const SETTINGS_KEY = 'inktts.settings';

export async function loadSettings(): Promise<ServerSettings> {
  const s = await chrome.storage.local.get(SETTINGS_KEY);
  const raw = (s[SETTINGS_KEY] ?? {}) as Partial<ServerSettings>;
  // Migrate: older stores may lack new keys; fill from defaults.
  return { ...DEFAULT_SETTINGS, ...raw };
}

/** True when the extension is configured enough to play. */
export async function isConfigured(): Promise<boolean> {
  const s = await loadSettings();
  return s.serverUrl.trim().length > 0;
}

export async function saveSettings(s: ServerSettings): Promise<void> {
  await chrome.storage.local.set({ [SETTINGS_KEY]: s });
}

/** Live echo: re-run fn on every settings change (popup+options stay in sync). */
export function onSettingsChanged(fn: (s: ServerSettings) => void): () => void {
  const h = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
    if (area === 'local' && changes[SETTINGS_KEY]) {
      fn({ ...DEFAULT_SETTINGS, ...((changes[SETTINGS_KEY].newValue ?? {}) as object) } as ServerSettings);
    }
  };
  chrome.storage.onChanged.addListener(h);
  return () => chrome.storage.onChanged.removeListener(h);
}

export async function savePlayState(st: PlayState): Promise<void> {
  try {
    // session may not exist in every context (offscreen in some
    // browsers): fall back to local, never break playback over it.
    const area = chrome.storage?.session ?? chrome.storage?.local;
    await area.set({ [EPHEMERAL_KEY]: st });
  } catch { /* ephemeral state: safe to drop */ }
}

export async function loadPlayState(): Promise<PlayState | null> {
  try {
    const area = chrome.storage?.session ?? chrome.storage?.local;
    const s = await area.get(EPHEMERAL_KEY);
    return (s[EPHEMERAL_KEY] as PlayState) ?? null;
  } catch {
    return null;
  }
}

const HANDOFF_KEY = 'inktts.chapter-handoff';
const HANDOFF_TTL_MS = 120_000;

function handoffArea(): chrome.storage.StorageArea | undefined {
  try {
    return chrome.storage?.session ?? chrome.storage?.local;
  } catch {
    return undefined;
  }
}

/** Inbuilt prev/next nav leaves a handoff so the new chapter re-opens the reader. */
export async function setChapterHandoff(): Promise<void> {
  try {
    await handoffArea()?.set({ [HANDOFF_KEY]: Date.now() });
  } catch { /* best-effort */ }
  try {
    sessionStorage.setItem('inktts.autoplay', '1');
  } catch { /* storage blocked: extension-store copy suffices */ }
}

/** True when we just landed via the reader's own prev/next buttons. Consumes the flag. */
export async function consumeChapterHandoff(): Promise<boolean> {
  let fresh = false;
  try {
    const area = handoffArea();
    const s = await area?.get(HANDOFF_KEY);
    const at = s?.[HANDOFF_KEY] as unknown;
    if (typeof at === 'number' && Date.now() - at < HANDOFF_TTL_MS) fresh = true;
    await area?.remove(HANDOFF_KEY);
  } catch { /* fall through to sessionStorage copy */ }
  try {
    if (sessionStorage.getItem('inktts.autoplay') === '1') fresh = true;
    sessionStorage.removeItem('inktts.autoplay');
  } catch { /* ignore */ }
  return fresh;
}
