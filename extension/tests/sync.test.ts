import { describe, expect, it } from 'vitest';
import { createSync, parseLRC } from '../entrypoints/content/sync';

const LRC = `[00:00.00]<0.00>Hello <0.40>world.\n[00:00.80]Plain line.\n`;
const lines = parseLRC(LRC, ['a.opus', 'b.opus']);

describe('sync', () => {
  it('word fill mid-word is ~fractional', () => {
    const s = createSync(lines);
    expect(s.lineAt(0.2)).toBe(0);
    const f = s.wordFill(0, 0.6); // halfway through "world."
    expect(f).toBeGreaterThan(0.5);
    expect(f).toBeLessThan(1);
  });

  it('gap between words holds prior fill, never blank', () => {
    const s = createSync(lines);
    expect(s.wordFill(0, 0.39)).toBeGreaterThan(0);
  });

  it('plain line degrades to line-sweep', () => {
    const s = createSync(lines);
    expect(s.lineAt(1.0)).toBe(1);
    expect(s.synthetic).toBe(false); // mixed doc: not all-synthetic
    const only = createSync([lines[1]]);
    expect(only.synthetic).toBe(true);
    expect(only.wordFill(0, 999)).toBe(1);
  });

  it('rate scales lookups', () => {
    const slow = createSync(lines, 0.5);
    expect(slow.lineAt(1.6)).toBe(1); // 1.6 wall = 0.8 media -> line 1
    const normal = createSync(lines, 1.0);
    expect(normal.lineAt(0.4)).toBe(0);
  });

  it('seek for sentence 10 is its file', () => {
    const many = Array.from({ length: 11 }, (_, i) => ({
      idx: i, start: i * 2, words: null, file: `s${i}.opus`,
    }));
    const s = createSync(many);
    expect(s.lineAt(20.5)).toBe(10);
    expect(many[10].file).toBe('s10.opus');
  });
});
