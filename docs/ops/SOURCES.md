# InkCast source strategy (from FMHY reading survey, 2026-11-14)

FMHY's Light-Novel section validates our lncrawl bet: nearly every named
reading site (NovelBuddy, NovelFull, Ranobes, Baka-Tsuki, WuxiaWorld,
ReadNovelFull, Light Novel World, NovelFire, LNORI…) is lncrawl-supported.
No new fetcher needed — the work is source *selection*, not source plumbing.

## Ranked strategy
1. **lncrawl first (zero integration).** Prefer cooperative sources:
   Baka-Tsuki (fan translations, stable) > Ranobes > NovelFull >
   NovelBuddy (L12 bot-fight seen live) > NovelFire (L9 JS challenge).
   The scheduler's quarantine already routes around the fighters.
2. **Nyaa.si Light-Novel batches (completed series).** Bulk, no scraping
   fragility, no per-page challenges. User already runs qBittorrent.
   Flow: Nyaa batch → qBittorrent → EPUBs → OpenReader fork (reads EPUB
   natively). EPUB-import bridge into OUR pipeline is phase 2, not now.
3. **Legal originals:** Royal Road + ScribbleHub (author-posted, clean).
   lncrawl covers Royal Road; no bot-fight, no guilt.
4. **Manual fallback:** LN_Index / Book Smelting Bot (Telegram), WebToEpub
   (browser → EPUB → OpenReader). No integration — human-in-the-loop.
5. **Avoid:** trackers needing invites/ratio (MyAnonaMouse) for automation;
   AudiobookBay-style repacks (wrong format for a TTS pipeline that
   synthesizes its own audio).

## Notes
- Prefer licensed/legal sources where they exist (J-Novel Club, WuxiaWorld
  licensed titles); torrents are the gray column — user's call per title.
- Rule of thumb: ongoing series -> lncrawl poll; finished series -> Nyaa
  batch once, never scrape again.
