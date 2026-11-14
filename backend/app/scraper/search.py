"""Novel search via the lncrawl sidecar (docker exec, never PyPI).

search_novels() shells out with the same venv prefix as client.run_crawl
(PID 1 in the image is `sleep infinity`; system python lacks deps) and
parses the human-readable `lncrawl search` stdout into normalized hits:

    [{title, url, source(host), status(ongoing/completed/unknown), raw}]

Observed 2026-11-14 (live probe, lncrawl 4.x), groups of::

    📖 <Title>  (N results)
      ➡ <url>
        <status: ongoing | completed | |>

Parsing is defensive (regex, never raises on unknown lines): title lines
open a group, arrow lines open a pending hit, the first non-empty line
after a hit that is not a new group/arrow is its status. Errors reuse
client.classify_error; exec timeout SIGKILLs the child and raises
CrawlTimeout (transient), mirroring run_crawl.
"""

from __future__ import annotations

import re
import signal
import subprocess
import time
from urllib.parse import urlparse

from .client import (LNCRAWL_CONTAINER, CrawlError, CrawlTimeout,
                     classify_error)

_TITLE_RE = re.compile(r"^\s*📖?\s*(.+?)\s*\(\d+\s*results?\)\s*$")
_ARROW_RE = re.compile(r"^\s*(?:➡|->|=>)\s*(\S+)\s*$")


def _status_of(line: str) -> str:
    low = line.strip().lower()
    if "ongoing" in low:
        return "ongoing"
    if "complet" in low:  # completed / complete
        return "completed"
    return "unknown"


def parse_search_output(text: str) -> list[dict]:
    """Parse raw `lncrawl search` output into normalized hits. Never raises."""
    hits: list[dict] = []
    title = ""
    pending: dict | None = None
    try:
        lines = (text or "").splitlines()
    except Exception:  # noqa: BLE001 -- defensive: garbage in, [] out
        return []
    for line in lines:
        try:
            m = _TITLE_RE.match(line)
            if m:
                title = m.group(1).strip()
                pending = None
                continue
            m = _ARROW_RE.match(line)
            if m:
                url = m.group(1).strip()
                try:
                    source = urlparse(url).hostname or ""
                except Exception:  # noqa: BLE001
                    source = ""
                pending = {"title": title, "url": url, "source": source,
                           "status": "unknown", "raw": line.strip()}
                hits.append(pending)
                continue
            if pending is not None and line.strip():
                pending["status"] = _status_of(line)
                pending = None  # status consumed; further lines are noise
        except Exception:  # noqa: BLE001 -- one bad line never kills parse
            continue
    return hits


def search_novels(query: str, timeout_s: float = 60,
                  exec_prefix: list[str] | None = None) -> list[dict]:
    """Run `lncrawl search <query>` in the sidecar; return normalized hits.

    Raises CrawlTimeout (transient) on exec timeout, CrawlError otherwise.
    """
    q = (query or "").strip()
    if not q:
        return []
    prefix = list(exec_prefix) if exec_prefix is not None else [
        "docker", "exec", LNCRAWL_CONTAINER,
        "/app/.venv/bin/python", "-m", "lncrawl"]
    proc = subprocess.Popen(prefix + ["search", q],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()  # SIGKILL the docker-exec child (mirror run_crawl)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGKILL)
            stdout, stderr = proc.communicate()
        raise CrawlTimeout(
            f"search timed out after {timeout_s}s (SIGKILLed)") from None
    if rc != 0:
        tail = ((stdout or "") + (stderr or ""))[-2000:]
        raise CrawlError(f"lncrawl search exit {rc}: {tail[-500:]}",
                         classify_error(rc, tail))
    try:
        return parse_search_output(stdout or "")
    except Exception as e:  # noqa: BLE001 -- parser is total, belt+suspenders
        raise CrawlError(f"search parse failed: {e}", "transient") from e


def timed_search(query: str, timeout_s: float = 60) -> tuple[list[dict], float]:
    """search_novels + wall-clock seconds for the took_s field."""
    start = time.monotonic()
    hits = search_novels(query, timeout_s)
    return hits, time.monotonic() - start
