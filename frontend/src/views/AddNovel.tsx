import { useEffect, useRef, useState } from "react";
import {
  addNovel,
  fetchSearch,
  pollAddStatus,
  type SearchHit,
} from "../lib/search";

// Per-result add state: idle -> adding -> queued (Check status) -> ready | error.
type Op =
  | { st: "idle" }
  | { st: "adding" }
  | { st: "queued"; jobId: number }
  | { st: "ready"; novelId: number }
  | { st: "error"; msg: string };

// Raw lib errors carry the HTTP status in the message — translate to actions.
export function humanizeError(msg: string, errorClass = ""): string {
  if (/401|unauthorized/i.test(msg)) return "logged out, refresh";
  if (/504|timeout|abort/i.test(msg)) return "sources slow, retry";
  if (/502|blocked|unreachable/i.test(msg + errorClass))
    return "source blocked, try another";
  if (errorClass) return `source blocked, try another (${errorClass})`;
  return msg;
}

function hostOf(h: SearchHit): string {
  try {
    return new URL(h.url).host.replace(/^www\./, "");
  } catch {
    return h.source || "?";
  }
}

function hueOf(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 360;
}

export default function AddNovel({ onAdded }: { onAdded?: () => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");
  const [ops, setOps] = useState<Record<string, Op>>({});
  const timer = useRef<number | null>(null);

  // Debounced 400ms, min 3 chars; user-triggered poll only (no bg loops).
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (q.trim().length < 3) {
      setHits(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    timer.current = window.setTimeout(() => {
      fetchSearch(q.trim())
        .then((r) => {
          setHits(r.results);
          setError("");
          setSearching(false);
        })
        .catch((e: Error) => {
          setError(humanizeError(e.name === "AbortError" ? "search timed out" : e.message));
          setSearching(false);
        });
    }, 400);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [q]);

  const setOp = (url: string, op: Op) =>
    setOps((prev) => ({ ...prev, [url]: op }));

  const add = (url: string) => {
    setOp(url, { st: "adding" });
    addNovel(url)
      .then((r) => setOp(url, { st: "queued", jobId: r.job_id }))
      .catch((e: Error) => setOp(url, { st: "error", msg: humanizeError(e.message) }));
  };

  const poll = (url: string, jobId: number) => {
    pollAddStatus(jobId)
      .then((s) => {
        if (s.ready && s.novel_id !== null) {
          setOp(url, { st: "ready", novelId: s.novel_id });
          onAdded?.();
        } else if (s.error || /fail|error/i.test(s.status)) {
          setOp(url, { st: "error", msg: humanizeError(s.error || s.status, s.error_class) });
        } else {
          setOp(url, { st: "queued", jobId: s.job_id });
        }
      })
      .catch((e: Error) => setOp(url, { st: "error", msg: humanizeError(e.message) }));
  };

  const opFor = (url: string): Op => ops[url] ?? { st: "idle" };

  return (
    <section aria-label="Add novel">
      <div className="toolbar">
        <input
          type="search"
          aria-label="Search novels"
          placeholder="Search novels (min 3 chars)…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {searching && <span className="spinner" aria-label="searching" />}
      </div>
      {searching && <p role="status">searching…</p>}
      {error && <p role="alert" className="op-msg error">{error}</p>}
      {hits !== null && hits.length === 0 && !searching && (
        <p>No hits: check spelling or paste a chapter URL directly.</p>
      )}
      {(hits ?? []).map((h) => {
        const op = opFor(h.url);
        const host = hostOf(h);
        return (
          <article key={h.url} className="result-card">
            <div className="result-row">
              <span
                className="host-badge"
                style={{ background: `hsl(${hueOf(host)} 35% 38%)` }}
              >
                {host}
              </span>
              <span className={`pill ${h.status === "ongoing" ? "ongoing" : h.status === "completed" ? "completed" : ""}`}>
                {h.status}
              </span>
            </div>
            <strong>{h.title || "(untitled)"}</strong>
            <div className="result-row">
              {op.st === "idle" && (
                <button className="btn" onClick={() => add(h.url)}>+ Add</button>
              )}
              {op.st === "adding" && (
                <span role="status"><span className="spinner" /> adding…</span>
              )}
              {op.st === "queued" && (
                <>
                  <span role="status" className="op-msg">queued — check when ready.</span>
                  <button className="btn ghost" onClick={() => poll(h.url, op.jobId)}>
                    Check status
                  </button>
                </>
              )}
              {op.st === "ready" && (
                <span role="status" className="op-msg">ready — TOC loaded.</span>
              )}
              {op.st === "error" && (
                <>
                  <span role="alert" className="op-msg error">{op.msg}</span>
                  <button className="btn ghost" onClick={() => add(h.url)}>Retry</button>
                </>
              )}
            </div>
          </article>
        );
      })}
      <section aria-label="Paste URL fallback">
        <h2>Paste a URL</h2>
        <div className="toolbar">
          <input
            type="text"
            aria-label="Paste novel URL"
            placeholder="…or paste a novel/chapter URL"
            value={pasteUrl}
            onChange={(e) => setPasteUrl(e.target.value)}
          />
          <button
            className="btn"
            disabled={!pasteUrl.trim() || opFor(pasteUrl.trim()).st === "adding"}
            onClick={() => add(pasteUrl.trim())}
          >
            Resolve URL
          </button>
        </div>
        {pasteUrl.trim() && opFor(pasteUrl.trim()).st !== "idle" && (
          <p role="status" className="op-msg">
            {(() => {
              const op = opFor(pasteUrl.trim());
              if (op.st === "adding") return "adding…";
              if (op.st === "queued") return `queued (job ${op.jobId}) — Check status when ready.`;
              if (op.st === "ready") return `ready — novel ${op.novelId} TOC loaded.`;
              if (op.st === "error") return op.msg;
              return "";
            })()}{" "}
            {opFor(pasteUrl.trim()).st === "queued" && (
              <button
                className="btn ghost"
                onClick={() => {
                  const op = opFor(pasteUrl.trim());
                  if (op.st === "queued") poll(pasteUrl.trim(), op.jobId);
                }}
              >
                Check status
              </button>
            )}
          </p>
        )}
      </section>
    </section>
  );
}
