import { describe, expect, it } from "vitest";
import {
  sentenceIdAt,
  sentenceIndexAt,
  type ChapterJson,
} from "../src/lib/manifest";

const fixture: ChapterJson = {
  version: 2,
  chapter: 1,
  render_key: "test",
  sentences: [
    { sent_id: 0, t_start: 0.0, t_end: 2.5, text: "First." },
    { sent_id: 1, t_start: 2.5, t_end: 5.0, text: "Second." },
    { sent_id: 2, t_start: 5.0, t_end: 9.25, text: "Third." },
  ],
};

describe("manifest binary search", () => {
  it("finds current sentence", () => {
    expect(sentenceIndexAt(fixture, 0)).toBe(0);
    expect(sentenceIndexAt(fixture, 2.49)).toBe(0);
    expect(sentenceIndexAt(fixture, 3.0)).toBe(1);
    expect(sentenceIndexAt(fixture, 9.0)).toBe(2);
  });

  it("upper-bound tie-break: boundary belongs to next sentence", () => {
    expect(sentenceIndexAt(fixture, 2.5)).toBe(1);
    expect(sentenceIndexAt(fixture, 5.0)).toBe(2);
    expect(sentenceIdAt(fixture, 2.5)).toBe(1);
  });

  it("clamps before-start and past-end", () => {
    expect(sentenceIndexAt(fixture, -0.5)).toBe(-1);
    expect(sentenceIndexAt(fixture, 999)).toBe(2);
  });
});
