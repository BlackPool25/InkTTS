import { DEFAULT_SETTINGS, loadSettings, saveSettings } from '../shared/messaging';

const CSS = `
:root { color-scheme: dark; }
body { font-family: system-ui, sans-serif; background: #101014; color: #f2f2f5; margin: 0; padding: 16px; width: 340px; }
h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: #9a9aa5; margin: 16px 0 8px; }
.row { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
input[type=text], select { flex: 1; background: #1c1c22; color: #fff; border: 1px solid #33333d; border-radius: 8px; padding: 8px; }
button { background: #4f7cff; color: #fff; border: 0; border-radius: 8px; padding: 8px 14px; cursor: pointer; }
button.ghost { background: #26262e; }
#status { font-size: 12px; margin-top: 6px; min-height: 18px; color: #9a9aa5; }
#status.ok { color: #7de2a8; }
#status.bad { color: #ff9d9d; }
.transport { display: flex; gap: 8px; margin-top: 8px; }
.hint { font-size: 11px; color: #77777f; }
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

async function main(): Promise<void> {
  document.head.insertAdjacentHTML('beforeend', `<style>${CSS}</style>`);
  const app = document.getElementById('app')!;
  const s = await loadSettings();
  app.innerHTML = `
    <h3>InkCast server</h3>
    <div class="row"><input id="url" type="text" placeholder="http://192.168.x.x:8095" value="${s.serverUrl}" /></div>
    <div class="row"><button id="test">Test connection</button><span id="status"></span></div>
    <div class="transport">
      <button id="play">Play</button>
      <button id="pause" class="ghost">Pause</button>
      <button id="opts" class="ghost">Settings</button>
    </div>
    <p class="hint">Find the URL in the terminal where you ran update.sh — it prints the LAN URL.</p>`;
  const urlEl = document.getElementById('url') as HTMLInputElement;
  const status = document.getElementById('status')!;
  document.getElementById('test')!.onclick = async () => {
    const url = urlEl.value.trim();
    status.className = '';
    status.textContent = 'Checking…';
    const r = await healthCheck(url);
    status.className = r.ok ? 'ok' : 'bad';
    status.textContent = r.msg;
    if (r.ok) await saveSettings({ ...s, serverUrl: url });
  };
  document.getElementById('play')!.onclick = () => chrome.runtime.sendMessage({ type: 'PLAY' });
  document.getElementById('pause')!.onclick = () => chrome.runtime.sendMessage({ type: 'PAUSE' });
  document.getElementById('opts')!.onclick = () => chrome.runtime.openOptionsPage();
}

void main();
