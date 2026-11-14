import { useEffect, useRef, useState } from "react";
import { fetchChapterJson, sentenceIndexAt, type ChapterJson } from "./lib/manifest";
import { onEndedOnce, seekWhenReady } from "./lib/audio";
import { Player, type PlayerState } from "./lib/player";
import { tryBecomeLeader, watchLifetime } from "./lib/tabs";

const API = "/api/v1";

interface Book {
  id: string;
  title: string;
  author?: string;
  chapters?: number;
}

export default function App() {
  const [books, setBooks] = useState<Book[]>([]);
  const [bookId, setBookId] = useState<string | null>(null);
  const [chapter, setChapter] = useState(1);
  const [manifest, setManifest] = useState<ChapterJson | null>(null);
  const [activeSent, setActiveSent] = useState(-1);
  const [bar, setBar] = useState<PlayerState>({ kind: "idle" });
  const [leader, setLeader] = useState(true);
  const [speed, setSpeed] = useState(1);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playerRef = useRef<Player | null>(null);
  const posRef = useRef({ bookId: "", chapter: 1, sent_id: -1 });
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
    el.addEventListener("timeupdate", () => player.onTimeUpdate());
  };

  useEffect(() => {
    fetch(`${API}/books`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((b: Book[]) => setBooks(b))
      .catch(() => setBooks([]));
    setLeader(tryBecomeLeader(tabId.current));
    const stop = watchLifetime(
      tabId.current,
      () => (posRef.current.bookId ? { ...posRef.current } : null),
      () => setLeader(false),
    );
    return () => stop();
  }, []);

  useEffect(() => {
    posRef.current = { bookId: bookId ?? "", chapter, sent_id: activeSent };
  }, [bookId, chapter, activeSent]);

  useEffect(() => {
    if (!bookId || !audioRef.current) return;
    let live = true;
    playerRef.current?.setLoading(0);
    fetchChapterJson(`${API}/books/${bookId}/chapters/${chapter}/chapter.json`)
      .then((m) => {
        if (!live) return;
        setManifest(m);
        setActiveSent(-1);
        const audio = audioRef.current!;
        audio.src = `${API}/books/${bookId}/chapters/${chapter}/audio.opus`;
        audio.load();
        playerRef.current?.maybePrefetch(
          `${API}/books/${bookId}/chapters/${chapter + 1}/audio.opus`,
        );
      })
      .catch((e: Error) =>
        playerRef.current?.setError(e.message ?? "chapter load failed"),
      );
    return () => {
      live = false;
    };
  }, [bookId, chapter]);

  // Highlight: timeupdate -> binary search -> <200ms class swap + autoscroll.
  const onTimeUpdate = () => {
    const audio = audioRef.current;
    if (!audio || !manifest) return;
    const i = sentenceIndexAt(manifest, audio.currentTime);
    const id = i < 0 ? -1 : manifest.sentences[i].sent_id;
    setActiveSent((prev) => (prev === id ? prev : id));
    if (i >= 0) {
      document
        .getElementById(`sent-${id}`)
        ?.scrollIntoView({ block: "nearest" });
    }
  };

  if (!bookId) {
    return (
      <main>
        <h1>InkCast</h1>
        <audio ref={attachAudio} controls style={{ display: "none" }} />
        <ul>
          {books.map((b) => (
            <li key={b.id}>
              <button onClick={() => { setBookId(b.id); setChapter(1); }}>
                {b.title}
                {b.author ? ` — ${b.author}` : ""}
              </button>
            </li>
          ))}
        </ul>
        {books.length === 0 && <p>No books yet.</p>}
      </main>
    );
  }

  return (
    <main>
      <button onClick={() => setBookId(null)}>← Library</button>
      <label>
        Speed
        <select
          value={speed}
          onChange={(e) => {
            const r = Number(e.target.value);
            setSpeed(r);
            playerRef.current?.setSpeed(r);
          }}
        >
          {[0.75, 1, 1.25, 1.5, 2].map((r) => (
            <option key={r} value={r}>{r}×</option>
          ))}
        </select>
      </label>
      {!leader && <p role="note">Read-only position (another tab is leader).</p>}
      <p role="status">
        {bar.kind === "loading" ? `loading ${bar.pct}%` : bar.kind}
        {bar.kind === "error" ? `: ${bar.message}` : ""}
      </p>
      <audio ref={attachAudio} controls onTimeUpdate={onTimeUpdate} />
      <article>
        {manifest?.sentences.map((s) => (
          <span
            key={s.sent_id}
            id={`sent-${s.sent_id}`}
            onClick={() => {
              const audio = audioRef.current;
              if (audio) seekWhenReady(audio, s.t_start);
            }}
            style={{
              background: s.sent_id === activeSent ? "yellow" : "none",
              cursor: "pointer",
            }}
          >
            {s.text}{" "}
          </span>
        ))}
      </article>
      <nav>
        <button disabled={chapter <= 1} onClick={() => setChapter((c) => c - 1)}>
          Prev
        </button>
        <span>Ch. {chapter}</span>
        <button onClick={() => setChapter((c) => c + 1)}>Next</button>
      </nav>
    </main>
  );
}
