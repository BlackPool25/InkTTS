# Voice ABX: 6-voice shortlist + pick-by-ear

Goal: one default narrator the household can listen to for hours, plus a
working cast for dialogue. Ears decide, not specs.

## Shortlist (Kokoro-82M ONNX, fixed 6)

| # | Voice | Profile | Audition for |
|---|-------|---------|--------------|
| 1 | `af_heart` | EN female, warm (current default) | Narrator baseline |
| 2 | `af_bella` | EN female, bright | Narrator alt / heroine |
| 3 | `af_sarah` | EN female, steady | Narrator alt / villainess |
| 4 | `am_adam` | EN male, mid | Male lead / hero |
| 5 | `bf_emma` | EN-GB female | Second female / notes voice |
| 6 | `bm_george` | EN-GB male | Older male / authority |

Same sentence pool for every voice (build command in
`docs/ops/HARDENING.md` gate 5 -> `/tmp/abx/*.opus`). Keep speed 1.0 during
ABX; client-side speed is listener preference later.

## Pick-by-ear procedure

1. **Shuffle blind.** Rename files to `a.opus`..`f.opus` (keep the key
   elsewhere). No peeking at filenames while scoring.
2. **Same passage, same volume.** Play each clip start-to-finish once, then
   a second time back-to-back for the top two.
3. **Score 1-5 each:** clarity (consonants survive speed-ups?), sibilance
   fatigue (could you take 3h?), character fit (does the hero sound 17 or
   45?), consistency (no sentence sounds like a different person).
4. **Kill fast.** Drop the worst two after round one; A/B the survivors on
   dialogue (two voices alternating), not narration.
5. **Record.** Winner -> default voice (`PUT /api/v1/voices`, `API_TOKEN`
   required); runners-up -> per-novel cast (`voice-cast`); paste the sheet
   below.

## Run log

| date | listener | passage ch | winner | runner-up | notes |
|------|----------|------------|--------|-----------|-------|
| | | | | | |

## Rules

- Never ABX on stub-tone output (voiceless without the model — all six
  clips would be identical). `KOKORO_MODEL_PATH` must be set for the build.
- Re-run ABX per genre: a xianxia hero and a cozy-mystery narrator are
  different jobs; the shortlist stays the same, the winner may not.
