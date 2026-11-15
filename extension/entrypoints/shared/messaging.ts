// Shared message envelope + storage contracts.
// Worker is a thin relay; offscreen owns SSE + prefetch + audio.
export type MsgType =
  | 'PLAY' | 'PAUSE' | 'SEEK' | 'STOP'
  | 'OFFSCREEN_READY' | 'LINE_AT' | 'AUDIO_ERROR'
  | 'PAGE_BLOCKED' | 'PAGE_CLEAR'
  | 'SETTINGS_CHANGED';

export interface Msg {
  type: MsgType;
  requestId?: string;
  payload?: Record<string, unknown>;
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
  perSite: Record<string, { voice?: string; model?: 'kokoro' | 'chatterbox' }>;
  siteRules?: string;
  pron?: string;
  theme?: string;
  karaoke?: boolean;
  focus?: number;
  immersiveDefault?: boolean;
}

export const DEFAULT_SETTINGS: ServerSettings = {
  serverUrl: '',
  voice: 'af_heart',
  speed: 1.0,
  model: 'kokoro',
  perSite: {},
};

const EPphemERAL_KEY = 'inkcast.playstate';

export async function loadSettings(): Promise<ServerSettings> {
  const s = await chrome.storage.local.get('inkcast.settings');
  return { ...DEFAULT_SETTINGS, ...(s['inkcast.settings'] ?? {}) };
}

export async function saveSettings(s: ServerSettings): Promise<void> {
  await chrome.storage.local.set({ 'inkcast.settings': s });
}

export async function savePlayState(st: PlayState): Promise<void> {
  await chrome.storage.session.set({ [EPphemERAL_KEY]: st });
}

export async function loadPlayState(): Promise<PlayState | null> {
  const s = await chrome.storage.session.get(EPphemERAL_KEY);
  return (s[EPphemERAL_KEY] as PlayState) ?? null;
}
