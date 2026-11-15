// Offscreen audio owner (AUDIO_PLAYBACK reason). Owns ALL of: SSE, prefetch
// scheduling, Web Audio gapless chaining, resume-by-{hash,idx,offsetMs}.
// Service workers die ~30s idle and cannot hold SSE or play audio —
// nothing playback-critical lives in the worker.
import { loadPlayState, loadSettings, savePlayState, type PlayState } from '../shared/messaging';

interface Sentence { idx: number; file: string; url: string }

const PREFETCH_K = 3;

export class Player {
  private ctx: AudioContext | null = null;
  private queue: Sentence[] = [];
  private at = 0;
  private source: AudioBufferSourceNode | null = null;
  private startedAt = 0;
  private pausedAt = 0;
  private playing = false;
  private rate = 1.0;
  private sse: EventSource | null = null;
  private ready = new Map<number, string>(); // idx -> object URL

  private audio(): AudioContext {
    if (!this.ctx) this.ctx = new AudioContext({ sampleRate: 24000 });
    return this.ctx;
  }

  async boot(docId: string, sentences: Sentence[], startIdx: number, serverUrl: string): Promise<void> {
    this.queue = sentences;
    this.at = startIdx;
    const st = await loadPlayState();
    const offset = st && st.docId === docId && st.idx === startIdx ? st.offsetMs : 0;
    this.subscribeSSE(serverUrl, docId);
    await this.ensure(startIdx, serverUrl);
    this.playFrom(startIdx, offset, serverUrl);
  }

  private subscribeSSE(serverUrl: string, docId: string): void {
    this.sse?.close();
    this.sse = new EventSource(`${serverUrl}/events?doc_id=${encodeURIComponent(docId)}`);
    this.sse.addEventListener('ready', (ev) => {
      const d = JSON.parse((ev as MessageEvent).data) as { idx: number; hash: string };
      void this.fetchSentence(d.idx, serverUrl);
    });
    this.sse.addEventListener('degraded', () => {
      chrome.runtime.sendMessage({ type: 'AUDIO_DEGRADED' });
    });
    this.sse.addEventListener('complete', () => {
      chrome.runtime.sendMessage({ type: 'CHAPTER_COMPLETE' });
    });
  }

  private objectUrl(idx: number): string | undefined {
    return this.ready.get(idx) ?? this.queue[idx]?.url;
  }

  async ensure(idx: number, serverUrl: string): Promise<void> {
    for (let i = idx; i < Math.min(idx + PREFETCH_K + 1, this.queue.length); i++) {
      if (!this.ready.has(i)) await this.fetchSentence(i, serverUrl);
    }
  }

  async fetchSentence(idx: number, serverUrl: string): Promise<void> {
    const s = this.queue[idx];
    if (!s || this.ready.has(idx)) return;
    const res = await fetch(`${serverUrl}/audio/${s.file}`);
    if (res.status === 202) return; // not rendered yet; SSE 'ready' will retry
    if (!res.ok) {
      chrome.runtime.sendMessage({ type: 'AUDIO_ERROR', payload: { idx, status: res.status } });
      return;
    }
    const buf = await res.arrayBuffer();
    this.ready.set(idx, URL.createObjectURL(new Blob([buf], { type: 'audio/ogg' })));
  }

  private async playFrom(idx: number, offsetMs: number, serverUrl: string): Promise<void> {
    const url = this.objectUrl(idx);
    if (!url) {
      // Gap: hold (SSE 'ready' triggers fetch -> retry), never stop.
      setTimeout(() => void this.playFrom(idx, offsetMs, serverUrl), 500);
      return;
    }
    const ctx = this.audio();
    const buf = await ctx.decodeAudioData(await (await fetch(url)).arrayBuffer());
    this.source?.stop();
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.playbackRate.value = this.rate;
    src.connect(ctx.destination);
    const offsetS = offsetMs / 1000;
    src.start(0, Math.min(offsetS, buf.duration - 0.05));
    this.startedAt = ctx.currentTime - offsetS / this.rate;
    this.playing = true;
    src.onended = () => {
      if (this.at === idx) this.next(serverUrl); // natural end -> advance
    };
    this.source = src;
    void this.ensure(idx + 1, serverUrl);
    void savePlayState({ docId: '', hash: this.queue[idx]?.file ?? '', idx, offsetMs: 0, rate: this.rate });
  }

  private next(serverUrl: string): void {
    this.at += 1;
    if (this.at >= this.queue.length) {
      this.playing = false;
      chrome.runtime.sendMessage({ type: 'CHAPTER_COMPLETE' });
      return;
    }
    chrome.runtime.sendMessage({ type: 'LINE_AT', payload: { idx: this.at } });
    void this.playFrom(this.at, 0, serverUrl);
  }

  pause(): void {
    if (!this.playing || !this.ctx) return;
    this.pausedAt = this.ctx.currentTime;
    this.source?.stop();
    this.playing = false;
    const elapsedMs = Math.max(0, (this.pausedAt - this.startedAt) * 1000 * this.rate);
    void savePlayState({ docId: '', hash: this.queue[this.at]?.file ?? '', idx: this.at, offsetMs: elapsedMs, rate: this.rate });
  }

  seek(idx: number, serverUrl: string): void {
    this.at = idx;
    this.source?.stop();
    this.playing = false;
    void this.playFrom(idx, 0, serverUrl);
  }

  setRate(r: number): void {
    this.rate = Math.min(2.5, Math.max(0.8, r));
    if (this.source) this.source.playbackRate.value = this.rate;
  }

  currentIdx(): number {
    return this.at;
  }
}

/** Pure helper: resume offset math (tested without AudioContext). */
export function resumeOffset(savedMs: number, durationMs: number): number {
  if (savedMs < 0) return 0;
  return Math.min(savedMs, Math.max(0, durationMs - 50));
}

void loadSettings;
void loadPlayState;
