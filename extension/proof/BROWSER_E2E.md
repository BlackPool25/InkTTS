# Browser E2E evidence (2026-11-15, Playwright Chromium + unpacked ext)

Run: `python3 proof/browser_e2e_pw.py` (unbranded Chromium; branded
Chrome CLI-sideloads nothing — log-verified `ignoring`).

- worker: ours (manifest-name matched, no ID guessing)
- overlay mounted: True, sentences rendered: 3
- active line follows playback; tap sentence -> highlight moves
- challenge page -> loud error + Retry (never narrated)
- pageerrors: none
- screenshot: proof/overlay.png (card-lyrics, opaque backdrop)

E2E-caught product bugs fixed along the way:
- content script matched https-only (fixture is http) -> http+https
- `tabs` permission missing (tabs.query blind) -> added to manifest
- stale-Chrome port squats -> script-scoped cleanup
