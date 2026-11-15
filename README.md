# InkCast — LAN Novel Read-Along (GGUF TTS + Chrome Extension)

Read light novels on their websites, narrated by local AI voices with
karaoke-style highlighting. One Docker stack on your LAN renders audio;
a Chrome extension turns any chapter page into a focused reader.

- **Engines (GGUF, no torch):** Kokoro-82M Q8 (fast, CPU) + Chatterbox
  Turbo Q8 (expressive, GPU) via a single CrispASR binary. ~1 GB total,
  not ~20 GB.
- **Server:** tiny FastAPI orchestrator — preempt render queue, per-sentence
  Opus cache, Enhanced-LRC timings, SSE progress, OpenAI-compat shim.
- **Extension (Manifest V3):** Readability extraction with loud gates,
  full-takeover card-lyrics overlay, immersive focus mode, karaoke fill,
  click-to-seek, per-site voice/model overrides.

## Repo layout

- `tts-stack/` — `docker-compose.yml`, `crisp/` (engine image), `orchestrator/`
  (queue/cache/text pipeline/API), `proof/` (server E2E evidence).
- `extension/` — WXT + Preact source, `proof/` (browser E2E + screenshots).
- `docs/` — `ARCHITECTURE.md` (full internals), `superpowers/` (design docs + plans).

## Install (GPU host: AMD 7900 GRE class, or any x86_64 Linux)

Prereqs: Docker Engine, `/dev/dri` present (Mesa RADV). No ROCm toolkit, no
CUDA, no Python needed on the host.

```bash
cd tts-stack && cp .env.example .env
docker compose up -d --build kokoro orchestrator   # phase 1: Kokoro now
curl http://localhost:8095/health                  # -> {"status":"ready",...}
./update.sh   # prints your LAN URL, e.g. http://192.168.1.50:8095
docker compose up -d --build chatterbox              # phase 2: expressive voice
```

First boot downloads ~1 GB of GGUFs once into the `crisp-models` volume.
`update.sh` = pull + rebuild + print URL. No Watchtower (manual updates).

Extension (desktop Chrome): `pnpm install && pnpm build` in `extension/`,
then `chrome://extensions` → Developer mode → Load unpacked →
`extension/.output/chrome-mv3`. Paste the LAN URL in the popup, Test
connection, open a chapter, press the extension button, play.

## Use

- **Overlay:** current sentence large/white, neighbors dimmed, auto-follow.
  Click any sentence to seek. `Focus` = immersive (prev/current/next only).
  `Aa` = karaoke word fill. `Theme` cycles dark/ambient/sepia/light. Esc exits.
- **Settings (options page):** global voice/speed/model, theme, karaoke,
  focus span, immersive-on-open, per-site overrides (JSON), pronunciation
  rules (`PATTERN => say-as`), site extraction-rule overrides.
- **End of chapter:** end card with Play-next-chapter (uses extracted link).
  Image-only chapters, bot walls, empty pages: loud error states, never
  silent narration.

## Voices & models

- Kokoro: `af_heart` default; any `kokoro-voice-*.gguf` pack dropped next to
  the model file becomes selectable (orchestrator maps UI name → filename).
- Chatterbox: server default voice; per-request reference-audio voices are
  a future upload flow. No cloning ever.
- Text pipeline: site-rule container → Readability fallback → strips →
  promo sweep → next-page stitch (cap 5) → gates (short/boilerplate/
  challenge/image-only) → split → normalize (numbers/abbrevs/acronyms) →
  pron-dict → ≤200-char/≤40-word request budget → per-sentence Opus +
  Enhanced-LRC (word tags Kokoro, line-sweep Chatterbox).

## Verify

```bash
cd tts-stack && python3 -m pytest orchestrator/tests/ -q   # 28 tests
cd ../extension && pnpm vitest run && pnpm typecheck && pnpm build
CACHE_DIR=/tmp/e2e PYTHONPATH=tts-stack python3 tts-stack/proof/e2e_live.py
python3 extension/proof/browser_e2e_pw.py   # needs Playwright Chromium
```

Proof artifacts: `tts-stack/proof/SERVER_E2E.md`, `extension/proof/BROWSER_E2E.md`
+ screenshots.

## Troubleshooting

- `chatterbox` unhealthy on AMD: needs `/dev/dri` + `video`/`render` groups;
  falls back to CPU automatically (slow). NVIDIA hosts: use the CUDA build
  path instead (see ARCHITECTURE.md).
- Extension can't reach server: LAN IP changes (DHCP) — rerun `update.sh`;
  check firewall on 8095; Test-connection in the popup distinguishes
  DNS/timeout/refused.
- Challenge page: solve it in the tab, press Retry — overlay listens for
  the wall clearing.
- Wrong-language/garbage narration: extraction gates fail loud by design;
  per-site selector overrides live in options.
