// Offscreen audio owner (AUDIO_PLAYBACK reason). Owns ALL of: manifest
// (content scripts must never fetch LAN — mixed-content), SSE, tap-driven
// render priority, gapless Web Audio chaining, resume by {hash,idx,offsetMs}.
// Service workers die ~30s idle and cannot hold SSE or play audio.
import { loadPlayState, loadSettings, savePlayState } from '../shared/messaging';

export interface ServerSentence { idx: number; text: string }

const WATCHDOG_MS = 45000;

export class Player {
  private ctx: AudioContext | null = null;
  private queue: ServerSentence[] = [];
  private docId = '';
  private serverUrl = '';
  private at = 0;
  private source: AudioBufferSourceNode | null = null;
  private startedAt = 0;
  private playing = false;
  private rate = 1.0;
  private sse: EventSource | null = null;
  private gen = 0; // invalidates in-flight gap retries on pause/seek/stop/boot
  private buffers = new Map<number, AudioBuffer>();
  private hashes = new Map<number, string>();
  private fetching = new Set<number>();
  private lastEventId = 0;
  private watchdog: ReturnType<typeof setTimeout> | null = null;
  private backoff = 1000;
  private manifestSeq = 0;
  private pausedOffsetMs = 0;
  private gapTries = new Map<number, number>();
  private fetch202 = new Map<number, number>();

  private audio(): AudioContext {
    if (!this.ctx) this.ctx = new AudioContext({ sampleRate: 24000 });
    // A context born without a user gesture starts suspended; resume is
    // harmless when already running and required otherwise.
    void this.ctx.resume().catch(() => undefined);
    return this.ctx;
  }

  /** BOOT with raw paragraphs: WE own POST /manifest (content never fetches LAN). */
  async boot(docId: string, paragraphs: string[], startIdx: number, serverUrl: string): Promise<void> {
    const fail = (step: string, e: unknown) => {
      chrome.runtime.sendMessage({ target: 'content', type: 'AUDIO_ERROR',
        payload: { reason: `boot/${step}: ${(e as Error)?.message ?? e}` } });
    };
    try {
      this.stopAll(); // also invalidates any in-flight manifest POST
      const seq = this.manifestSeq;
      this.docId = docId;
      this.serverUrl = serverUrl.replace(/\/$/, '');
      let st: Awaited<ReturnType<typeof loadPlayState>>;
      try {
        st = await loadPlayState();
      } catch (e) { fail('state', e); return; }
      const settings = await loadSettings().catch(() => null);
      this.rate = settings?.speed ?? 1.0;
      // Per-site voice/model override: the stored map wins for this host.
      let voice = settings?.voice;
      let model = settings?.model;
      try {
        const host = new URL(docId).hostname;
        const ov = (settings?.perSite as Record<string, { voice?: string; model?: string }> | undefined)?.[host];
        if (ov?.voice) voice = ov.voice;
        if (ov?.model === 'kokoro' || ov?.model === 'chatterbox') model = ov.model;
      } catch { /* docId not a URL: global voice applies */ }
      let sentences: ServerSentence[] = paragraphs.map((text, idx) => ({ idx, text }));
    const ctrl = new AbortController();
    const abortTimer = setTimeout(() => ctrl.abort(), 20000);
    try {
      const r = await fetch(`${this.serverUrl}/manifest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          doc_id: docId,
          sentences: paragraphs.map((text) => ({ text })),
          voice, speed: settings?.speed, model,
          pron: settings?.pron ?? '',
          consent: settings?.voiceConsent
            ? 'Operator accepts responsibility for using this non-default reference voice for playback (attested in InkCast settings).'
            : '',
        }),
      });
      if (!r.ok) throw new Error(`manifest HTTP ${r.status}`);
      const man = await r.json() as { sentences?: { text: string }[] };
      if (seq !== this.manifestSeq) return; // superseded by newer BOOT
      if (Array.isArray(man.sentences) && man.sentences.length > 0) {
        sentences = man.sentences.map((s, idx) => ({ idx, text: s.text }));
      }
    } catch (e) {
      clearTimeout(abortTimer);
      // No silent spinner: name the failure on the overlay with Retry.
      const msg = (e instanceof DOMException && e.name === 'AbortError')
        ? `manifest timed out after 20s (${this.serverUrl} unreachable?)`
        : `manifest failed: ${(e as Error).message}`;
      chrome.runtime.sendMessage({ target: 'content', type: 'AUDIO_ERROR',
        payload: { reason: msg } });
      return;
    }
    clearTimeout(abortTimer);
    this.queue = sentences;
    this.at = Math.min(startIdx, Math.max(0, sentences.length - 1));
    const offset = st && st.docId === docId && st.idx === this.at ? st.offsetMs : 0;
    chrome.runtime.sendMessage({
      target: 'content', type: 'MANIFEST_OK',
      payload: { docId, sentences: sentences.map((s) => s.text) },
    });
    this.subscribeSSE();
    await this.tap(this.at);
    this.playFrom(this.at, offset);
    } catch (e) {
      fail('unexpected', e);
    }
  }

  private pokeWatchdog(): void {
    this.backoff = 1000;
    if (this.watchdog) clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => this.reconnect(), WATCHDOG_MS);
  }

  private reconnect(): void {
    // 45s of SSE silence -> reconnect with backoff (never duplicate play).
    this.backoff = Math.min(30000, this.backoff * 2);
    this.subscribeSSE();
  }

  private subscribeSSE(): void {
    this.sse?.close();
    if (this.watchdog) clearTimeout(this.watchdog);
    const q = `${this.serverUrl}/events?doc_id=${encodeURIComponent(this.docId)}`;
    const sse = new EventSource(q);
    this.sse = sse;
    this.pokeWatchdog();
    sse.addEventListener('ready', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data) as { idx: number; hash: string; id?: number };
        if (typeof (ev as MessageEvent).lastEventId === 'string') {
          const n = parseInt((ev as MessageEvent).lastEventId, 10);
          if (!isNaN(n)) this.lastEventId = n;
        } else if (typeof d.id === 'number') this.lastEventId = d.id;
        this.pokeWatchdog();
        this.hashes.set(d.idx, d.hash);
        void this.fetchSentence(d.idx);
      } catch { /* malformed event: ignore */ }
    });
    sse.addEventListener('rendering', () => this.pokeWatchdog());
    sse.addEventListener('resync', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data) as { playhead?: number };
        this.pokeWatchdog();
        if (typeof d.playhead === 'number') {
          this.at = d.playhead;
          void this.tap(this.at);
          this.playFrom(this.at, 0);
        }
      } catch { /* ignore */ }
    });
    sse.addEventListener('failed', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data) as { idx: number; reason?: string };
        chrome.runtime.sendMessage({ target: 'content', type: 'AUDIO_ERROR', payload: { idx: d.idx, reason: d.reason ?? 'render failed' } });
      } catch { /* ignore */ }
    });
    sse.addEventListener('degraded', () => {
      chrome.runtime.sendMessage({ target: 'content', type: 'AUDIO_DEGRADED' });
    });
    sse.addEventListener('complete', () => {
      chrome.runtime.sendMessage({ target: 'content', type: 'CHAPTER_COMPLETE' });
    });
    sse.onerror = () => {
      if (this.watchdog) clearTimeout(this.watchdog);
      this.watchdog = setTimeout(() => this.subscribeSSE(), this.backoff);
    };
  }

  private async tap(idx: number): Promise<void> {
    try {
      await fetch(`${this.serverUrl}/tap`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc_id: this.docId, idx }),
      });
    } catch { /* offline: hold until reconnect */ }
  }

  private async fetchSentence(idx: number): Promise<void> {
    if (this.buffers.has(idx) || this.fetching.has(idx)) return;
    const hash = this.hashes.get(idx);
    if (!hash) return; // not rendered yet; SSE 'ready' will trigger us
    this.fetching.add(idx);
    try {
      const res = await fetch(`${this.serverUrl}/audio/${hash}`);
      if (!res.ok) {
        if (res.status === 202) {
          // Evicted before fetch: re-tap to requeue, up to 3 times, then loud.
          const n = (this.fetch202.get(idx) ?? 0) + 1;
          if (n >= 3) {
            this.fetch202.delete(idx);
            chrome.runtime.sendMessage({ target: 'content', type: 'AUDIO_ERROR', payload: { idx, status: 202 } });
          } else {
            this.fetch202.set(idx, n);
            void this.tap(idx);
          }
          return;
        }
        chrome.runtime.sendMessage({ target: 'content', type: 'AUDIO_ERROR', payload: { idx, status: res.status } });
        return;
      }
      const buf = await this.audio().decodeAudioData(await res.arrayBuffer());
      this.fetch202.delete(idx);
      this.buffers.set(idx, buf);
      if (idx === this.at && !this.playing) this.playFrom(idx, 0);
    } catch {
      /* network blip: SSE replay or next tap retries */
    } finally {
      this.fetching.delete(idx);
    }
  }

  private playFrom(idx: number, offsetMs: number): void {
    const buf = this.buffers.get(idx);
    if (!buf) {
      // Gap: hold briefly for SSE 'ready' -> fetch -> auto-start. Cap the
      // wait (~10s) then fail loudly instead of retrying forever.
      // A pause/seek/stop/boot bumps gen: never error on those.
      const g = this.gen;
      const n = (this.gapTries.get(idx) ?? 0) + 1;
      if (n * 500 >= 10000) {
        this.gapTries.delete(idx);
        if (g === this.gen) {
          chrome.runtime.sendMessage({ target: 'content', type: 'AUDIO_ERROR',
            payload: { idx, reason: `sentence ${idx + 1} not ready after 10s` } });
        }
        return;
      }
      this.gapTries.set(idx, n);
      setTimeout(() => {
        if (g !== this.gen) return; // superseded: stay silent
        if (this.at === idx && !this.playing) this.playFrom(idx, offsetMs);
      }, 500);
      return;
    }
    this.gapTries.delete(idx);
    const ctx = this.audio();
    try { this.source?.stop(); } catch { /* already stopped */ }
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.playbackRate.value = this.rate;
    src.connect(ctx.destination);
    const offsetS = Math.min(offsetMs / 1000, Math.max(0, buf.duration - 0.05));
    try {
      src.start(0, offsetS);
    } catch {
      return;
    }
    this.startedAt = ctx.currentTime - offsetS / this.rate;
    this.playing = true;
    this.source = src;
    src.onended = () => {
      // stop() also fires onended: only the live source may advance.
      if (this.source === src && this.at === idx) void this.next();
    };
    void this.tap(idx + 1); // keep render ahead of the playhead
    void this.lineAt(idx, offsetMs);
  }

  private lineAt(idx: number, offsetMs = 0): void {
    chrome.runtime.sendMessage({ target: 'content', type: 'LINE_AT', payload: { idx } });
    // Pause>30s kills the offscreen doc by design; every LINE_AT persists
    // the resume point so wake-up is exact (resume carries its offset).
    void savePlayState({ docId: this.docId, hash: this.hashes.get(idx) ?? '', idx, offsetMs, rate: this.rate });
  }

  private async next(): Promise<void> {
    this.at += 1;
    if (this.at >= this.queue.length) {
      this.playing = false;
      chrome.runtime.sendMessage({ target: 'content', type: 'CHAPTER_COMPLETE' });
      return;
    }
    this.lineAt(this.at);
    this.playFrom(this.at, 0);
  }

  pause(): void {
    this.gen += 1; // invalidate gap retries: pausing is not a failure
    this.gapTries.clear();
    const s = this.source;
    this.source = null; // stop() fires onended: must not advance
    this.playing = false;
    if (!s || !this.ctx) return;
    const now = this.ctx.currentTime;
    try { s.stop(); } catch { /* already stopped */ }
    const elapsedMs = Math.max(0, (now - this.startedAt) * 1000 * this.rate);
    this.pausedOffsetMs = elapsedMs;
    void savePlayState({ docId: this.docId, hash: this.hashes.get(this.at) ?? '', idx: this.at, offsetMs: elapsedMs, rate: this.rate });
  }

  resume(): void {
    if (this.playing) return;
    this.audio(); // resume() inside; required after gesture-less boot
    const off = this.pausedOffsetMs;
    this.pausedOffsetMs = 0;
    const buf = this.buffers.get(this.at);
    if (buf && off >= buf.duration * 1000) {
      this.playFrom(this.at, 0); // offset past end (re-rendered file): restart
    } else if (buf) {
      this.playFrom(this.at, resumeOffset(off, buf.duration * 1000));
    } else {
      this.playFrom(this.at, off);
    }
  }

  step(d: number): void {
    this.seek(this.at + d);
  }

  seek(idx: number): void {
    this.gen += 1;
    this.gapTries.clear();
    const s = this.source;
    this.source = null; // stop() fires onended: must not advance
    try { s?.stop(); } catch { /* already stopped */ }
    if (idx < 0 || idx >= this.queue.length) return;
    this.playing = false;
    this.pausedOffsetMs = 0;
    this.at = idx;
    void this.tap(idx);
    this.playFrom(idx, 0);
  }

  stop(): void {
    this.stopAll();
  }

  private stopAll(): void {
    this.gen += 1; // invalidate gap retries
    this.gapTries.clear();
    this.manifestSeq += 1; // invalidate in-flight manifest POSTs
    const s = this.source;
    this.source = null; // stop() fires onended: must not advance
    try { s?.stop(); } catch { /* already stopped */ }
    this.playing = false;
    this.pausedOffsetMs = 0;
    if (this.watchdog) clearTimeout(this.watchdog);
    this.watchdog = null;
    this.sse?.close();
    this.sse = null;
    this.buffers.clear();
    this.hashes.clear();
  }

  setRate(r: number): void {
    const nr = Math.min(2.5, Math.max(0.8, r));
    if (this.playing && this.ctx && this.source) {
      // Rebase the wall-clock anchor so pause/resume math stays exact.
      const posS = (this.ctx.currentTime - this.startedAt) * this.rate;
      this.rate = nr;
      this.startedAt = this.ctx.currentTime - posS / nr;
      this.source.playbackRate.value = nr;
    } else {
      this.rate = nr;
      if (this.source) this.source.playbackRate.value = nr;
    }
  }

  state(): { playing: boolean; idx: number } {
    return { playing: this.playing, idx: this.at };
  }
}

/** Pure helper: resume offset math (tested without AudioContext). */
export function resumeOffset(savedMs: number, durationMs: number): number {
  if (savedMs < 0) return 0;
  return Math.min(savedMs, Math.max(0, durationMs - 50));
}
