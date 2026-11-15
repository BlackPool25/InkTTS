/** Shadow-DOM mount: style isolation from arbitrary novel sites.
 * open root (devtools/a11y), reset inherited font sizing, Esc teardown
 * restoring the reader's scroll position.
 */
import { h, render } from 'preact';
import { Overlay, type OverlayProps } from './overlay';

const HOST_ID = 'inkcast-overlay-host';

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
  const style = document.createElement('style');
  style.textContent = CSS;
  shadow.appendChild(style);
  const root = document.createElement('div');
  shadow.appendChild(root);
  document.body.style.overflow = 'hidden';

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') teardown();
  };
  document.addEventListener('keydown', onKey, true);

  function teardown() {
    document.removeEventListener('keydown', onKey, true);
    render(null, root);
    root.remove();
    style.remove();
    document.body.style.overflow = prevOverflow;
    window.scrollTo(0, scrollY);
    props.onExit?.();
  }
  render(<Overlay {...props} />, root);
  return teardown;
}

export function rerenderOverlay(props: OverlayProps): void {
  const host = document.getElementById(HOST_ID);
  const root = host?.shadowRoot?.lastElementChild;
  if (root) render(<Overlay {...props} />, root as Element);
}

const CSS = `
.ink { position: fixed; inset: 0; z-index: 2147483647; display: flex; flex-direction: column;
  font-size: 16px; font-family: Georgia, 'Times New Roman', serif; }
.ink-theme-dark { background: rgba(8,8,12,.97); color: #f2f2f5; }
.ink-theme-ambient { background: radial-gradient(120% 100% at 50% 0%, #2b3a67 0%, #101018 60%, #08080c 100%); color: #f5f2ea; }
.ink-theme-sepia { background: #f4ecd8; color: #3b2f1e; }
.ink-theme-light { background: #ffffff; color: #1a1a1a; }
.ink-bar { display: flex; gap: 12px; align-items: center; padding: 12px 20px; opacity: .85; }
.ink-bar button { cursor: pointer; padding: 6px 12px; border-radius: 8px; border: 1px solid currentColor; background: transparent; color: inherit; }
.ink-scroll { flex: 1; overflow-y: auto; padding: 8vh 12vw 20vh; contain: layout style; }
.ink-line { position: relative; margin: 0.7em 0; font-size: 1.6em; line-height: 1.5; cursor: pointer;
  opacity: .92; transition: opacity .25s ease, transform .25s ease; overflow: hidden; border-radius: 8px; padding: 0 8px; }
.ink-active { opacity: 1; transform: scale(1.04); font-weight: 700; }
.ink-dim { opacity: .35; }
.ink-fill { position: absolute; inset: 0; background: rgba(255,255,255,.14); pointer-events: none; }
.ink-immersive .ink-line { font-size: 2em; }
.ink-error, .ink-endcard { margin: auto; text-align: center; max-width: 28em; padding: 24px; }
.ink-error button, .ink-endcard button { margin: 8px; padding: 8px 16px; cursor: pointer; }
.ink-degraded { padding: 8px 20px; background: #5a3b00; color: #ffe1a8; font-size: 14px; }
.ink-progress { margin-right: auto; }
`;
