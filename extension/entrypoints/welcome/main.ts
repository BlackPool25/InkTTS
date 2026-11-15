import { loadSettings, saveSettings } from '../shared/messaging';

const CSS = `
body { font-family: system-ui, sans-serif; background: #101014; color: #f2f2f5; margin: 0; padding: 48px 24px; }
.card { max-width: 560px; margin: 0 auto; background: #17171d; border: 1px solid #26262e; border-radius: 16px; padding: 32px; }
input { width: 100%; box-sizing: border-box; background: #1c1c22; color: #fff; border: 1px solid #33333d; border-radius: 8px; padding: 10px; margin: 12px 0; }
button { background: #4f7cff; color: #fff; border: 0; border-radius: 8px; padding: 10px 18px; cursor: pointer; }
#status { font-size: 13px; margin-top: 8px; min-height: 20px; }
ol { line-height: 1.7; font-size: 14px; }
`;

async function main(): Promise<void> {
  document.head.insertAdjacentHTML('beforeend', `<style>${CSS}</style>`);
  const app = document.getElementById('app')!;
  const s = await loadSettings();
  const esc = (v: unknown): string => String(v ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  app.innerHTML = `
    <div class="card">
      <h1>Welcome to InkTTS</h1>
      <ol>
        <li>Start the TTS stack on your LAN machine (<code>update.sh</code> prints the URL).</li>
        <li>Paste that URL below and Test connection.</li>
        <li>Open any novel chapter, press Play in the popup.</li>
      </ol>
      <input id="url" type="text" placeholder="http://192.168.x.x:8095" value="${esc(s.serverUrl)}" />
      <button id="test">Test connection</button>
      <button id="open" style="margin-left:8px">Open all settings</button>
      <div id="status"></div>
    </div>`;
  const status = document.getElementById('status')!;
  document.getElementById('test')!.onclick = async () => {
    const url = (document.getElementById('url') as HTMLInputElement).value.trim().replace(/\/$/, '');
    status.textContent = 'Checking…';
    try {
      const r = await fetch(url + '/health');
      if (!r.ok) throw new Error(`HTTP ${r.status} — is this the orchestrator?`);
      await saveSettings({ ...s, serverUrl: url });
      status.textContent = 'Connected. You can close this tab.';
    } catch (e) {
      status.textContent = `Cannot reach it (${(e as Error).message}). Check IP, port, firewall.`;
    }
  };
  document.getElementById('open')!.onclick = () => chrome.runtime.openOptionsPage();
}

void main();
