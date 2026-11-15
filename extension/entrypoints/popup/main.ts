import { loadSettings, saveSettings } from '../shared/messaging';

const CSS = `
:root { color-scheme: dark; }
body { font-family: Georgia, 'Times New Roman', serif; background: #08080c; color: #f2f2f5; margin: 0; padding: 0; width: 360px; }
.wrap { padding: 16px; background-image: radial-gradient(120% 90% at 50% 0%, #141422 0%, #08080c 55%); }
.brand { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.brand h1 { font-size: 15px; margin: 0; letter-spacing: .04em; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #55555f; }
.dot.ok { background: #7de2a8; box-shadow: 0 0 8px #7de2a8; }
.dot.bad { background: #ff9d9d; }
#now { font-size: 12px; color: #9a9aa5; min-height: 18px; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#big { width: 100%; font-size: 17px; font-weight: 700; padding: 14px; border-radius: 12px; margin: 4px 0 8px; }
.row { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
.row button { flex: 1; }
h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: #9a9aa5; margin: 14px 0 6px; font-family: system-ui, sans-serif; }
input[type=text], select { flex: 1; background: #1c1c22; color: #fff; border: 1px solid #33333d; border-radius: 8px; padding: 8px; font-family: system-ui, sans-serif; }
button { background: #4f7cff; color: #fff; border: 0; border-radius: 8px; padding: 8px 14px; cursor: pointer; font-size: 14px; font-family: system-ui, sans-serif; }
button.ghost { background: #26262e; }
button:disabled { opacity: 0.5; }
#status { font-size: 12px; margin-top: 6px; min-height: 18px; color: #9a9aa5; font-family: system-ui, sans-serif; }
#status.ok { color: #7de2a8; }
#status.bad { color: #ff9d9d; }
.hint { font-size: 11px; color: #77777f; font-family: system-ui, sans-serif; }
`;

async function healthCheck(url: string): Promise<{ ok: boolean; msg: string }> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  try {
    const r = await fetch(url.replace(/\/$/, '') + '/health', { signal: ctrl.signal });
    if (!r.ok) return { ok: false, msg: `Server answered HTTP ${r.status} — is this the orchestrator?` };
    const j = await r.json();
    return { ok: true, msg: `Connected (${j.status ?? 'unknown'})` };
  } catch (e) {
    const m = e instanceof Error ? e.message : String(e);
    if (m.includes('abort')) return { ok: false, msg: 'Timed out — host unreachable or wrong port.' };
    return { ok: false, msg: `Cannot reach it (${m}). Check IP, port, firewall.` };
  } finally {
    clearTimeout(t);
  }
}

interface ReaderState {
  phase?: string;
  activeIdx?: number;
  total?: number;
  title?: string;
  novel?: string;
  hasOverlay?: boolean;
}

async function main(): Promise<void> {
  document.head.insertAdjacentHTML('beforeend', `<style>${CSS}</style>`);
  const app = document.getElementById('app')!;
  const s = await loadSettings();
  const esc = (v: unknown): string => String(v ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  app.innerHTML = `
    <div class="wrap">
    <div class="brand"><h1>InkCast</h1><span id="dot" class="dot" title="server status"></span></div>
    <div id="now">Opening reader…</div>
    <button id="big">Play</button>
    <div class="row">
      <button id="prev" class="ghost" title="Previous sentence">|&#9664;</button>
      <button id="next" class="ghost" title="Next sentence">&#9654;|</button>
      <button id="opts" class="ghost">Settings</button>
    </div>
    <div id="status"></div>
    <h3>InkCast server</h3>
    <div class="row"><input id="url" type="text" placeholder="http://192.168.x.x:8095" value="${esc(s.serverUrl)}" /></div>
    <div class="row"><button id="test">Test connection</button></div>
    <p class="hint">Find the URL in the terminal where you ran update.sh — it prints the LAN URL.</p>
    </div>`;
  const dot = document.getElementById('dot')!;
  const setDot = (cls: string) => { dot.className = 'dot' + (cls ? ' ' + cls : ''); };
  const urlEl = document.getElementById('url') as HTMLInputElement;
  const status = document.getElementById('status')!;
  const now = document.getElementById('now')!;
  const big = document.getElementById('big') as HTMLButtonElement;

  async function refreshState(): Promise<void> {
    let st: ReaderState | null = null;
    try {
      st = await chrome.runtime.sendMessage({ type: 'GET_STATE' }) as ReaderState | null;
    } catch { /* no content script: overlay never opened */ }
    if (!st || !st.hasOverlay) {
      big.textContent = 'Play';
      big.onclick = play;
      now.textContent = 'Open a chapter page, then Play.';
      return;
    }
    const label = [st.novel, st.title].filter(Boolean).join(' — ') || 'Chapter';
    const pos = typeof st.total === 'number' && st.total > 0 ? ` · ${(st.activeIdx ?? 0) + 1}/${st.total}` : '';
    now.textContent = `${label}${pos}`;
    if (st.phase === 'playing') {
      big.textContent = 'Pause';
      big.onclick = pause;
    } else if (st.phase === 'loading') {
      big.textContent = 'Starting…';
      big.onclick = null;
    } else {
      big.textContent = st.phase === 'idle' ? 'Play' : 'Resume';
      big.onclick = play;
    }
  }

  async function play(): Promise<void> {
    big.textContent = 'Starting…';
    try {
      const res = await chrome.runtime.sendMessage({ type: 'PLAY' }) as { ok?: boolean } | undefined;
      if (!res?.ok) {
        status.className = 'bad';
        status.textContent = 'Page is not listening — refresh the chapter page, then Play.';
      } else {
        status.className = '';
        status.textContent = '';
      }
    } catch {
      status.className = 'bad';
      status.textContent = 'Page is not listening — refresh the chapter page, then Play.';
    }
    setTimeout(() => void refreshState(), 800);
  }

  function pause(): void {
    chrome.runtime.sendMessage({ target: 'offscreen', type: 'PAUSE' });
    setTimeout(() => void refreshState(), 400);
  }

  document.getElementById('test')!.onclick = async () => {
    const url = urlEl.value.trim();
    status.className = '';
    status.textContent = 'Checking…';
    setDot('');
    const r = await healthCheck(url);
    status.className = r.ok ? 'ok' : 'bad';
    status.textContent = r.msg;
    setDot(r.ok ? 'ok' : 'bad');
    if (r.ok) await saveSettings({ ...(await loadSettings()), serverUrl: url });
  };
  // Show last-known server state on open.
  void (async () => {
    if (!s.serverUrl) return;
    const r = await healthCheck(s.serverUrl);
    setDot(r.ok ? 'ok' : 'bad');
    if (!r.ok) {
      status.className = 'bad';
      status.textContent = r.msg;
    }
  })();
  document.getElementById('prev')!.onclick = () => chrome.runtime.sendMessage({ target: 'offscreen', type: 'SEEK_PREV' });
  document.getElementById('next')!.onclick = () => chrome.runtime.sendMessage({ target: 'offscreen', type: 'SEEK_NEXT' });
  document.getElementById('opts')!.onclick = () => chrome.runtime.openOptionsPage();
  await refreshState();
}

void main();
