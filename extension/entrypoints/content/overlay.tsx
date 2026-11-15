/** Read-along overlay: lyrics-style reading layer (no boxes — the active
 * line is promoted by size and light, neighbors dim, the rest fall back).
 * immersive = Edge-style line focus: a lit band over the SAME list, the
 * rest visible behind a dim wash (never removed). Controls auto-hide.
 * karaoke: gradient word-fill wipe. focusSpan: 1/3/5 sharp lines.
 * Tap = seek (restart that sentence). Esc exits (handled by mount layer).
 */
import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';

export type Theme = 'dark' | 'ambient' | 'sepia' | 'light';
export type ExtractError = 'empty' | 'boilerplate' | 'challenge' | 'locked' | 'image-only';

export interface OverlayProps {
  sentences: string[];
  activeIdx: number;
  chapterTitle?: string;
  novelTitle?: string | null;
  author?: string | null;
  fallbackNotice?: boolean;
  theme?: Theme;
  focusSpan?: 1 | 3 | 5;
  error?: { code: ExtractError; detail: string } | null;
  complete?: boolean;
  nextUrl?: string | null;
  prevUrl?: string | null;
  degraded?: boolean;
  setupNeeded?: boolean;
  followPaused?: boolean;
  loading?: boolean;
  loadingStage?: 'boot' | 'manifest';
  playing?: boolean;
  audioError?: string | null;
  phase?: 'idle' | 'loading' | 'playing' | 'paused' | 'error' | 'done';
  onSeek?: (idx: number) => void;
  onExit?: () => void;
  onRetry?: () => void;
  onNext?: () => void;
  onPrevChapter?: () => void;
  onNextChapter?: () => void;
  onCycleTheme?: () => void;
  onTheme?: (t: Theme) => void;
  onResumeFollow?: () => void;
  onPauseFollow?: () => void;
  onPause?: () => void;
  onPlay?: () => void;
  onPrev?: () => void;
  onNextSentence?: () => void;
  rate?: number;
  onCycleSpeed?: () => void;
  onSettingsChange?: (patch: { theme?: string; speed?: number }) => void;
}

export const ERROR_COPY: Record<ExtractError, { title: string; body: string }> = {
  empty: { title: 'Nothing to read here', body: 'No chapter text found on this page. Open a chapter page and try again.' },
  boilerplate: { title: 'Page not recognized', body: 'This page does not look like a chapter. Open a chapter page and try again.' },
  challenge: { title: 'Blocked by a bot check', body: 'Solve the check on the page, then press Retry.' },
  locked: { title: 'Sign-in required', body: 'This chapter sits behind a login or paywall. Sign in on the page, then press Retry.' },
  'image-only': { title: 'Scanned pages', body: 'This chapter is images, not text. OCR is not supported yet.' },
};

export function visibleRange(active: number, total: number, immersive: boolean, span: 1 | 3 | 5): [number, number] {
  if (immersive) {
    // Lit band scales with focus span: 1 -> 3 lines, 3 -> 5, 5 -> 7.
    const half = Math.floor(span / 2);
    return [Math.max(0, active - (half + 1)), Math.min(total, active + (half + 2))];
  }
  const half = Math.floor(span / 2);
  return [Math.max(0, active - half - 4), Math.min(total, active + half + 5)];
}

/** Karaoke wipe (future): gradient-mask fill driven by rAF word timings
 * once the server ships word tags. Currently unwired — no dead UI
 * references it. */
export function KaraokeFill({ frac }: { frac: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, frac)) * 100);
  return (
    <span
      className="inktts-fill"
      data-testid="karaoke-fill"
      style={{ clipPath: `inset(0 ${100 - pct}% 0 0)` }}
    />
  );
}

export function Overlay(p: OverlayProps) {
  const immersive = true; // immersive-only reader
  const theme = p.theme ?? 'dark';
  const span = p.focusSpan ?? 3;
  const scroller = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLDivElement>(null);
  const followTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  useEffect(() => {
    if (p.followPaused) return;
    // Smooth-follow: active sentence eased to ~40% viewport, transform-only.
    const el = activeRef.current;
    const box = scroller.current;
    if (!el || !box) return;
    const target = el.offsetTop - box.clientHeight * 0.4 + el.clientHeight / 2;
    box.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
  }, [p.activeIdx, immersive, p.followPaused]);

  useEffect(() => () => {
    if (followTimer.current) clearTimeout(followTimer.current);
  }, []);

  if (p.error) {
    const copy = ERROR_COPY[p.error.code];
    return (
      <div className={`ink inktts-theme-${theme}`} data-testid="overlay-error">
        <div className="inktts-error">
          <h2>{copy.title}</h2>
          <p>{copy.body}</p>
          {p.error.code === 'challenge' && <button onClick={p.onRetry}>Retry</button>}
          {p.error.code === 'locked' && <button onClick={p.onRetry}>Retry</button>}
          <button onClick={p.onExit}>Close</button>
        </div>
      </div>
    );
  }

  if (p.setupNeeded) {
    return (
      <div className={`ink inktts-theme-${theme}`} data-testid="overlay-setup">
        <div className="inktts-setup">
          <h2>Connect your TTS server</h2>
          <p>Paste your LAN URL in the popup, Test connection, then press Play.</p>
          <button onClick={p.onPlay}>Play</button>
          <button onClick={p.onExit}>Close</button>
        </div>
      </div>
    );
  }

  const [lo, hi] = visibleRange(p.activeIdx, p.sentences.length, true, span);
  return (
    <div className={`ink inktts-theme-${theme} inktts-immersive`} data-testid="overlay" role="log" aria-live="polite">
      {p.audioError && (
        <div className="inktts-degraded" data-testid="audio-error">
          Audio failed: {p.audioError} <button onClick={p.onPlay}>Retry</button>
        </div>
      )}
      {p.degraded && (
        <div className="inktts-degraded" data-testid="degraded-badge">
          Backup voice — GPU offline. <span>Playback continues.</span>
        </div>
      )}
      <div className="inktts-bar">
        <span className="inktts-progress" data-testid="chapter-position">{p.activeIdx + 1} / {p.sentences.length}</span>
        <span className="inktts-title" data-testid="chapter-title" title={[p.novelTitle, p.author ? `by ${p.author}` : ''].filter(Boolean).join(' ') || undefined}>
          {p.novelTitle ? `${p.novelTitle} — ` : ''}{p.chapterTitle || ''}{p.author ? ` · ${p.author}` : ''}
        </span>
        {p.loading && <span className="inktts-progress" data-testid="loading">{p.loadingStage === 'manifest' ? 'Rendering audio…' : 'Starting player…'}</span>}
        {(p.phase === 'playing') ? (
          <button onClick={p.onPause} aria-label="Pause">Pause</button>
        ) : (p.phase === 'loading') ? (
          <button disabled aria-label="Starting">Starting…</button>
        ) : (p.phase === 'paused' || p.phase === 'idle') ? (
          <button className="inktts-primary" onClick={p.onPlay} aria-label={p.phase === 'idle' ? 'Play' : 'Resume'}>{p.phase === 'idle' ? 'Play' : 'Resume'}</button>
        ) : null}
        <button onClick={p.onPrev} aria-label="Previous sentence" disabled={p.sentences.length === 0}>|&#9664;</button>
        <button onClick={p.onNextSentence} aria-label="Next sentence" disabled={p.sentences.length === 0}>&#9654;|</button>
        <button onClick={p.onPrevChapter} aria-label="Previous chapter" title="Previous chapter" disabled={!p.prevUrl}>&#171;</button>
        <button onClick={p.onNextChapter} aria-label="Next chapter" title="Next chapter" disabled={!p.nextUrl}>&#187;</button>
        <button onClick={p.onCycleSpeed} aria-label="Playback speed" title="Playback speed" disabled={p.phase !== 'playing' && p.phase !== 'paused'}>{(p.rate ?? 1).toFixed(2).replace(/0$/, '')}x</button>
        <button onClick={p.onCycleTheme} title="Theme">Theme</button>
        <button onClick={() => setPanelOpen((v) => !v)} aria-pressed={panelOpen} title="Reading settings">Settings</button>
        <button onClick={p.onExit}>Close (Esc)</button>
      </div>
      {panelOpen && (
        <div className="inktts-panel" data-testid="settings-panel">
          {p.fallbackNotice && (
            <div className="inktts-panel-row" data-testid="fallback-note">
              <span>Generic extraction — site rule missed this page</span>
            </div>
          )}
          <div className="inktts-panel-row">
            <span>Theme</span>
            <div className="inktts-swatches">
              {(['dark', 'ambient', 'sepia', 'light'] as const).map((t) => (
                <button
                  key={t}
                  className={`inktts-sw inktts-sw-${t}`}
                  data-t={t}
                  title={t}
                  aria-pressed={(p.theme ?? 'dark') === t}
                  onClick={() => p.onSettingsChange?.({ theme: t })}
                />
              ))}
            </div>
          </div>
          <div className="inktts-panel-row">
            <span>Speed</span>
            <div className="inktts-swatches">
              {[0.8, 1.0, 1.25, 1.5, 2.0].map((s) => (
                <button
                  key={s}
                  aria-pressed={(p.rate ?? 1) === s}
                  onClick={() => p.onSettingsChange?.({ speed: s })}
                >{s}x</button>
              ))}
            </div>
          </div>
        </div>
      )}
      <div className="inktts-hud" data-testid="chapter-progress" title="Chapter progress">
        <div className="inktts-hpbar">
          <div
            className="inktts-hpfill"
            style={{ width: `${p.sentences.length > 0 ? Math.round(((p.activeIdx + 1) / p.sentences.length) * 100) : 0}%` }}
          />
          <div className="inktts-hptext">{p.sentences.length > 0 ? `${p.activeIdx + 1} / ${p.sentences.length}` : ''}</div>
        </div>
      </div>
      <div
        className="inktts-scroll"
        ref={scroller}
        onWheel={() => {
          // User takes the wheel: pause follow now, show the Follow pill,
          // auto re-lock after 5s of no scrolling.
          p.onPauseFollow?.();
          if (followTimer.current) clearTimeout(followTimer.current);
          followTimer.current = setTimeout(() => {
            // Auto re-lock after 5s of no scrolling.
            p.onResumeFollow?.();
          }, 5000);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
            e.preventDefault();
            p.onSeek?.(Math.min(p.sentences.length - 1, p.activeIdx + 1));
          } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
            e.preventDefault();
            p.onSeek?.(Math.max(0, p.activeIdx - 1));
          } else if (e.key === 'Enter') {
            e.preventDefault();
            p.onSeek?.(p.activeIdx);
          }
        }}
      >
        {p.sentences.map((s, idx) => {
          const active = idx === p.activeIdx;
          const inWindow = idx >= lo && idx < hi;
          const far = immersive && !inWindow;
          const dim = !far && !immersive && Math.abs(idx - p.activeIdx) > Math.floor(span / 2);
          return (
            <div
              key={idx}
              ref={active ? activeRef : undefined}
              data-testid={active ? 'line-active' : 'line'}
              data-idx={idx}
              tabIndex={active ? 0 : -1}
              className={`inktts-line${active ? ' inktts-active' : ''}${dim ? ' inktts-dim' : ''}${far ? ' inktts-far' : ''}`}
              onClick={() => p.onSeek?.(idx)}
            >
              <span className="inktts-text">{s}</span>
            </div>
          );
        })}
      </div>
      {p.followPaused && (
        <button className="inktts-follow" data-testid="follow-pill" onClick={p.onResumeFollow}>
          Follow
        </button>
      )}
      {p.complete && (
        <div className="inktts-endcard" data-testid="endcard">
          <h2>Finished</h2>
          {p.novelTitle || p.chapterTitle ? (
            <p>{[p.novelTitle, p.chapterTitle].filter(Boolean).join(' — ')}{p.author ? ` · by ${p.author}` : ''}</p>
          ) : null}
          <div className="inktts-endcard-row">
            {p.prevUrl ? <button onClick={p.onPrevChapter}>Previous chapter</button> : null}
            {p.nextUrl ? <button className="inktts-primary-btn" onClick={p.onNext}>Play next chapter</button> : <p>No next chapter found.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
