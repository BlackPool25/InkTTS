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
  it('marks active sentence, dims far lines', () => {
    const html = renderToString(
      <Overlay sentences={SENTS} activeIdx={1} focusSpan={1} />,
    );
    expect(html).toContain('line-active');
    expect(html).toContain('ink-dim');
  });

  it('immersive shows only +-1', () => {
    const [lo, hi] = visibleRange(2, 4, true, 3);
    expect([lo, hi]).toEqual([1, 4]);
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
});
