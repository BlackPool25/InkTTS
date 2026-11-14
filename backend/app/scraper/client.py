"""docker-exec client for the lncrawl sidecar. See package docstring for paths.

Rules (from Task 3 brief):
- Per-job artifact dirs: every crawl snapshots new sidecar files into its own
  backend dir (no zip-name races between jobs).
- Parse artifact FILES only (zip/epub/json/txt) — never scrape stdout, which
  is rich-console noise in v4, not content.
- Exec goes through the docker SDK (exec_docker.exec_sidecar): the backend
  image ships no `docker` CLI. Timeout raises CrawlTimeout (transient); the
  jobs-table lease (MISFIRE_S) re-queues via sweep_stale.
- Container name from LNCRAWL_CONTAINER env (compose sets inkcast-lncrawl).
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..gate import CHALLENGE_SIGS
from .exec_docker import exec_sidecar, local_exec

LNCRAWL_CONTAINER = os.environ.get("LNCRAWL_CONTAINER", "inkcast-lncrawl")
# Sidecar-side data root (ENV LNCRAWL_DATA_PATH in image; see pkg docstring).
LNCRAWL_DATA_PATH = os.environ.get("LNCRAWL_DATA_PATH", "/data")
# Where the backend sees that same dir (compose mounts lncrawl-data here, :ro).
SIDECAR_MOUNT = os.environ.get("LNCRAWL_MOUNT", "/data/lncrawl")
# Backend-writable per-job artifact dirs (DATA_DIR on the backend container).
BACKEND_JOB_DIR = os.path.join(os.environ.get("DATA_DIR", "/data"), "lncrawl-jobs")

TEXTISH_EXTS = (".json", ".txt", ".xhtml", ".html", ".htm", ".opf", ".ncx")
ARTIFACT_EXTS = (".epub", ".zip", ".json", ".txt")

TRANSIENT_MARKS = ("429", "too many requests", "timeout", "timed out",
                   "connection reset", "temporarily", "service unavailable",
                   "bad gateway", "gateway timeout", "rate limit")
# Challenge-solver absent from the sidecar image: quarantine the SOURCE
# (blocked), never crash the tick — loud via the jobs row (Task 8 gate).
SOLVER_MISSING_MARKS = ("solver missing", "no solver", "solver not ",
                        "solver unavailable", "solver failed to load")
EMPTY_MARKS = ("no chapters", "0 chapters", "nothing to download", "not downloaded yet")
# Live-observed 2026-11-14: L19 identity-provider gates (login-walled novels).
# Terminal per novel: no retry burns, UI flags "source requires login".
IMPASSABLE_MARKS = ("identity-provider gate", "l19",
                    "authentication required", "login required",
                    "account required", "behind an identity provider")


class CrawlError(Exception):
    """Base; carries the jobs-table error_class for the failure row."""

    def __init__(self, msg: str, error_class: str = "transient"):
        super().__init__(msg)
        self.error_class = error_class


class CrawlTimeout(CrawlError):
    """Exec exceeded its budget; child was SIGKILLed. Always transient."""

    def __init__(self, msg: str):
        super().__init__(msg, "transient")


@dataclass
class CrawlResult:
    job_id: int
    dir: str  # per-job artifact dir (backend side)
    files: list[str] = field(default_factory=list)  # copied artifact paths
    returncode: int = 0
    stderr_tail: str = ""


def build_crawl_argv(url: str, first: int | None = None,
                     last: int | None = None, all_: bool = False,
                     resume: bool = False, refresh: bool = False,
                     formats: tuple[str, ...] = ("json",),
                     rate_limit: float | None = None) -> list[str]:
    """Non-interactive v4 argv: ``crawl URL --noin ...`` (exec_docker runs
    ``/app/.venv/bin/python -m lncrawl`` in the sidecar: the image's system
    python lacks deps, and PID 1 is `sleep infinity`). Scheduler ticks use
    --resume (= conditional fetch: only chapters not already downloaded
    transfer)."""
    argv = ["crawl", url, "--noin"]
    if all_:
        argv.append("--all")
    if first:
        argv += ["--first", str(first)]
    if last:
        argv += ["--last", str(last)]
    if resume:
        argv.append("--resume")
    if refresh:
        argv.append("--refresh")
    if rate_limit:
        argv += ["--rate-limit", str(rate_limit)]
    for fmt in formats:
        argv += ["-f", fmt]
    return argv


def classify_error(returncode: int, output: str, timed_out: bool = False) -> str:
    """Map exec outcome -> jobs error_class. Never raises."""
    if timed_out:
        return "transient"
    low = (output or "").lower()
    if any(m in low for m in IMPASSABLE_MARKS):
        return "impassable"
    if any(sig in low for sig in CHALLENGE_SIGS) or "captcha" in low or "403" in low:
        return "blocked"
    if any(m in low for m in SOLVER_MISSING_MARKS):
        return "blocked"
    if any(m in low for m in TRANSIENT_MARKS):
        return "transient"
    if any(m in low for m in EMPTY_MARKS):
        return "empty"
    if returncode != 0:
        return "transient"  # unknown failure: retryable, LOUD via jobs row
    return ""


def job_dir(job_id: int, base: str = BACKEND_JOB_DIR) -> str:
    path = os.path.join(base, f"crawl-{job_id}-{uuid.uuid4().hex[:8]}")
    os.makedirs(path, exist_ok=True)
    return path


def snapshot_dir(root: str) -> dict[str, tuple[int, float]]:
    """{relpath: (size, mtime)}; missing dir -> {} (sidecar down = empty)."""
    out: dict[str, tuple[int, float]] = {}
    if not os.path.isdir(root):
        return out
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
                out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime)
            except OSError:
                continue
    return out


def new_files(before: dict, after: dict) -> list[str]:
    return sorted(rel for rel in after if after[rel] != before.get(rel))


def collect_artifacts(sidecar_root: str, relpaths: list[str],
                      dest_dir: str) -> list[str]:
    """Copy new sidecar files into the per-job dir. Returns copied paths."""
    os.makedirs(dest_dir, exist_ok=True)
    copied = []
    for rel in relpaths:
        if not rel.lower().endswith(ARTIFACT_EXTS):
            continue
        src = os.path.join(sidecar_root, rel)
        dst = os.path.join(dest_dir, os.path.basename(rel))
        try:
            shutil.copyfile(src, dst)
            copied.append(dst)
        except OSError:
            continue
    return copied


def iter_zip_chapters(zip_path: str):
    """Yield (member_name, text) for textish members of a zip/epub artifact.

    Content comes from artifact FILES only — stdout is never content.
    HTML members are tag-stripped (best-effort); caller runs gate_text next.
    """
    import re as _re

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(TEXTISH_EXTS):
                continue
            try:
                raw = zf.read(info.filename).decode("utf-8", errors="replace")
            except (KeyError, RuntimeError, zipfile.BadZipFile):
                continue
            if info.filename.lower().endswith((".xhtml", ".html", ".htm")):
                raw = _re.sub(r"<[^>]+>", " ", raw)
                raw = _re.sub(r"\s+", " ", raw)
            yield info.filename, raw


def parse_retry_after(value: str, now: float | None = None) -> float:
    """Retry-After (delay-seconds or HTTP-date) -> seconds from now.

    Unparseable -> 0 (caller falls back to exponential backoff).
    """
    now = time.time() if now is None else now
    value = (value or "").strip()
    if value.isdigit():
        return max(0, int(value))
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, dt.timestamp() - now)
    except (ValueError, TypeError):
        return 0


def run_crawl(argv: list[str], job_id: int, timeout_s: float,
              sidecar_mount: str = SIDECAR_MOUNT,
              dest_base: str = BACKEND_JOB_DIR,
              exec_prefix: list[str] | None = None) -> CrawlResult:
    """Exec the sidecar (docker SDK; ``exec_prefix`` overrides with a local
    subprocess for hermetic tests), snapshot new artifacts into a per-job dir.

    timeout_s should be <= the job's remaining lease: on expiry CrawlTimeout
    is raised (transient), so no orphan crawl can later commit rows for a
    lease that already moved on.
    """
    before = snapshot_dir(sidecar_mount)
    dest = job_dir(job_id, dest_base)
    if exec_prefix is not None:
        rc, _, stderr = local_exec(list(exec_prefix) + list(argv), timeout_s)
    else:
        rc, _, stderr = exec_sidecar(argv, timeout_s)
    tail = (stderr or "")[-2000:]
    if rc != 0:
        raise CrawlError(f"lncrawl exit {rc}: {tail[-500:]}",
                         classify_error(rc, tail))
    after = snapshot_dir(sidecar_mount)
    files = collect_artifacts(sidecar_mount, new_files(before, after), dest)
    return CrawlResult(job_id=job_id, dir=dest, files=files,
                       returncode=rc, stderr_tail=tail)


def sweep_stale_tmp(base: str = BACKEND_JOB_DIR,
                    older_than_s: float | None = None) -> int:
    """Remove per-job tmp dirs. Boot sweep: older_than_s=None removes all
    (no crawl survives a reboot); running crawls hold dirs younger than the
    age gate. Returns count removed."""
    now = time.time()
    removed = 0
    if not os.path.isdir(base):
        return 0
    for name in os.listdir(base):
        if not name.startswith("crawl-"):
            continue
        path = os.path.join(base, name)
        try:
            if older_than_s is not None and now - os.stat(path).st_mtime < older_than_s:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed


def stamp(directory: str = BACKEND_JOB_DIR) -> str:
    """UTC timestamp marker for logs/evidence."""
    ts = datetime.now(timezone.utc).isoformat()
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, ".last-sweep"), "w") as f:
        f.write(ts)
    return ts
