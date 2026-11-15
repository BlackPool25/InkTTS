import { describe, expect, it, vi } from 'vitest';
import { h } from 'preact';
import renderToString from 'preact-render-to-string';
import { ERROR_COPY, Overlay, visibleRange } from '../entrypoints/content/overlay';

const SENTS = [
  'The lanterns swayed in the evening wind over the quiet harbor town.',
  'She said nothing and kept walking through the empty marketplace.',
  'He followed at a distance, silent as the morning fog itself.',
  'The gates closed behind them with a heavy iron sound.',
];

describe('overlay', () => {
  it('marks active sentence, dims out-of-band lines', () => {
    const many = [...SENTS, ...SENTS, ...SENTS];
    const html = renderToString(
      <Overlay sentences={many} activeIdx={6} focusSpan={1} />,
    );
    expect(html).toContain('line-active');
    expect(html).toContain('inktts-far');
  });

  it('immersive band is -2/+3', () => {
    const [lo, hi] = visibleRange(2, 4, true, 3);
    expect([lo, hi]).toEqual([0, 4]);
    expect(visibleRange(10, 53, true, 3)).toEqual([8, 13]);
  });

  it('renders one error state per code with copy', () => {
    for (const code of Object.keys(ERROR_COPY) as (keyof typeof ERROR_COPY)[]) {
      const html = renderToString(
        <Overlay sentences={[]} activeIdx={0} error={{ code, detail: 'x' }} />,
      );
      expect(html).toContain('overlay-error');
      expect(html).toContain(ERROR_COPY[code].title);
    }
  });

  it('end card offers next chapter', () => {
    const onNext = vi.fn();
    const html = renderToString(
      <Overlay sentences={SENTS} activeIdx={3} complete nextUrl="http://x/ch2" onNext={onNext} />,
    );
    expect(html).toContain('endcard');
    expect(html).toContain('Play next chapter');
  });

  it('degraded badge renders when set', () => {
    const html = renderToString(<Overlay sentences={SENTS} activeIdx={0} degraded />);
    expect(html).toContain('degraded-badge');
  });

  it('immersive is a dim wash: all lines render, out-of-window dimmed', () => {
    const html = renderToString(
      <Overlay sentences={SENTS} activeIdx={0} />,
    );
    expect((html.match(/data-idx/g) || []).length).toBe(4);
    expect(html).toContain('inktts-far');
    expect(html).not.toContain('inktts-hidden');
  });

  it('control bar auto-hides (class hook present)', () => {
    const html = renderToString(
      <Overlay sentences={SENTS} activeIdx={0} />,
    );
    expect(html).toContain('inktts-bar');
  });

  it('chapter progress bar fills with active index', () => {
    const html = renderToString(
      <Overlay sentences={SENTS} activeIdx={1} phase="playing" />,
    );
    expect(html).toContain('chapter-progress');
    expect(html).toContain('inktts-hpfill');
    expect(html).toContain('width:50%');
    expect(html).toContain('2 / 4');
  });

  it('phases drive the transport buttons', () => {
    const loading = renderToString(
      <Overlay sentences={SENTS} activeIdx={0} phase="loading" />,
    );
    expect(loading).toContain('Starting…');
    expect(loading).toContain('disabled');
    const paused = renderToString(
      <Overlay sentences={SENTS} activeIdx={0} phase="paused" />,
    );
    expect(paused).toContain('Resume');
    const playing = renderToString(
      <Overlay sentences={SENTS} activeIdx={0} phase="playing" />,
    );
    expect(playing).toContain('Pause');
    const idle = renderToString(
      <Overlay sentences={SENTS} activeIdx={0} phase="idle" />,
    );
    expect(idle).toContain('>Play<');
  });

  it('setup state renders when server URL missing', () => {
    const html = renderToString(
      <Overlay sentences={SENTS} activeIdx={0} setupNeeded />,
    );
    expect(html).toContain('overlay-setup');
    expect(html).toContain('Connect your TTS server');
  });

  it('follow pill appears when follow pauses', () => {
    const html = renderToString(
      <Overlay sentences={SENTS} activeIdx={0} followPaused />,
    );
    expect(html).toContain('follow-pill');
    expect(html).toContain('Follow');
  });
});
