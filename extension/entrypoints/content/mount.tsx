/** Shadow-DOM mount: style isolation from arbitrary novel sites.
 * open root (devtools/a11y), host on documentElement, ALL CSS in one
 * bundled <style> string (px only, explicit font/color, :host reset last).
 * Backdrop is solid rgba first, blur is enhancement-only.
 * `?inktts-selftest=1` asserts active styling + anchor; failure renders
 * a red STYLE MISSING banner instead of a silent unstyled box.
 */
import { h, render } from 'preact';
import { Overlay, type OverlayProps } from './overlay';

const HOST_ID = 'inktts-overlay-host';
let rootEl: HTMLElement | null = null;

export function runSelfTest(host: HTMLElement): string[] {
  const failures: string[] = [];
  const shadow = host.shadowRoot;
  const active = shadow?.querySelector('[data-testid=line-active]') as HTMLElement | null;
  if (!active) {
    failures.push('no active line');
    return failures;
  }
  const cs = getComputedStyle(active);
  if (parseFloat(cs.fontSize) < 20) failures.push(`active font-size ${cs.fontSize} < 20px`);
  const idle = shadow?.querySelector('[data-testid=line]:not([data-testid=line-active])') as HTMLElement | null;
  if (idle && !(parseFloat(getComputedStyle(idle).opacity) < parseFloat(cs.opacity))) {
    failures.push('active not brighter than neighbors');
  }
  const overlay = shadow?.querySelector('[data-testid=overlay]') as HTMLElement | null;
  if (overlay) {
    const bg = getComputedStyle(overlay).backgroundColor;
    if (bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent') failures.push('backdrop transparent');
  }
  return failures;
}

export function mountOverlay(props: OverlayProps): () => void {
  const prevOverflow = document.body.style.overflow;
  const scrollY = window.scrollY;
  let host = document.getElementById(HOST_ID);
  if (!host) {
    host = document.createElement('div');
    host.id = HOST_ID;
    document.documentElement.appendChild(host);
  }
  const shadow = host.shadowRoot ?? host.attachShadow({ mode: 'open' });
  shadow.innerHTML = '';
  const style = document.createElement('style');
  style.textContent = CSS;
  shadow.appendChild(style);
  const root = document.createElement('div');
  shadow.appendChild(root);
  rootEl = root;
  document.body.style.overflow = 'hidden';

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') teardown();
  };
  document.addEventListener('keydown', onKey, true);

  function teardown() {
    document.removeEventListener('keydown', onKey, true);
    render(null, root);
    rootEl = null;
    shadow.innerHTML = '';
    document.body.style.overflow = prevOverflow;
    window.scrollTo(0, scrollY);
    props.onExit?.();
  }
  render(<Overlay {...props} />, root);
  // Self-test: query param on the PAGE url (e.g. fixture?inktts-selftest=1).
  try {
    if (new URLSearchParams(location.search).has('inktts-selftest')) {
      const failures = runSelfTest(host);
      if (failures.length > 0) {
        const banner = document.createElement('div');
        banner.setAttribute('data-testid', 'style-missing');
        banner.textContent = `STYLE MISSING: ${failures.join('; ')}`;
        banner.setAttribute('style', 'position:fixed;top:0;left:0;right:0;background:#c00;color:#fff;padding:8px;z-index:2147483647;font:14px sans-serif;');
        shadow.appendChild(banner);
      }
    }
  } catch { /* file:// or opaque origin: skip self-test */ }
  return teardown;
}

export function rerenderOverlay(props: OverlayProps): void {
  if (rootEl) render(<Overlay {...props} />, rootEl);
}

const CSS = `
:host { all: initial; display: block; }
.ink { position: fixed; inset: 0; z-index: 2147483647; display: flex; flex-direction: column;
  font-size: 16px; line-height: 24px; font-family: Georgia, 'Times New Roman', serif;
  text-align: left; letter-spacing: normal; }
.inktts-theme-dark { background: rgba(8,8,12,0.97); background-color: #08080c; background-image: radial-gradient(120% 90% at 50% 0%, #141422 0%, #08080c 55%); color: #f2f2f5; }
.inktts-theme-ambient { background: rgba(12,12,20,0.94); background-image: radial-gradient(120% 100% at 50% 0%, #2b3a67 0%, #101018 60%, #08080c 100%); color: #f5f2ea; }
.inktts-theme-sepia { background: rgba(244,236,216,0.98); background-color: #f4ecd8; color: #3b2f1e; }
.inktts-theme-light { background: rgba(255,255,255,0.98); background-color: #ffffff; color: #1a1a1a; }
.inktts-bar { position: sticky; top: 0; z-index: 5; display: flex; gap: 12px; align-items: center; padding: 12px 20px; opacity: 0.9; transition: opacity 400ms ease; background: inherit; }
.inktts-bar-hidden { display: none; }
.inktts-bar button { cursor: pointer; padding: 6px 12px; border-radius: 8px; border: 1px solid currentColor; background: transparent; color: inherit; font-size: 14px; min-height: 32px; }
.inktts-bar button:hover { border-color: #7c9cff; }
.inktts-bar button:disabled { opacity: 0.35; cursor: default; }
.inktts-bar button.inktts-primary { background: #4f7cff; border-color: #4f7cff; color: #fff; font-weight: 600; }
.inktts-bar button[aria-pressed=true] { border-color: #7c9cff; }
.inktts-scroll { flex: 1; overflow-y: auto; padding: 48px 4vw 160px; contain: layout style; }
.inktts-line { position: relative; margin: 0 auto; max-width: 1020px; font-size: 22px; line-height: 37px; cursor: pointer;
  opacity: 0.55; transition: opacity 220ms ease, transform 220ms ease; padding: 6px 18px; }
.inktts-line:focus { outline: none; }
.inktts-text { position: relative; z-index: 1; }
.inktts-active { opacity: 1; font-weight: 700; font-size: 30px; line-height: 48px; text-shadow: 0 0 24px rgba(255,255,255,0.25); }
.inktts-dim { opacity: 0.35; }
.inktts-far { opacity: 0.12; }
.inktts-fill { position: absolute; left: 0; top: 0; bottom: 0; width: 100%; background: rgba(255,255,255,0.14); pointer-events: none; z-index: 0; }
.inktts-immersive .inktts-line { font-size: 26px; line-height: 42px; }
.inktts-immersive .inktts-active { font-size: 34px; line-height: 54px; }
.inktts-error, .inktts-endcard, .inktts-setup { margin: auto; text-align: center; max-width: 448px; padding: 24px; }
.inktts-error button, .inktts-endcard button, .inktts-setup button { margin: 8px; padding: 8px 16px; cursor: pointer; min-height: 36px; }
.inktts-degraded { padding: 8px 20px; background: #5a3b00; color: #ffe1a8; font-size: 14px; }
.inktts-follow { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); padding: 8px 16px; border-radius: 999px; border: 1px solid currentColor; background: rgba(0,0,0,0.6); color: inherit; cursor: pointer; }
.inktts-progress { margin-right: 12px; font-size: 14px; white-space: nowrap; }
.inktts-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; opacity: 0.75; margin-right: 12px; }
.inktts-panel { border-bottom: 1px solid rgba(255,255,255,0.1); padding: 12px 20px; display: flex; flex-direction: column; gap: 10px; background: rgba(0,0,0,0.25); }
.inktts-panel-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 14px; }
.inktts-panel-row > span { opacity: 0.7; }
.inktts-panel button { cursor: pointer; padding: 6px 12px; border-radius: 8px; border: 1px solid currentColor; background: transparent; color: inherit; font-size: 14px; min-height: 32px; }
.inktts-panel button[aria-pressed=true] { border-color: #7c9cff; }
.inktts-swatches { display: flex; gap: 8px; }
.inktts-swatches button { min-width: 40px; height: 32px; border-radius: 8px; padding: 0; border: 2px solid rgba(255,255,255,0.25); }
.inktts-swatches button[aria-pressed=true] { border-color: #7c9cff; box-shadow: 0 0 8px rgba(124,156,255,0.6); }
.inktts-sw { width: 40px; padding: 0; }
.inktts-swatches button.inktts-sw-dark { background: #08080c; }
.inktts-swatches button.inktts-sw-ambient { background: radial-gradient(circle at 50% 0%, #2b3a67, #101018); }
.inktts-swatches button.inktts-sw-sepia { background: #f4ecd8; }
.inktts-swatches button.inktts-sw-light { background: #ffffff; }
.inktts-endcard-row { display: flex; gap: 12px; justify-content: center; margin-top: 8px; }
.inktts-primary-btn { background: #4f7cff; border-color: #4f7cff; color: #fff; font-weight: 600; }
.inktts-hud { flex: none; padding: 8px 4vw 0; background: inherit; }
.inktts-hpbar { height: 22px; background: #141414; border: 3px solid #000; outline: 2px solid rgba(255,255,255,0.22); box-shadow: 4px 4px 0 rgba(0,0,0,0.55); position: relative; overflow: hidden; image-rendering: pixelated; }
.inktts-hpfill { height: 100%; background: repeating-linear-gradient(-55deg, #ffd23f 0 8px, #e9a800 8px 16px); box-shadow: inset 0 3px 0 rgba(255,255,255,0.45), inset 0 -3px 0 rgba(0,0,0,0.25); transition: width 400ms ease; }
.inktts-hptext { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font: 700 12px/1 ui-monospace, monospace; color: #fff; text-shadow: 2px 2px 0 #000; letter-spacing: 1px; }
.inktts-immersive .inktts-scroll { padding: 10vh 3vw 40vh; }
.inktts-immersive .inktts-line { max-width: 1200px; }
`;
