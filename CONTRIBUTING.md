# Contributing (incremental build model)

Waves (dependency order — do not skip):
1. **Wave 0 — Foundation** (Task 1): contracts + DDL + compose. Blocks all.
   Gate: `docker compose config` + health deep green + Alembic idempotent.
2. **Wave 1 — Parallel** (Tasks 2-6, disjoint dirs): render, scraper, auth/ops,
   OpenReader fork, backup. Gate per task: happy + failure QA with evidence.
3. **Wave 2 — Integration** (Tasks 7-8): playback API, OPDS/M4B + hardening.
   Gate: Range 206 curl, RTF p50<=0.7, solver check, restore drill, ABX list.
4. **Docs pass** (this dir): every flow has a mermaid diagram; every table
   has an owner.

Reviews (required):
- Task review after each implementer (spec compliance + code quality).
- Phase review after each wave (cross-task contracts: manifest shape,
  route table, render_key pinning, token enforcement).
- Final review: F1 compliance, F2 quality, F3 manual QA, F4 scope fidelity.

Commits: `git commit -s` (Signed-off-by) per task, small + green. No commits
to `main` from two workers on the same files — Wave 1 owns disjoint paths
(see plan task Dir ownership).
