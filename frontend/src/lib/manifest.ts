// Chapter sidecar v2 shape: {idx}.{hash8}.json — sent_id positional,
// timings measured from shipped Opus bytes, next.t_start == prev.t_end.
export interface Sentence {
  sent_id: number;
  t_start: number;
  t_end: number;
  text: string;
}

export interface ChapterJson {
  version: 2;
  chapter: number;
  render_key: string;
  sentences: Sentence[];
}

export async function fetchChapterJson(url: string): Promise<ChapterJson> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`chapter.json fetch failed: ${res.status}`);
  const body = (await res.json()) as ChapterJson;
  if (body.version !== 2 || !Array.isArray(body.sentences)) {
    throw new Error("unsupported sidecar version (want v2)");
  }
  return body;
}

// Binary search: index of sentence containing t.
// Tie-break (upper-bound): exact boundary t == s[i+1].t_start belongs to i+1.
// Returns -1 when t < first.t_start; clamps past-end to last index.
export function sentenceIndexAt(m: ChapterJson, t: number): number {
  const s = m.sentences;
  if (s.length === 0) return -1;
  if (t < s[0].t_start) return -1;
  let lo = 0;
  let hi = s.length - 1;
  let ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (s[mid].t_start <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

export function sentenceIdAt(m: ChapterJson, t: number): number {
  const i = sentenceIndexAt(m, t);
  return i < 0 ? -1 : m.sentences[i].sent_id;
}

// Dev-time invariant check: next.t_start == prev.t_end (exact, shipped bytes).
export function assertGapless(m: ChapterJson): void {
  for (let i = 1; i < m.sentences.length; i++) {
    if (m.sentences[i].t_start !== m.sentences[i - 1].t_end) {
      throw new Error(`gap/overlap at sent_id ${m.sentences[i].sent_id}`);
    }
  }
}
