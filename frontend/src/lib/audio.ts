import type { ChapterJson } from "./manifest";

// Single shared <audio>: Opus src, Range/206 server-side, preload=metadata.
export function createAudio(): HTMLAudioElement {
  const el = document.createElement("audio");
  el.preload = "metadata";
  return el;
}

export function setOpusSrc(el: HTMLAudioElement, url: string): void {
  el.src = url;
  el.load();
}

function hasSrc(el: HTMLAudioElement): boolean {
  return !!el.currentSrc || !!el.getAttribute("src");
}

// Cold-synth seek queue: never set currentTime on an empty src
// (throws InvalidStateError in some browsers). Queue until metadata arrives.
export function seekWhenReady(
  el: HTMLAudioElement,
  t: number,
  onDone?: () => void,
): () => void {
  if (hasSrc(el) && el.readyState > 0) {
    el.currentTime = t;
    onDone?.();
    return () => {};
  }
  const onMeta = () => {
    el.removeEventListener("loadedmetadata", onMeta);
    try {
      el.currentTime = t;
    } catch {
      /* retry on next metadata */
    }
    onDone?.();
  };
  el.addEventListener("loadedmetadata", onMeta);
  return () => el.removeEventListener("loadedmetadata", onMeta);
}

// Seek-to-100% means "advance to next chapter" exactly once — the audio
// `ended` event is the single trigger (Opus padding may fire ended slightly
// past manifest end, so compare but always advance once).
export function onEndedOnce(el: HTMLAudioElement, cb: () => void): () => void {
  let fired = false;
  const h = () => {
    if (fired) return;
    fired = true;
    cb();
  };
  el.addEventListener("ended", h);
  return () => el.removeEventListener("ended", h);
}

// Manifest end vs Opus padding: ended may arrive with currentTime beyond
// the last sentence t_end. Treat ended as chapter-done regardless.
export function manifestEnd(m: ChapterJson): number {
  const s = m.sentences;
  return s.length ? s[s.length - 1].t_end : 0;
}

export function isPastManifestEnd(m: ChapterJson, currentTime: number): boolean {
  return currentTime >= manifestEnd(m);
}

// <60s prefetch boundary: chapters shorter than 60s always prefetch N+1
// (boundary crossings are frequent); longer chapters prefetch near the end.
export const PREFETCH_BOUNDARY_S = 60;

export function shouldPrefetchNext(
  durationS: number,
  currentTime: number,
): boolean {
  if (durationS < PREFETCH_BOUNDARY_S) return true;
  return durationS - currentTime < PREFETCH_BOUNDARY_S;
}
