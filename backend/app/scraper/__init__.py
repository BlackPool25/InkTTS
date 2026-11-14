"""lncrawl sidecar client. lncrawl is the ONLY fetcher (no hand-rolled scrapers).

DATA-PATH VERDICT (image ghcr.io/lncrawl/lightnovel-crawler:4.14.0, v4 rewrite):
sidecar data dir is ``/data`` (``ENV LNCRAWL_DATA_PATH=/data`` in the upstream
Dockerfile; upstream docs run ``-v ./lncrawl-data:/data``). The old v3 default
``~/.lncrawl`` (``/root/.lncrawl``) NO LONGER APPLIES. docker-compose.yml still
mounts ``lncrawl-data:/root/.lncrawl`` — that ingests nothing on 4.14.0.
Fix = retarget ONLY that compose volume target to ``/data`` (backend keeps
reading it at ``/data/lncrawl``); no code change needed here. Verified against
upstream Dockerfile + ``lncrawl/commands/crawl`` CLI source (dev branch, same
v4 line as 4.14.0): ``ENV LNCRAWL_DATA_PATH``, ``crawl URL [--noin] [--all |
--first N | --last N] [--resume] [--refresh] [-f json|epub|...]``.
Not runtime-verified (image not pulled in this env); confirm once with:
``docker exec inkcast-lncrawl printenv LNCRAWL_DATA_PATH`` (expect ``/data``).

Notes carried from the plan: TZ matters for challenge clearance (image
defaults ``TZ=UTC``; set container TZ to the LAN zone when challenges fail),
arm64 is supported by the image, and concurrent execs share one browser
profile — single-flight enforced by jobs.MAX_INSTANCES_PER_KIND=1.
"""

from .client import (
    BACKEND_JOB_DIR,
    LNCRAWL_CONTAINER,
    LNCRAWL_DATA_PATH,
    SIDECAR_MOUNT,
    CrawlError,
    CrawlResult,
    CrawlTimeout,
    build_crawl_argv,
    classify_error,
    collect_artifacts,
    iter_zip_chapters,
    job_dir,
    new_files,
    run_crawl,
    snapshot_dir,
    sweep_stale_tmp,
)

__all__ = [
    "BACKEND_JOB_DIR",
    "LNCRAWL_CONTAINER",
    "LNCRAWL_DATA_PATH",
    "SIDECAR_MOUNT",
    "CrawlError",
    "CrawlResult",
    "CrawlTimeout",
    "build_crawl_argv",
    "classify_error",
    "collect_artifacts",
    "iter_zip_chapters",
    "job_dir",
    "new_files",
    "run_crawl",
    "snapshot_dir",
    "sweep_stale_tmp",
]
