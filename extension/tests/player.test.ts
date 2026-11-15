import { describe, expect, it, vi } from 'vitest';
import { resumeOffset } from '../entrypoints/offscreen/player';

describe('player contracts', () => {
  it('resume offset clamps into the file', () => {
    expect(resumeOffset(500, 2000)).toBe(500);
    expect(resumeOffset(-10, 2000)).toBe(0);
    expect(resumeOffset(5000, 2000)).toBe(1950);
  });

  it('pause stores hash+idx+offset, never blob URLs', async () => {
    const set = vi.fn();
    (globalThis as Record<string, unknown>).chrome = {
      storage: { session: { set, get: vi.fn() }, local: { get: vi.fn(), set: vi.fn() } },
      runtime: { sendMessage: vi.fn() },
    };
    const { savePlayState, loadPlayState } = await import('../entrypoints/shared/messaging');
    await savePlayState({ docId: 'd', hash: 'abc123', idx: 10, offsetMs: 450, rate: 1.0 });
    expect(set).toHaveBeenCalledWith({
      'inktts.playstate': { docId: 'd', hash: 'abc123', idx: 10, offsetMs: 450, rate: 1.0 },
    });
    const state = { 'inktts.playstate': { docId: 'd', hash: 'abc123', idx: 10, offsetMs: 450, rate: 1 } };
    (globalThis as Record<string, unknown>).chrome = {
      storage: { session: { set, get: vi.fn().mockResolvedValue(state) }, local: { get: vi.fn(), set: vi.fn() } },
      runtime: { sendMessage: vi.fn() },
    };
    const loaded = await loadPlayState();
    expect(loaded?.hash).toBe('abc123');
    expect(JSON.stringify(loaded)).not.toMatch(/blob:/);
  });
});
