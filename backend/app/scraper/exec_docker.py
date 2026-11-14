"""Sidecar exec via the docker SDK (never a `docker` CLI binary).

The backend image ships no `docker` CLI, so shelling out to one 500s every
live search/crawl. The socket IS mounted, and ``docker>=7.0`` is already a
dependency — exec through it instead.

Timeout/orphan note: a server-side exec ID cannot be SIGKILLed remotely, so
on timeout we abandon the thread and raise CrawlTimeout. The stray crawl may
outlive the call, but it is harmless: artifacts are snapshotted into per-job
dirs (an orphan's files are never collected by another job under the
single-flight ``MAX_INSTANCES_PER_KIND=1`` lease), and validate-then-commit
plus the jobs-lease sweep drop any late commit from a stale lease.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading

LNCRAWL_CONTAINER = os.environ.get("LNCRAWL_CONTAINER", "inkcast-lncrawl")
# 4.14.0 image: system python lacks deps (no typer); the venv python owns
# them. `sleep infinity` is PID 1, so exec must name the interpreter.
LNCCMD = ("/app/.venv/bin/python", "-m", "lncrawl")


def _decode(part) -> str:
    if not part:
        return ""
    if isinstance(part, bytes):
        return part.decode("utf-8", errors="replace")
    return str(part)


def exec_sidecar(argv: list[str], timeout_s: float,
                 container: str = LNCRAWL_CONTAINER
                 ) -> tuple[int, str, str]:
    """Run lncrawl ``argv`` in the sidecar via the SDK. Returns
    ``(returncode, stdout, stderr)`` (full text; callers slice tails).

    Raises CrawlTimeout on budget expiry (transient), CrawlError with a loud
    ``docker socket unreachable ...`` message when the daemon/socket (or the
    container) cannot be reached.
    """
    from .client import CrawlError, CrawlTimeout, classify_error

    try:
        import docker
    except ImportError as e:
        raise CrawlError(f"docker SDK unavailable: {e}", "transient") from e
    try:
        client = docker.from_env(timeout=max(int(timeout_s) + 30, 60))
        ctr = client.containers.get(container)
    except Exception as e:  # noqa: BLE001 -- socket/daemon gone: LOUD, not 500-blank
        raise CrawlError(
            f"docker socket unreachable (container {container!r}): {e}",
            "transient") from e
    box: dict = {}
    try:
        def _run() -> None:
            try:
                box["res"] = ctr.exec_run(list(LNCCMD) + list(argv),
                                          demux=True)
            except Exception as e:  # noqa: BLE001 -- stored, raised below
                box["err"] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout_s if timeout_s > 0 else None)
        if t.is_alive():
            raise CrawlTimeout(
                f"exec timed out after {timeout_s}s; exec left running in "
                f"{container!r} (exec IDs cannot be SIGKILLed remotely — "
                "orphan is harmless: per-job dirs + jobs lease/gate)")
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001 -- best effort
            pass
    if "err" in box:
        e = box["err"]
        raise CrawlError(f"sidecar exec failed: {e}",
                         classify_error(1, str(e))) from e
    rc_raw, out = box.get("res", (1, (b"", b"")))
    try:
        rc = int(rc_raw)
    except (TypeError, ValueError):
        rc = 1
    if isinstance(out, (bytes, bytearray)) or out is None:
        return rc, _decode(out), ""
    first, second = (list(out) + [b"", b""])[:2]
    return rc, _decode(first), _decode(second)


def local_exec(prefix_argv: list[str],
               timeout_s: float) -> tuple[int, str, str]:
    """Hermetic subprocess path for the ``exec_prefix`` test hook (no daemon
    needed). SIGKILLs the child on timeout and raises CrawlTimeout."""
    from .client import CrawlTimeout

    proc = subprocess.Popen(prefix_argv, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        return proc.returncode, stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGKILL)
            stdout, stderr = proc.communicate()
        raise CrawlTimeout(
            f"exec timed out after {timeout_s}s (SIGKILLed)") from None
