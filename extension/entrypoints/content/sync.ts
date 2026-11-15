// LRC sync engine. Lives in the OFFSCREEN document (it owns the audio
// clock) — the overlay receives lineAt pushes, never per-frame round-trips.
// Karaoke rule: sustain, don't gap (between words, hold prior fill).
// Missing word timings (Chatterbox): line-sweep. Rate-scaled lookups.
export interface Word { w: string; start: number; end: number }
export interface Line { idx: number; start: number; words: Word[] | null; file: string }

export interface Sync {
  lineAt(t: number): number;
  wordFill(lineIdx: number, t: number): number; // 0..1 across the line
  synthetic: boolean;
}

export function parseLRC(lrc: string, files: string[]): Line[] {
  const lines: Line[] = [];
  const re = /^\[(\d+):(\d+\.\d+)\](.*)$/;
  lrc.split('\n').forEach((raw, idx) => {
    const m = re.exec(raw.trim());
    if (!m) return;
    const start = parseInt(m[1], 10) * 60 + parseFloat(m[2]);
    const body = m[3];
    const words: Word[] = [];
    const wre = /<(\d+\.\d+)>([^<]*)/g;
    let wm: RegExpExecArray | null;
    while ((wm = wre.exec(body)) !== null) {
      words.push({ w: wm[2], start: parseFloat(wm[1]), end: -1 });
    }
    for (let i = 0; i < words.length; i++) {
      words[i].end = i + 1 < words.length ? words[i + 1].start : start + 5;
      if (words[i].end - words[i].start < 0.08) words[i].end = words[i].start + 0.08;
    }
    lines.push({ idx, start, words: words.length > 0 ? words : null, file: files[idx] ?? '' });
  });
  return lines;
}

export function createSync(lines: Line[], rate = 1.0): Sync {
  const at = (t: number) => t / rate; // wall clock -> LRC media time
  // Bound trailing words: no word outlives the next line's start.
  lines.forEach((l, i) => {
    const cap = (lines[i + 1]?.start ?? l.start + 5);
    l.words?.forEach((w) => {
      if (w.end > cap) w.end = Math.max(w.start + 0.08, cap);
    });
  });
  const synthetic = lines.every((l) => l.words === null);
  function lineAt(t: number): number {
    const mt = at(t);
    let lo = 0;
    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].start <= mt) { lo = i; break; }
    }
    void lo;
    let best = 0;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].start <= mt) best = i;
      else break;
    }
    return best;
  }
  function wordFill(lineIdx: number, t: number): number {
    const mt = at(t);
    const line = lines[lineIdx];
    if (!line) return 0;
    if (!line.words) {
      // Line-sweep: progress toward next line start (2s grace at the end).
      const next = lines[lineIdx + 1]?.start ?? line.start + 2;
      const span = Math.max(0.001, next - line.start);
      return Math.min(1, Math.max(0, (mt - line.start) / span));
    }
    const spoken = line.words.filter((w) => w.start <= mt);
    if (spoken.length === 0) return 0;
    const cur = spoken[spoken.length - 1];
    const span = Math.max(0.001, cur.end - cur.start);
    const frac = Math.min(1, (mt - cur.start) / span);
    return Math.min(1, (spoken.length - 1 + frac) / line.words.length);
  }
  return { lineAt, wordFill, synthetic };
}
