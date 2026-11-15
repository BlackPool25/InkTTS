/** Card-lyrics overlay: full-takeover reading layer.
 * sentences: extracted chapter text. activeIdx: current playback index.
 * immersive: prev/current/next only, OFF default. karaoke: word fill overlay.
 * focusSpan: 1/3/5 sharp lines. Esc exits (handled by mount layer).
 */
import { h } from 'preact';
import { useEffect, useRef } from 'preact/hooks';

export type Theme = 'dark' | 'ambient' | 'sepia' | 'light';
export type ExtractError = 'empty' | 'boilerplate' | 'challenge' | 'image-only';

export interface OverlayProps {
  sentences: string[];
  activeIdx: number;
  wordFrac?: number; // 0..1 fill within the active sentence
  immersive?: boolean;
  karaoke?: boolean;
  theme?: Theme;
  focusSpan?: 1 | 3 | 5;
  error?: { code: ExtractError; detail: string } | null;
  complete?: boolean;
  nextUrl?: string | null;
  degraded?: boolean;
  onSeek?: (idx: number) => void;
  onExit?: () => void;
  onRetry?: () => void;
  onNext?: () => void;
  onToggleImmersive?: () => void;
  onToggleKaraoke?: () => void;
  onCycleTheme?: () => void;
  onTheme?: (t: Theme) => void;
}

export const ERROR_COPY: Record<ExtractError, { title: string; body: string }> = {
  empty: { title: 'Nothing to read here', body: 'No chapter text found on this page. Open a chapter page and try again.' },
  boilerplate: { title: 'Page not recognized', body: 'This page does not look like a chapter. Open a chapter page and try again.' },
  challenge: { title: 'Blocked by a bot check', body: 'Solve the check on the page, then press Retry.' },
  'image-only': { title: 'Scanned pages', body: 'This chapter is images, not text. OCR is not supported yet.' },
};

export function visibleRange(active: number, total: number, immersive: boolean, span: 1 | 3 | 5): [number, number] {
  if (immersive) return [Math.max(0, active - 1), Math.min(total, active + 2)];
  const half = Math.floor(span / 2);
  return [Math.max(0, active - half - 4), Math.min(total, active + half + 5)];
}

export function Overlay(p: OverlayProps) {
  const immersive = p.immersive ?? false;
  const theme = p.theme ?? 'dark';
  const span = p.focusSpan ?? 3;
  const scroller = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Smooth-follow: active sentence eased to ~40% viewport, transform-only.
    const el = activeRef.current;
    const box = scroller.current;
    if (!el || !box) return;
    const target = el.offsetTop - box.clientHeight * 0.4 + el.clientHeight / 2;
    box.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
  }, [p.activeIdx, immersive]);

  if (p.error) {
    const copy = ERROR_COPY[p.error.code];
    return (
      <div className={`ink ink-theme-${theme}`} data-testid="overlay-error">
        <div className="ink-error">
          <h2>{copy.title}</h2>
          <p>{copy.body}</p>
          {p.error.code === 'challenge' && <button onClick={p.onRetry}>Retry</button>}
          <button onClick={p.onExit}>Close</button>
        </div>
      </div>
    );
  }

  const [lo, hi] = visibleRange(p.activeIdx, p.sentences.length, immersive, span);
  return (
    <div className={`ink ink-theme-${theme}${immersive ? ' ink-immersive' : ''}`} data-testid="overlay">
      {p.degraded && (
        <div className="ink-degraded" data-testid="degraded-badge">
          Backup voice — GPU offline. <span>Playback continues.</span>
        </div>
      )}
      <div className="ink-bar">
        <span className="ink-progress">{p.activeIdx + 1} / {p.sentences.length}</span>
        <button onClick={p.onToggleImmersive} aria-pressed={immersive}>
          {immersive ? 'Exit focus' : 'Focus'}
        </button>
        <button onClick={p.onToggleKaraoke} aria-pressed={!!p.karaoke} title="Karaoke word fill">Aa</button>
        <button onClick={p.onCycleTheme} title="Theme">Theme</button>
        <button onClick={p.onExit}>Close (Esc)</button>
      </div>
      <div className="ink-scroll" ref={scroller}>
        {p.sentences.slice(lo, hi).map((s, k) => {
          const idx = lo + k;
          const active = idx === p.activeIdx;
          const dim = !immersive && Math.abs(idx - p.activeIdx) > Math.floor(span / 2);
          return (
            <div
              key={idx}
              ref={active ? activeRef : undefined}
              data-testid={active ? 'line-active' : 'line'}
              data-idx={idx}
              className={`ink-line${active ? ' ink-active' : ''}${dim ? ' ink-dim' : ''}`}
              onClick={() => p.onSeek?.(idx)}
            >
              <span className="ink-text">{s}</span>
              {active && p.karaoke && (
                <span
                  className="ink-fill"
                  style={{ width: `${Math.round((p.wordFrac ?? 0) * 100)}%` }}
                />
              )}
            </div>
          );
        })}
      </div>
      {p.complete && (
        <div className="ink-endcard" data-testid="endcard">
          <h2>Finished</h2>
          {p.nextUrl ? <button onClick={p.onNext}>Play next chapter</button> : <p>No next chapter found.</p>}
        </div>
      )}
    </div>
  );
}
