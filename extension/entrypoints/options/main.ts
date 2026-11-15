import { DEFAULT_SETTINGS, loadSettings, saveSettings, type ServerSettings } from '../shared/messaging';

async function main(): Promise<void> {
  const app = document.getElementById('app')!;
  const s = await loadSettings();
  const perSite = JSON.stringify(s.perSite, null, 2);
  app.innerHTML = `
    <h3>Voice & model</h3>
    <label>Voice <input id="voice" value="${s.voice}" /></label>
    <label>Speed <input id="speed" type="number" step="0.1" min="0.5" max="2" value="${s.speed}" /></label>
    <label>Model <select id="model">
      <option value="kokoro"${s.model === 'kokoro' ? ' selected' : ''}>Kokoro (fast, CPU)</option>
      <option value="chatterbox"${s.model === 'chatterbox' ? ' selected' : ''}>Chatterbox (expressive, GPU)</option>
    </select></label>
    <h3>Per-site overrides (domain -&gt; JSON)</h3>
    <textarea id="persite" rows="6" style="width:100%">${perSite.replace(/</g, '&lt;')}</textarea>
    <h3>Pronunciation rules (one per line: pattern =&gt; say-as)</h3>
    <textarea id="pron" rows="6" style="width:100%" placeholder="FBI => F B I"></textarea>
    <h3>Site extraction rules (JSON, optional)</h3>
    <textarea id="rules" rows="6" style="width:100%" placeholder='[{"match":"example.com","content":["#text"]}]'></textarea>
    <br/><button id="save">Save</button> <span id="ok"></span>`;
  document.getElementById('save')!.onclick = async () => {
    const next: ServerSettings = {
      ...s,
      voice: (document.getElementById('voice') as HTMLInputElement).value.trim() || DEFAULT_SETTINGS.voice,
      speed: parseFloat((document.getElementById('speed') as HTMLInputElement).value) || 1.0,
      model: (document.getElementById('model') as HTMLSelectElement).value as ServerSettings['model'],
      perSite: JSON.parse((document.getElementById('persite') as HTMLTextAreaElement).value || '{}'),
    };
    (next.siteRules as string | undefined) = (document.getElementById('rules') as HTMLTextAreaElement).value;
    (next.pron as string | undefined) = (document.getElementById('pron') as HTMLTextAreaElement).value;
    await saveSettings(next);
    (document.getElementById('ok') as HTMLElement).textContent = 'Saved.';
  };
}

void main();
