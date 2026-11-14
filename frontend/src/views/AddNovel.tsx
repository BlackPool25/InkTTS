import { useEffect, useRef, useState } from "react";
import {
  addNovel,
  fetchSearch,
  pollAddStatus,
  type SearchHit,
} from "../lib/search";

type Phase =
  | { kind: "idle" }
  | { kind: "searching" }
  | { kind: "adding"; url: string }
  | { kind: "queued"; jobId: number; novelId: number };

export default function AddNovel({ onAdded }: { onAdded?: () => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [statusLine, setStatusLine] = useState("");
  const timer = useRef<number | null>(null);

  // Debounced 400ms, min 3 chars; user-triggered poll only (no bg loops).
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (q.trim().length < 3) {
      setHits(null);
      return;
    }
    setPhase({ kind: "searching" });
    timer.current = window.setTimeout(() => {
      fetchSearch(q.trim())
        .then((r) => {
          setHits(r.results);
          setError("");
          setPhase({ kind: "idle" });
        })
        .catch((e: Error) => {
          setError(e.name === "AbortError" ? "search timed out" : e.message);
          setPhase({ kind: "idle" });
        });
    }, 400);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [q]);

  const add = (url: string) => {
    setPhase({ kind: "adding", url });
    setStatusLine("");
    addNovel(url)
      .then((r) => {
        setPhase({ kind: "queued", jobId: r.job_id, novelId: r.novel_id });
        setStatusLine(`queued (job ${r.job_id}) — Check status when ready.`);
      })
      .catch((e: Error) => {
        setStatusLine(`add failed: ${e.message}`);
        setPhase({ kind: "idle" });
      });
  };

  const poll = (jobId: number) => {
    pollAddStatus(jobId)
      .then((s) => {
        if (s.ready) {
          setStatusLine(`ready — novel ${s.novel_id} TOC loaded.`);
          onAdded?.();
        } else {
          setStatusLine(
            `job ${s.job_id}: ${s.status}${s.error ? ` (${s.error})` : ""}`,
          );
        }
      })
      .catch((e: Error) => setStatusLine(`status failed: ${e.message}`));
  };

  return (
    <section aria-label="Add novel">
      <h2>Add novel</h2>
      <input
        aria-label="Search novels"
        placeholder="Search novels (min 3 chars)…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {phase.kind === "searching" && <p role="status">searching…</p>}
      {error && <p role="alert">{error}</p>}
      {hits !== null && hits.length === 0 && (
        <p>No hits: check spelling or paste a chapter URL directly.</p>
      )}
      <ul>
        {(hits ?? []).map((h) => (
          <li key={h.url}>
            <span
              aria-hidden
              style={{
                display: "inline-block",
                width: "1.5em",
                textAlign: "center",
                background: "#eee",
              }}
            >
              {(h.source || "?").slice(0, 1).toUpperCase()}
            </span>{" "}
            <strong>{h.title || "(untitled)"}</strong>{" "}
            <span>{h.source}</span>{" "}
            <span
              style={{
                color: h.status === "ongoing" ? "green" : "gray",
              }}
            >
              {h.status}
            </span>{" "}
            <button
              disabled={phase.kind === "adding"}
              onClick={() => add(h.url)}
            >
              + Add
            </button>
          </li>
        ))}
      </ul>
      <div>
        <input
          aria-label="Paste novel URL"
          placeholder="…or paste a novel/chapter URL"
          value={pasteUrl}
          onChange={(e) => setPasteUrl(e.target.value)}
        />
        <button
          disabled={!pasteUrl.trim() || phase.kind === "adding"}
          onClick={() => add(pasteUrl.trim())}
        >
          Resolve URL
        </button>
      </div>
      {phase.kind === "adding" && <p role="status">adding…</p>}
      {phase.kind === "queued" && (
        <p role="status">
          {statusLine}{" "}
          <button onClick={() => poll(phase.jobId)}>Check status</button>{" "}
          <a href={`#/books/${phase.novelId}`}>Open in library</a>
        </p>
      )}
      {phase.kind !== "queued" && statusLine && (
        <p role="status">{statusLine}</p>
      )}
    </section>
  );
}
