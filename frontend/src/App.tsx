import { useCallback, useEffect, useRef, useState } from "react";
import "./styles.css";
import { fetchChapterJson, sentenceIndexAt, type ChapterJson } from "./lib/manifest";
import { onEndedOnce, seekWhenReady } from "./lib/audio";
import { Player, type PlayerState } from "./lib/player";
import {
  readSnapshot,
  tryBecomeLeader,
  watchLifetime,
  type PositionSnapshot,
} from "./lib/tabs";
import AddNovel from "./views/AddNovel";

const API = "/api/v1";
const PREFS_KEY = "inkcast:reader-prefs";
const SPEEDS = [0.75, 1, 1.25, 1.5, 2];
const SLEEPS = [0, 5, 15, 30, 60]; // minutes, 0 = off

interface Book {
  id: string;
  title: string;
  author?: string;
  chapters?: number;
}

type View = "library" | "search" | "reader";

interface ReaderPrefs {
  fontPx: number;
  font: "serif" | "sans";
  lh: number;
  theme: "dark" | "sepia" | "light";
}

const DEFAULT_PREFS: ReaderPrefs = { fontPx: 18, font: "serif", lh: 1.9, theme: "dark" };

function loadPrefs(): ReaderPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (raw) return { ...DEFAULT_PREFS, ...JSON.parse(raw) };
  } catch {
    /* private mode: defaults */
  }
  return DEFAULT_PREFS;
}

function hueOf(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 360;
}

export default function App() {
  const [books, setBooks] = useState<Book[]>([]);
  const [booksLoading, setBooksLoading] = useState(true);
  const [booksError, setBooksError] = useState("");
  const [bookId, setBookId] = useState<string | null>(null);
  const [chapter, setChapter] = useState(1);
  const [manifest, setManifest] = useState<ChapterJson | null>(null);
  const [manifestLoading, setManifestLoading] = useState(false);
  const [activeSent, setActiveSent] = useState(-1);
  const [bar, setBar] = useState<PlayerState>({ kind: "idle" });
  const [leader, setLeader] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [sleepMin, setSleepMin] = useState(0);
  const [view, setView] = useState<View>("library");
  const [banner, setBanner] = useState<string | null>(null);
  const [quotaMsg, setQuotaMsg] = useState("");
  const [snap, setSnap] = useState<PositionSnapshot | null>(null);
  const [prefs, setPrefs] = useState<ReaderPrefs>(loadPrefs);
  const [follow, setFollow] = useState(true);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(0);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playerRef = useRef<Player | null>(null);
  const posRef = useRef({ bookId: "", chapter: 1, sent_id: -1 });
  const manifestRef = useRef<ChapterJson | null>(null);
  const followRef = useRef(true);
  const tabId = useRef(Math.random().toString(36).slice(2));

  // Single managed <audio>: attach Player once the tag mounts.
  const attachAudio = (el: HTMLAudioElement | null) => {
    if (!el || audioRef.current === el) return;
    audioRef.current = el;
    const player = new Player(el, {
      onState: setBar,
      onAdvance: () => setChapter((c) => c + 1), // single advance on ended
      prefetch: (url) => {
        const l = document.createElement("link");
        l.rel = "prefetch";
        l.href = url;
        document.head.appendChild(l);
      },
    });
    playerRef.current = player;
    onEndedOnce(el, () => setChapter((c) => c + 1));
    el.addEventListener("timeupdate", () => {
      player.onTimeUpdate();
      const m = manifestRef.current;
      if (m) {
        const i = sentenceIndexAt(m, el.currentTime);
        const id = i < 0 ? -1 : m.sentences[i].sent_id;
        setActiveSent((prev) => (prev === id ? prev : id));
        if (i >= 0 && followRef.current) {
          document.getElementById(`sent-${id}`)?.scrollIntoView({ block: "nearest" });
        }
      }
      setCur(el.currentTime);
      if (Number.isFinite(el.duration)) setDur(el.duration);
    });
  };

  const refreshBooks = useCallback(() => {
    setBooksLoading(true);
    setBooksError("");
    fetch(`${API}/books`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((b: Book[]) => {
        setBooks(b);
        setBooksLoading(false);
        setSnap(readSnapshot());
      })
      .catch(() => {
        setBooks([]);
        setBooksLoading(false);
        setBooksError("Library failed to load — check connection, then retry.");
      });
  }, []);

  useEffect(() => {
    refreshBooks();
    setLeader(tryBecomeLeader(tabId.current));
    setSnap(readSnapshot());
    const stop = watchLifetime(
      tabId.current,
      () => (posRef.current.bookId ? { ...posRef.current } : null),
      () => setLeader(false),
    );
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    posRef.current = { bookId: bookId ?? "", chapter, sent_id: activeSent };
  }, [bookId, chapter, activeSent]);

  useEffect(() => {
    manifestRef.current = manifest;
  }, [manifest]);

  useEffect(() => {
    followRef.current = follow;
  }, [follow]);

  useEffect(() => {
    if (!bookId) return;
    let live = true;
    setManifestLoading(true);
    playerRef.current?.setLoading(0);
    fetchChapterJson(`${API}/books/${bookId}/chapters/${chapter}/chapter.json`)
      .then((m) => {
        if (!live) return;
        setManifest(m);
        setManifestLoading(false);
        setActiveSent(-1);
        setFollow(true);
        const audio = audioRef.current;
        if (audio) {
          audio.src = `${API}/books/${bookId}/chapters/${chapter}/audio.opus`;
          audio.load();
        }
        playerRef.current?.maybePrefetch(
          `${API}/books/${bookId}/chapters/${chapter + 1}/audio.opus`,
        );
      })
      .catch((e: Error) => {
        if (!live) return;
        setManifestLoading(false);
        const msg = e.message ?? "chapter load failed";
        playerRef.current?.setError(msg);
        setBanner(`Chapter ${chapter} failed to load (${msg}).`);
      });
    return () => {
      live = false;
    };
  }, [bookId, chapter]);

  const persistPrefs = (p: ReaderPrefs) => {
    setPrefs(p);
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(p));
    } catch {
      playerRef.current?.quotaFullFallback();
      setQuotaMsg("storage full — streaming without cache");
    }
  };

  const openBook = (b: Book) => {
    const s = readSnapshot();
    setSnap(s);
    setBookId(b.id);
    setChapter(s && String(s.bookId) === String(b.id) ? s.chapter : 1);
    setBanner(null);
    setView("reader");
  };

  const closeReader = () => {
    setBookId(null);
    setManifest(null);
    setView("library");
    refreshBooks(); // re-read snapshot for continue badges
  };

  const applySleep = (min: number) => {
    setSleepMin(min);
    if (min <= 0) playerRef.current?.clearSleep();
    else playerRef.current?.sleepIn(min * 60_000);
  };

  const book = books.find((b) => String(b.id) === String(bookId));
  const isPlaying = bar.kind === "playing";
  const playerErr = bar.kind === "error" ? bar.message : null;
  const offline = bar.kind === "offline";

  return (
    <>
      <audio ref={attachAudio} preload="metadata" className="sr-only" aria-hidden />

      <main>
        <h1>InkCast</h1>

        {(banner || playerErr) && (
          <div role="alert" className="banner">
            <span>{banner ?? (playerErr ? `Playback error: ${playerErr}` : "")}</span>
            <button className="btn ghost" onClick={() => { setBanner(null); }}>Dismiss</button>
          </div>
        )}
        {offline && (
          <div role="alert" className="banner offline">
            <span>You’re offline — reading position is kept, audio will resume when reconnected.</span>
          </div>
        )}
        {quotaMsg && (
          <div role="status" className="banner quota"><span>{quotaMsg}</span></div>
        )}

        {view !== "reader" && (
          <>
            {view === "search" ? (
              <AddNovel onAdded={refreshBooks} />
            ) : (
              <section aria-label="Library">
                <div className="toolbar">
                  <button
                    className="btn ghost"
                    onClick={refreshBooks}
                    disabled={booksLoading}
                    aria-busy={booksLoading}
                  >
                    {booksLoading ? "Loading…" : "⟳ Refresh"}
                  </button>
                </div>
                {booksLoading ? (
                  <ul className="grid" aria-label="Loading books">
                    {Array.from({ length: 6 }).map((_, i) => (
                      <li key={i} className="skel" aria-hidden />
                    ))}
                  </ul>
                ) : booksError ? (
                  <div role="alert">
                    <p>{booksError}</p>
                    <button className="btn" onClick={refreshBooks}>Retry</button>
                  </div>
                ) : books.length === 0 ? (
                  <div className="empty">
                    <p>No books yet — add your first novel.</p>
                    <button className="btn" onClick={() => setView("search")}>
                      Search novels
                    </button>
                  </div>
                ) : (
                  <ul className="grid">
                    {books.map((b) => (
                      <li key={b.id}>
                        <button className="card" onClick={() => openBook(b)}>
                          <span
                            className="cover"
                            aria-hidden
                            style={{ background: `hsl(${hueOf(b.title)} 35% 32%)` }}
                          >
                            {(b.title || "?").slice(0, 1).toUpperCase()}
                          </span>
                          <span className="card-body">
                            <span className="card-title">{b.title}</span>
                            {b.author && <span className="card-meta">{b.author}</span>}
                            {typeof b.chapters === "number" && (
                              <span className="card-meta">{b.chapters} chapters</span>
                            )}
                            {snap && String(snap.bookId) === String(b.id) && (
                              <span className="continue-badge">
                                Continue · Ch. {snap.chapter}
                              </span>
                            )}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}
          </>
        )}

        {view === "reader" && (
          <section aria-label="Reader" className="reader" data-theme={prefs.theme}>
            <div className="sticky-top">
              <div className="toolbar">
                <button className="btn ghost" onClick={closeReader}>← Library</button>
                <strong>{book?.title ?? "Reader"}</strong>
                <span className="card-meta">Ch. {chapter}</span>
              </div>
              <div className="playerbar" role="group" aria-label="Narration">
                <button
                  className="btn"
                  onClick={() => (isPlaying ? playerRef.current?.pause() : playerRef.current?.play())}
                >
                  {isPlaying ? "❚❚ Pause" : "▶ Play"}
                </button>
                <label>
                  <span className="sr-only">Speed</span>
                  <select
                    aria-label="Speed"
                    value={speed}
                    onChange={(e) => {
                      const r = Number(e.target.value);
                      setSpeed(r);
                      playerRef.current?.setSpeed(r);
                    }}
                  >
                    {SPEEDS.map((r) => (
                      <option key={r} value={r}>{r}×</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span className="sr-only">Sleep timer</span>
                  <select
                    aria-label="Sleep timer"
                    value={sleepMin}
                    onChange={(e) => applySleep(Number(e.target.value))}
                  >
                    {SLEEPS.map((m) => (
                      <option key={m} value={m}>
                        {m === 0 ? "Sleep off" : `Sleep ${m}m`}
                      </option>
                    ))}
                  </select>
                </label>
                <span className="status" role="status">
                  {bar.kind === "loading" ? `loading ${bar.pct}%` : bar.kind}
                </span>
              </div>
              {!leader && <p role="note" className="card-meta">Read-only position (another tab is leader).</p>}
            </div>

            <div className="typebar" role="group" aria-label="Typography">
              <span className="grp">
                <button className="btn ghost" aria-label="Decrease font size"
                  onClick={() => persistPrefs({ ...prefs, fontPx: Math.max(14, prefs.fontPx - 1) })}>A−</button>
                <button className="btn ghost" aria-label="Increase font size"
                  onClick={() => persistPrefs({ ...prefs, fontPx: Math.min(24, prefs.fontPx + 1) })}>A+</button>
              </span>
              <span className="grp">
                <button
                  className="btn ghost"
                  aria-pressed={prefs.font === "serif"}
                  onClick={() => persistPrefs({ ...prefs, font: prefs.font === "serif" ? "sans" : "serif" })}
                >
                  {prefs.font === "serif" ? "Serif" : "Sans"}
                </button>
                <button className="btn ghost" aria-label="Cycle line height"
                  onClick={() => persistPrefs({ ...prefs, lh: prefs.lh >= 2.1 ? 1.6 : +(prefs.lh + 0.15).toFixed(2) })}>
                  ☰ {prefs.lh.toFixed(2)}
                </button>
              </span>
              <label>
                <span className="sr-only">Theme</span>
                <select
                  aria-label="Theme"
                  value={prefs.theme}
                  onChange={(e) => persistPrefs({ ...prefs, theme: e.target.value as ReaderPrefs["theme"] })}
                >
                  <option value="dark">Dark</option>
                  <option value="sepia">Sepia</option>
                  <option value="light">Light</option>
                </select>
              </label>
            </div>

            {manifestLoading && <p role="status"><span className="spinner" /> loading chapter…</p>}
            <article
              className="prose"
              style={{
                fontSize: prefs.fontPx,
                lineHeight: prefs.lh,
                fontFamily: prefs.font === "serif"
                  ? "Georgia, 'Noto Serif', 'Noto Serif CJK SC', serif"
                  : undefined,
              }}
              onWheel={() => follow && setFollow(false)}
              onTouchMove={() => follow && setFollow(false)}
            >
              {manifest?.sentences.map((s) => (
                <span
                  key={s.sent_id}
                  id={`sent-${s.sent_id}`}
                  className={s.sent_id === activeSent ? "sent active" : "sent"}
                  onClick={() => {
                    const audio = audioRef.current;
                    if (audio) seekWhenReady(audio, s.t_start);
                    setFollow(true);
                  }}
                >
                  {s.text}{" "}
                </span>
              ))}
            </article>
            {!follow && (
              <button className="btn follow-pill" onClick={() => setFollow(true)}>
                ↓ Follow narration
              </button>
            )}
            <nav className="chapnav" aria-label="Chapter">
              <button className="btn ghost" disabled={chapter <= 1} onClick={() => setChapter((c) => c - 1)}>
                Prev
              </button>
              <input
                type="range"
                aria-label="Audio position"
                min={0}
                max={1000}
                value={dur > 0 ? Math.round((cur / dur) * 1000) : 0}
                onChange={(e) => playerRef.current?.sliderSeek(Number(e.target.value) / 1000)}
              />
              <button className="btn ghost" onClick={() => setChapter((c) => c + 1)}>
                Next
              </button>
            </nav>
          </section>
        )}
      </main>

      <nav className="tabbar" aria-label="Primary">
        <button
          aria-current={view === "library" ? "page" : undefined}
          onClick={() => { setView("library"); setBookId(null); }}
        >
          Library
        </button>
        <button
          aria-current={view === "search" ? "page" : undefined}
          onClick={() => setView("search")}
        >
          Search
        </button>
        {bookId && (
          <button
            aria-current={view === "reader" ? "page" : undefined}
            onClick={() => setView("reader")}
          >
            Reader
          </button>
        )}
      </nav>
    </>
  );
}
