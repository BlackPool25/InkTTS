import { loadSettings, saveSettings } from '../shared/messaging';

async function healthCheck(url: string): Promise<string> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  try {
    const r = await fetch(`${url.replace(/\/$/, '')}/health`, { signal: ctrl.signal });
    if (!r.ok) return `Server answered HTTP ${r.status} — is this the orchestrator?`;
    const j = await r.json();
    return `OK (${j.status ?? 'unknown'}, engines: ${JSON.stringify(j.engines ?? {})})`;
  } catch (e) {
    const m = e instanceof Error ? e.message : String(e);
    if (m.includes('abort')) return 'Timed out — host unreachable or wrong port.';
    return `Cannot reach it (${m}). Check the IP, port, and firewall.`;
  } finally {
    clearTimeout(t);
  }
}

async function main(): Promise<void> {
  const app = document.getElementById('app')!;
  const s = await loadSettings();
  app.innerHTML = `
    <h3>InkCast server</h3>
    <input id="url" style="width:100%" placeholder="http://192.168.x.x:8095" value="${s.serverUrl}" />
    <button id="test">Test connection</button>
    <div id="status"></div>
    <div style="margin-top:12px">
      <button id="play">Play</button>
      <button id="pause">Pause</button>
    </div>
    <p style="font-size:12px">Find the URL in the terminal where you ran update.sh (it prints the LAN URL).</p>`;
  const urlEl = document.getElementById('url') as HTMLInputElement;
  const status = document.getElementById('status')!;
  document.getElementById('test')!.onclick = async () => {
    const url = urlEl.value.trim();
    status.textContent = 'Checking…';
    status.textContent = await healthCheck(url);
    if (status.textContent.startsWith('OK')) {
      await saveSettings({ ...s, serverUrl: url });
    }
  };
  document.getElementById('play')!.onclick = () => chrome.runtime.sendMessage({ type: 'PLAY' });
  document.getElementById('pause')!.onclick = () => chrome.runtime.sendMessage({ type: 'PAUSE' });
}

void main();
