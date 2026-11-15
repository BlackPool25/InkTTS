import { DEFAULT_SETTINGS, loadSettings, saveSettings, type ServerSettings } from '../shared/messaging';

const CSS = `
:root { color-scheme: dark; }
body { font-family: Georgia, 'Times New Roman', serif; background: #08080c; background-image: radial-gradient(120% 60% at 50% 0%, #141422 0%, #08080c 55%); background-attachment: fixed; color: #f2f2f5; margin: 0 auto; padding: 48px 20px 80px; max-width: 680px; }
h1 { text-align: center; font-size: 26px; margin: 0 0 6px; }
body > p.sub, .lede { text-align: center; }
p.sub { font-size: 13px; color: #9a9aa5; margin: 0 0 12px; font-family: system-ui, sans-serif; }
.card h2:first-child { margin-top: 0; }
h2 { font-size: 17px; margin: 28px 0 4px; }
.card { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.09); border-radius: 12px; padding: 20px; margin: 14px 0; backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); box-shadow: 0 8px 32px rgba(0,0,0,0.45); }
label { display: block; font-size: 13px; margin: 10px 0 4px; color: #c9c9d2; font-family: system-ui, sans-serif; }
input[type=text], input[type=number], select, textarea { width: 100%; box-sizing: border-box; background: #1c1c22; color: #fff; border: 1px solid #33333d; border-radius: 8px; padding: 8px; font-family: system-ui, sans-serif; }
input[type=range] { width: 100%; accent-color: #7c9cff; height: 32px; }
#speedval { font-variant-numeric: tabular-nums; color: #fff; }
input[type=checkbox] { width: 22px; height: 22px; accent-color: #7c9cff; vertical-align: middle; }
.check { display: flex; gap: 10px; align-items: center; font-size: 14px; font-family: system-ui, sans-serif; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
button { background: #7c9cff; border: 1px solid #7c9cff; color: #fff; border-radius: 8px; padding: 10px 18px; cursor: pointer; font-size: 14px; font-family: system-ui, sans-serif; font-weight: 600; }
button.ghost { background: transparent; border: 1px solid currentColor; color: inherit; font-weight: 400; }
#ok { color: #7de2a8; margin-left: 12px; font-family: system-ui, sans-serif; }
.swatches { display: flex; gap: 8px; margin-top: 8px; }
.swatches button { width: 44px; height: 44px; border-radius: 10px; padding: 0; border: 2px solid transparent; }
.swatches button[aria-pressed=true] { border-color: #7c9cff; box-shadow: 0 0 12px rgba(124,156,255,0.5); }
.sw-dark { background: #08080c; } .sw-ambient { background: radial-gradient(circle at 50% 0%, #2b3a67, #101018); }
.sw-sepia { background: #f4ecd8; } .sw-light { background: #ffffff; }
code { font-size: 12px; }
`;

const THEMES = ['dark', 'ambient', 'sepia', 'light'] as const;

async function main(): Promise<void> {
  document.head.insertAdjacentHTML('beforeend', `<style>${CSS}</style>`);
  const app = document.getElementById('app')!;
  const s = await loadSettings();
  const esc = (v: unknown): string => String(v ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  app.innerHTML = `
    <h1 style="font-size:18px">InkCast settings</h1>
    <div class="card"><h2>Server</h2>
      <p class="sub">Your LAN TTS stack URL. Same setting as the popup — either side saves it.</p>
      <label>Server URL<input id="serverurl" type="text" placeholder="http://192.168.x.x:8095" value="${esc(s.serverUrl)}" /></label>
      <div class="row"><div><button id="testserver" class="ghost">Test connection</button></div><div><span id="srvstatus"></span></div></div>
    </div>
    <div class="card"><h2>Voice</h2>
      <p class="sub">Global default, captured from the model — never typed. Override per site below.</p>
      <label>Model</label>
      <select id="model">
        <option value="kokoro"${s.model === 'kokoro' ? ' selected' : ''}>Kokoro — fast, CPU</option>
        <option value="chatterbox"${s.model === 'chatterbox' ? ' selected' : ''}>Chatterbox — expressive, GPU</option>
      </select>
      <label>Voice</label>
      <select id="voice">
        <option value="${esc(s.voice)}" selected>${esc(s.voice)} (saved)</option>
      </select>
      <p class="sub" id="voicenote">Loading voices from the server…</p>
      <label class="check" id="consentrow" style="display:none">I accept responsibility for using non-default voices <input id="consent" type="checkbox" /></label>
      <label>Speed <span id="speedval">${esc(s.speed ?? 1)}</span>x</label>
      <input id="speed" type="range" step="0.05" min="0.5" max="2.5" value="${esc(s.speed ?? 1)}" />
    </div>
    <div class="card"><h2>Reading</h2>
      <p class="sub">Theme, focus band width and autoplay.</p>
      <label>Theme</label>
      <div class="swatches" id="themes">${THEMES.map((t) => `<button class="sw-${t}" data-t="${t}" title="${t}" aria-pressed="${s.theme === t}"></button>`).join('')}</div>
      <div class="row">
        <div><label>Focus span<select id="focus">${[1, 3, 5].map((n) => `<option${(s.focus ?? 3) === n ? ' selected' : ''}>${n}</option>`).join('')}</select></label></div>
        <div><label class="check">Autoplay next chapter <input id="autoplay" type="checkbox"${s.autoplayNext !== false ? ' checked' : ''} /></label></div>
      </div>
    </div>
    <div class="card"><h2>Per-site overrides</h2>
      <p class="sub">Domain → voice/model JSON. Used when a site needs a different voice.</p>
      <textarea id="persite" rows="4">${esc(JSON.stringify(s.perSite, null, 2))}</textarea>
    </div>
    <div class="card"><h2>Pronunciation</h2>
      <p class="sub">One per line: <code>pattern =&gt; say-as</code>. Applied before synthesis.</p>
      <textarea id="pron" rows="4" placeholder="FBI => F B I">${esc(s.pron ?? '')}</textarea>
    </div>
    <div class="card"><h2>Site extraction rules</h2>
      <p class="sub">Optional JSON overrides when a site changes layout.</p>
      <textarea id="rules" rows="4" placeholder='[{"match":"example.com","content":["#text"]}]'>${esc(s.siteRules ?? '')}</textarea>
    </div>
    <button id="save">Save</button><span id="ok"></span>
    <div class="row" style="margin-top:12px">
      <div><button id="reset" class="ghost">Reset section defaults</button></div>
      <div><button id="backup" class="ghost">Export backup</button> <button id="restore" class="ghost">Import</button>
      <input id="restore-file" type="file" accept="application/json" style="display:none" /></div>
    </div>`;

  let theme = (s.theme as string) || 'dark';
  document.getElementById('themes')!.onclick = (e) => {
    const b = (e.target as HTMLElement).closest('button');
    if (!b) return;
    theme = b.dataset.t!;
    document.querySelectorAll('#themes button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
  };
  const okEl = (document.getElementById('ok') as HTMLElement);
  function fail(line: string): void {
    okEl.style.color = '#ff9d9d';
    okEl.textContent = line;
  }
  // Voices captured from the model itself: list live, filtered by model.
  // If the server is unreachable the saved value stays selectable.
  const voiceSel = document.getElementById('voice') as HTMLSelectElement;
  const modelSel = document.getElementById('model') as HTMLSelectElement;
  const note = document.getElementById('voicenote')!;
  async function refreshVoices(): Promise<void> {
    const model = modelSel.value;
    const keep = voiceSel.value || s.voice;
    try {
      const base = (document.getElementById('serverurl') as HTMLInputElement).value.trim().replace(/\/$/, '')
        || s.serverUrl.replace(/\/$/, '');
      if (!base) throw new Error('no server URL');
      const r = await fetch(`${base}/voices`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json() as { kokoro?: string[]; chatterbox?: string[] };
      const list = (model === 'kokoro' ? j.kokoro : j.chatterbox) ?? [];
      voiceSel.innerHTML = '';
      const names = list.length > 0 ? list : [keep || 'af_heart'];
      for (const n of names) {
        const o = document.createElement('option');
        o.value = n;
        o.textContent = n;
        if (n === keep) o.selected = true;
        voiceSel.appendChild(o);
      }
      if (!names.includes(keep) && keep) {
        const o = document.createElement('option');
        o.value = keep;
        o.textContent = `${keep} (saved)`;
        o.selected = true;
        voiceSel.appendChild(o);
      }
      note.textContent = list.length > 0
        ? `${list.length} voice${list.length === 1 ? '' : 's'} from the model.`
        : 'Server unreachable — saved voice kept.';
    } catch {
      note.textContent = 'Server unreachable — saved voice kept.';
      if (![...voiceSel.options].some((o) => o.value === keep)) {
        const o = document.createElement('option');
        o.value = keep;
        o.textContent = `${keep} (saved)`;
        o.selected = true;
        voiceSel.appendChild(o);
      }
    }
  }
  const consentRow = document.getElementById('consentrow')!;
  const consentEl = document.getElementById('consent') as HTMLInputElement;
  try { consentEl.checked = !!s.voiceConsent; } catch { /* ignore */ }
  function paintConsent(): void {
    const need = modelSel.value === 'chatterbox' && voiceSel.value !== 'default';
    consentRow.style.display = need ? '' : 'none';
  }
  modelSel.onchange = () => { void refreshVoices().then(paintConsent); };
  voiceSel.onchange = paintConsent;
  void refreshVoices().then(paintConsent);
  // Speed slider with live value.
  const speedEl = document.getElementById('speed') as HTMLInputElement;
  const speedVal = document.getElementById('speedval')!;
  const paintSpeed = () => { speedVal.textContent = parseFloat(speedEl.value).toFixed(2); };
  speedEl.oninput = paintSpeed;
  paintSpeed();
  document.getElementById('save')!.onclick = async () => {
    // Validation-first: block save naming the offending line.
    let perSite: ServerSettings['perSite'] = {};
    try {
      perSite = JSON.parse((document.getElementById('persite') as HTMLTextAreaElement).value || '{}');
    } catch (e) {
      fail(`Per-site JSON invalid: ${(e as Error).message}. Fix it or Reset.`);
      return;
    }
    const speed = parseFloat((document.getElementById('speed') as HTMLInputElement).value);
    if (!(speed >= 0.5 && speed <= 2.5)) {
      fail('Speed must be 0.5–2.5.');
      return;
    }
    const voice = (document.getElementById('voice') as HTMLSelectElement).value.trim();
    if (!/^[a-z0-9_+-]{1,64}$/i.test(voice || DEFAULT_SETTINGS.voice)) {
      fail('Voice has illegal characters.');
      return;
    }
    const model = (document.getElementById('model') as HTMLSelectElement).value as ServerSettings['model'];
    const voiceConsent = (document.getElementById('consent') as HTMLInputElement).checked;
    if (model === 'chatterbox' && voice !== 'default' && !voiceConsent) {
      fail('Non-default Chatterbox voices need the responsibility checkbox ticked.');
      return;
    }
    const next: ServerSettings = {
      ...s,
      voice: voice || DEFAULT_SETTINGS.voice,
      speed,
      model,
      voiceConsent,
      perSite,
      theme, pron: (document.getElementById('pron') as HTMLTextAreaElement).value,
      siteRules: (document.getElementById('rules') as HTMLTextAreaElement).value,
      focus: parseInt((document.getElementById('focus') as HTMLSelectElement).value, 10),
      immersiveDefault: true, // immersive-only reader; kept for back-compat
      autoplayNext: (document.getElementById('autoplay') as HTMLInputElement).checked,
    } as ServerSettings;
    await saveSettings(next);
    chrome.runtime.sendMessage({ target: 'sw', type: 'SETTINGS_CHANGED' });
    okEl.style.color = '';
    okEl.textContent = 'Saved.';
  };
  document.getElementById('testserver')!.onclick = async () => {
    const st = document.getElementById('srvstatus')!;
    const url = (document.getElementById('serverurl') as HTMLInputElement).value.trim().replace(/\/$/, '');
    st.textContent = 'Checking…';
    try {
      const r = await fetch(url + '/health');
      if (!r.ok) throw new Error(`HTTP ${r.status} — is this the orchestrator?`);
      const cur = await loadSettings();
      await saveSettings({ ...cur, serverUrl: url });
      st.textContent = 'Connected.';
    } catch (e) {
      st.textContent = `Cannot reach it (${(e as Error).message}). Check IP, port, firewall.`;
    }
  };
  document.getElementById('reset')!.onclick = async () => {
    await saveSettings({ ...DEFAULT_SETTINGS, serverUrl: (await loadSettings()).serverUrl });
    okEl.textContent = 'Defaults restored (server URL kept). Reload the page.';
  };
  document.getElementById('backup')!.onclick = async () => {
    const cur = await loadSettings();
    const blob = new Blob([JSON.stringify(cur, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'inkcast-settings.json';
    a.click();
  };
  document.getElementById('restore')!.onclick = () => (document.getElementById('restore-file') as HTMLInputElement).click();
  (document.getElementById('restore-file') as HTMLInputElement).onchange = async (e) => {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (!f) return;
    try {
      await saveSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(await f.text()) });
      okEl.textContent = 'Backup imported. Reload the page.';
    } catch (err) {
      fail(`Backup invalid: ${(err as Error).message}`);
    }
  };
}

void main();
