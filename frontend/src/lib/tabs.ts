// Two-tab leader election: exactly one tab writes position; the second
// tab is read-only for position (still plays audio locally).
// Snapshot on pagehide + visibilitychange (mobile beforeunload never fires).
const LOCK_KEY = "inkcast:pos-leader";
const HEARTBEAT_MS = 2000;
const STALE_MS = 5000;

export interface PositionSnapshot {
  bookId: string;
  chapter: number;
  sent_id: number;
  at: string; // UTC ISO
}

const SNAP_KEY = "inkcast:pos-snapshot";

function nowUtcIso(): string {
  return new Date().toISOString();
}

export function writeSnapshot(s: Omit<PositionSnapshot, "at">): void {
  const full: PositionSnapshot = { ...s, at: nowUtcIso() };
  try {
    localStorage.setItem(SNAP_KEY, JSON.stringify(full));
  } catch {
    /* quota-full: position stays in memory only */
  }
}

export function readSnapshot(): PositionSnapshot | null {
  try {
    const raw = localStorage.getItem(SNAP_KEY);
    return raw ? (JSON.parse(raw) as PositionSnapshot) : null;
  } catch {
    return null;
  }
}

// Claim leadership. Returns true if this tab is leader. Callers should
// re-check on "storage" events: if another tab holds a fresh lock, defer.
export function tryBecomeLeader(tabId: string): boolean {
  const now = Date.now();
  try {
    const raw = localStorage.getItem(LOCK_KEY);
    if (raw) {
      const { tab, ts } = JSON.parse(raw) as { tab: string; ts: number };
      if (tab !== tabId && now - ts < STALE_MS) return false;
    }
    localStorage.setItem(LOCK_KEY, JSON.stringify({ tab: tabId, ts: now }));
    return true;
  } catch {
    return true; // no storage (private mode): single-tab assumption
  }
}

export function heartbeatLeader(tabId: string): void {
  try {
    localStorage.setItem(
      LOCK_KEY,
      JSON.stringify({ tab: tabId, ts: Date.now() }),
    );
  } catch {
    /* ignore */
  }
}

export function resignLeader(tabId: string): void {
  try {
    const raw = localStorage.getItem(LOCK_KEY);
    if (raw && (JSON.parse(raw) as { tab: string }).tab === tabId) {
      localStorage.removeItem(LOCK_KEY);
    }
  } catch {
    /* ignore */
  }
}

// Wire pagehide + visibilitychange to snapshot + resign. Returns cleanup.
export function watchLifetime(
  tabId: string,
  getPos: () => Omit<PositionSnapshot, "at"> | null,
  onDemote?: () => void,
): () => void {
  const snap = () => {
    const p = getPos();
    if (p) writeSnapshot(p);
  };
  const onPageHide = () => {
    snap();
    resignLeader(tabId);
  };
  const onVis = () => {
    if (document.visibilityState === "hidden") snap();
  };
  const beat = window.setInterval(() => heartbeatLeader(tabId), HEARTBEAT_MS);
  const onStorage = (e: StorageEvent) => {
    if (e.key === LOCK_KEY && e.newValue) {
      try {
        const { tab } = JSON.parse(e.newValue) as { tab: string };
        if (tab !== tabId) onDemote?.();
      } catch {
        /* ignore */
      }
    }
  };
  window.addEventListener("pagehide", onPageHide);
  document.addEventListener("visibilitychange", onVis);
  window.addEventListener("storage", onStorage);
  return () => {
    window.clearInterval(beat);
    window.removeEventListener("pagehide", onPageHide);
    document.removeEventListener("visibilitychange", onVis);
    window.removeEventListener("storage", onStorage);
  };
}
