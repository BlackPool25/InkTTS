import { describe, expect, it } from 'vitest';

describe('messaging envelope', () => {
  it('sendTo tags target and type', async () => {
    const seen: unknown[] = [];
    (globalThis as Record<string, unknown>).chrome = {
      runtime: { sendMessage: (m: unknown) => { seen.push(m); } },
      storage: {
        local: { get: async () => ({}), set: async () => undefined },
        session: { get: async () => ({}), set: async () => undefined },
        onChanged: { addListener: () => undefined, removeListener: () => undefined },
      },
    };
    const { sendTo, DEFAULT_SETTINGS } = await import('../entrypoints/shared/messaging');
    sendTo('offscreen', 'BOOT', { docId: 'd' });
    expect(seen).toEqual([{ target: 'offscreen', type: 'BOOT', payload: { docId: 'd' } }]);
    expect(DEFAULT_SETTINGS.focus).toBe(3);
    expect(DEFAULT_SETTINGS.immersiveDefault).toBe(false);
  });
});
