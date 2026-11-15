import { DEFAULT_SETTINGS, loadSettings, saveSettings, type ServerSettings } from '../shared/messaging';

const CSS = `
:root { color-scheme: dark; }
body { font-family: system-ui, sans-serif; background: #101014; color: #f2f2f5; margin: 0; padding: 24px; max-width: 640px; }
h2 { font-size: 15px; margin: 28px 0 4px; }
p.sub { font-size: 12px; color: #9a9aa5; margin: 0 0 12px; }
.card { background: #17171d; border: 1px solid #26262e; border-radius: 12px; padding: 16px; margin: 12px 0; }
label { display: block; font-size: 13px; margin: 10px 0 4px; color: #c9c9d2; }
input[type=text], input[type=number], select, textarea { width: 100%; box-sizing: border-box; background: #1c1c22; color: #fff; border: 1px solid #33333d; border-radius: 8px; padding: 8px; font-family: inherit; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
button { background: #4f7cff; color: #fff; border: 0; border-radius: 8px; padding: 10px 18px; cursor: pointer; font-size: 14px; }
#ok { color: #7de2a8; margin-left: 12px; }
.swatches { display: flex; gap: 8px; margin-top: 8px; }
.swatches button { width: 44px; height: 44px; border-radius: 10px; padding: 0; border: 2px solid transparent; }
.swatches button[aria-pressed=true] { border-color: #fff; }
.sw-dark { background: #08080c; } .sw-ambient { background: radial-gradient(circle at 50% 0%, #2b3a67, #101018); }
.sw-sepia { background: #f4ecd8; } .sw-light { background: #ffffff; }
`;

const THEMES = ['dark', 'ambient', 'sepia', 'light'] as const;

async function main(): Promise<void> {
  document.head.insertAdjacentHTML('beforeend', `<style>${CSS}</style>`);
  const app = document.getElementById('app')!;
  const s = await loadSettings();
  app.innerHTML = `
    <h1 style="font-size:18px">InkCast settings</h1>
    <div class="card"><h2>Voice</h2>
      <p class="sub">Global default. Override per site below.</p>
      <div class="row">
        <div><label>Voice<input id="voice" type="text" value="${s.voice}" /></div>
        <div><label>Speed<input id="speed" type="number" step="0.1" min="0.5" max="2" value="${s.speed}" /></div>
      </div>
      <label>Model</label>
      <select id="model">
        <option value="kokoro"${s.model === 'kokoro' ? ' selected' : ''}>Kokoro — fast, CPU</option>
        <option value="chatterbox"${s.model === 'chatterbox' ? ' selected' : ''}>Chatterbox — expressive, GPU</option>
      </select>
    </div>
    <div class="card"><h2>Reading</h2>
      <p class="sub">Theme, karaoke word-fill, focus span and immersive default.</p>
      <label>Theme</label>
      <div class="swatches" id="themes">${THEMES.map((t) => `<button class="sw-${t}" data-t="${t}" title="${t}" aria-pressed="${s.theme === t}"></button>`).join('')}</div>
      <div class="row">
        <div><label>Karaoke word fill<input id="karaoke" type="text" value="${s.karaoke ? 'on' : 'off'}" /></div>
        <div><label>Focus span<select id="focus">${[1, 3, 5].map((n) => `<option${(s.focus ?? 3) === n ? ' selected' : ''}>${n}</option>`).join('')}</select></label></div>
      </div>
      <label>Immersive on open<input id="imm" type="text" value="${s.immersiveDefault ? 'on' : 'off'}" /></label>
    </div>
    <div class="card"><h2>Per-site overrides</h2>
      <p class="sub">Domain → voice/model JSON. Used when a site needs a different voice.</p>
      <textarea id="persite" rows="4">${JSON.stringify(s.perSite, null, 2).replace(/</g, '&lt;')}</textarea>
    </div>
    <div class="card"><h2>Pronunciation</h2>
      <p class="sub">One per line: <code>pattern =&gt; say-as</code>. Applied before synthesis.</p>
      <textarea id="pron" rows="4" placeholder="FBI => F B I">${((s.pron ?? '') as string).replace(/</g, '&lt;')}</textarea>
    </div>
    <div class="card"><h2>Site extraction rules</h2>
      <p class="sub">Optional JSON overrides when a site changes layout.</p>
      <textarea id="rules" rows="4" placeholder='[{"match":"example.com","content":["#text"]}]'>${((s.siteRules ?? '') as string).replace(/</g, '&lt;')}</textarea>
    </div>
    <button id="save">Save</button><span id="ok"></span>`;

  let theme = (s.theme as string) || 'dark';
  document.getElementById('themes')!.onclick = (e) => {
    const b = (e.target as HTMLElement).closest('button');
    if (!b) return;
    theme = b.dataset.t!;
    document.querySelectorAll('#themes button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
  };
  const parseOnOff = (v: string) => v.trim().toLowerCase().startsWith('on');
  document.getElementById('save')!.onclick = async () => {
    const next: ServerSettings = {
      ...s,
      voice: (document.getElementById('voice') as HTMLInputElement).value.trim() || DEFAULT_SETTINGS.voice,
      speed: parseFloat((document.getElementById('speed') as HTMLInputElement).value) || 1.0,
      model: (document.getElementById('model') as HTMLSelectElement).value as ServerSettings['model'],
      perSite: JSON.parse((document.getElementById('persite') as HTMLTextAreaElement).value || '{}'),
      theme, pron: (document.getElementById('pron') as HTMLTextAreaElement).value,
      siteRules: (document.getElementById('rules') as HTMLTextAreaElement).value,
      karaoke: parseOnOff((document.getElementById('karaoke') as HTMLInputElement).value),
      focus: parseInt((document.getElementById('focus') as HTMLSelectElement).value, 10),
      immersiveDefault: parseOnOff((document.getElementById('imm') as HTMLInputElement).value),
    } as ServerSettings;
    await saveSettings(next);
    chrome.runtime.sendMessage({ type: 'SETTINGS_CHANGED' });
    (document.getElementById('ok') as HTMLElement).textContent = 'Saved.';
  };
}

void main();
