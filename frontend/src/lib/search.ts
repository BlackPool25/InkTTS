// Add-novel client: search hits + TOC-job enqueue + user-triggered polling.
// Token passes as Bearer (same posture as jobs); search aborts at 65s so the
// 60s server exec budget always wins the race server-side first.
const API = "/api/v1";
const SEARCH_TIMEOUT_MS = 65_000;

export interface SearchHit {
  title: string;
  url: string;
  source: string;
  status: "ongoing" | "completed" | "unknown";
}

export interface SearchResponse {
  query: string;
  results: SearchHit[];
  took_s: number;
}

function authHeaders(): Record<string, string> {
  const tok = localStorage.getItem("inkcast_token") ?? "";
  return tok ? { Authorization: `Bearer ${tok}` } : {};
}

export async function fetchSearch(q: string): Promise<SearchResponse> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), SEARCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${API}/search?q=${encodeURIComponent(q)}`, {
      headers: { Accept: "application/json", ...authHeaders() },
      signal: ctl.signal,
    });
    if (res.status === 401) throw new Error("unauthorized (bad token)");
    if (!res.ok) throw new Error(`search failed: ${res.status}`);
    return (await res.json()) as SearchResponse;
  } finally {
    clearTimeout(timer);
  }
}

export async function addNovel(
  url: string,
): Promise<{ novel_id: number; job_id: number; status: string }> {
  const res = await fetch(`${API}/books/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ url }),
  });
  if (res.status === 401) throw new Error("unauthorized (bad token)");
  if (!res.ok) throw new Error(`add failed: ${res.status}`);
  return await res.json();
}

export interface AddStatus {
  job_id: number;
  status: string;
  error_class: string;
  error: string;
  novel_id: number | null;
  ready: boolean;
}

export async function pollAddStatus(jobId: number): Promise<AddStatus> {
  const res = await fetch(`${API}/books/add/status/${jobId}`, {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (res.status === 401) throw new Error("unauthorized (bad token)");
  if (!res.ok) throw new Error(`status failed: ${res.status}`);
  return (await res.json()) as AddStatus;
}
