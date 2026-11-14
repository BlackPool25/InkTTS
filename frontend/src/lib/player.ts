import { seekWhenReady, shouldPrefetchNext } from "./audio";
import type { ChapterJson } from "./manifest";

// 6-state narration bar: idle / loading % / ready / playing / error / offline.
export type PlayerState =
  | { kind: "idle" }
  | { kind: "loading"; pct: number }
  | { kind: "ready" }
  | { kind: "playing"; sent_id: number }
  | { kind: "error"; message: string }
  | { kind: "offline" };

export interface PlayerEvents {
  onState(s: PlayerState): void;
  onAdvance(): void; // seek-to-100% / ended: exactly one chapter advance
  prefetch(url: string): void;
}

export class Player {
  state: PlayerState = { kind: "idle" };
  private sleepTimer: ReturnType<typeof setTimeout> | null = null;
  private prefetchPending: string | null = null;
  private streamOnly = false;

  constructor(
    private el: HTMLAudioElement,
    private ev: PlayerEvents,
  ) {
    el.addEventListener("waiting", () => this.set({ kind: "loading", pct: -1 }));
    el.addEventListener("canplay", () => {
      if (this.state.kind === "loading") this.set({ kind: "ready" });
    });
    el.addEventListener("playing", () =>
      this.set({ kind: "playing", sent_id: -1 }),
    );
    el.addEventListener("error", () =>
      this.set({
        kind: "error",
        message: this.el.error?.message ?? "audio error",
      }),
    );
    // Opus padding vs manifest end: `ended` is the single advance trigger.
    el.addEventListener("ended", () => this.ev.onAdvance());
    window.addEventListener("offline", () => {
      if (this.el.networkState === this.el.NETWORK_NO_SOURCE) return;
      this.set({ kind: "offline" });
    });
    window.addEventListener("online", () => {
      if (this.state.kind === "offline") this.set({ kind: "ready" });
    });
  }

  private set(s: PlayerState): void {
    this.state = s;
    this.ev.onState(s);
  }

  setLoading(pct: number): void {
    this.set({ kind: "loading", pct });
  }

  setOfflineMidChapter(): void {
    // Offline mid-chapter: visible error, never a silent stall.
    this.set({ kind: "offline" });
  }

  setError(message: string): void {
    this.set({ kind: "error", message });
  }

  // Tap a sentence span -> seek to its t_start (queues if src not ready).
  tapSeek(t: number): void {
    seekWhenReady(this.el, t);
  }

  // Slider 0..1 -> seek to fraction of duration.
  sliderSeek(frac: number): void {
    if (!Number.isFinite(this.el.duration)) return;
    seekWhenReady(this.el, frac * this.el.duration);
  }

  // Client-side speed only; manifest rate recorded server-side (render_key).
  setSpeed(rate: number): void {
    this.el.playbackRate = rate;
  }

  // Prefetch N+1 when near the end (<60s boundary rule in shouldPrefetchNext).
  maybePrefetch(nextUrl: string): void {
    if (!Number.isFinite(this.el.duration)) {
      this.prefetchPending = nextUrl;
      return;
    }
    if (shouldPrefetchNext(this.el.duration, this.el.currentTime)) {
      this.ev.prefetch(nextUrl);
    } else {
      this.prefetchPending = nextUrl;
    }
  }

  onTimeUpdate(): void {
    if (
      this.prefetchPending &&
      Number.isFinite(this.el.duration) &&
      shouldPrefetchNext(this.el.duration, this.el.currentTime)
    ) {
      this.ev.prefetch(this.prefetchPending);
      this.prefetchPending = null;
    }
  }

  // Sleep timer fires -> stop + cancel pending prefetch (no wasted bytes).
  sleepIn(ms: number): void {
    this.clearSleep();
    this.sleepTimer = setTimeout(() => {
      this.el.pause();
      this.prefetchPending = null;
      this.set({ kind: "ready" });
    }, ms);
  }

  clearSleep(): void {
    if (this.sleepTimer) clearTimeout(this.sleepTimer);
    this.sleepTimer = null;
  }

  // Quota-full (persist/cache write throws): stream-only fallback + message.
  quotaFullFallback(message = "storage full — streaming without cache"): void {
    this.streamOnly = true;
    this.prefetchPending = null;
  }

  get isStreamOnly(): boolean {
    return this.streamOnly;
  }

  async play(): Promise<void> {
    try {
      await this.el.play();
    } catch (e) {
      this.set({
        kind: "error",
        message: e instanceof Error ? e.message : "play failed",
      });
    }
  }

  pause(): void {
    this.el.pause();
    this.set({ kind: "ready" });
  }
}
