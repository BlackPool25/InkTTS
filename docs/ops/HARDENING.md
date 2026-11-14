# Hardening gates (Task 8)

Five gates, each with an exact command and where its evidence lands. Gates 1
(partial), 2, 4, 5 run without compose; gate 3 needs the sidecar up. The fast
proof for all five is `pytest backend/tests/test_hardening.py` (14 tests).

## 0. Fast proof (no sidecar, no server)

```bash
cd /home/shreyas/projects/InkCast
python3 -m pytest backend/tests/test_hardening.py -q   # expect: 14 passed
python3 -m pytest backend/tests -q                     # expect: 60 passed
```

## 1. Range 206 + If-Range (audio route)

Needs compose up AND Task 2 chapter bytes (today the frozen audio path is
501 until render lands — the helper math is locked by tests now).

```bash
BASE=http://127.0.0.1
# single range -> 206 + Content-Range
curl -s -D - -o /dev/null -H 'Range: bytes=0-99' \
  "$BASE/api/v1/books/1/chapters/1/audio.opus" | grep -E 'HTTP|Content-Range'
# expect: HTTP/1.1 206 Partial Content / Content-Range: bytes 0-99/<size>

# open + suffix ranges -> 206
curl -s -o /dev/null -w '%{http_code}\n' -H 'Range: bytes=500-' "$BASE/api/v1/books/1/chapters/1/audio.opus"   # 206
curl -s -o /dev/null -w '%{http_code}\n' -H 'Range: bytes=-200' "$BASE/api/v1/books/1/chapters/1/audio.opus"   # 206

# If-Range: fresh ETag -> 206, stale -> full 200
ETAG=$(curl -s -D - -o /dev/null "$BASE/api/v1/books/1/chapters/1/audio.opus" | grep -i '^etag:' | tr -d '\r' | awk '{print $2}')
curl -s -o /dev/null -w '%{http_code}\n' -H 'Range: bytes=0-99' -H "If-Range: $ETAG" \
  "$BASE/api/v1/books/1/chapters/1/audio.opus"   # 206
curl -s -o /dev/null -w '%{http_code}\n' -H 'Range: bytes=0-99' -H 'If-Range: "stale"' \
  "$BASE/api/v1/books/1/chapters/1/audio.opus"   # 200
```

Evidence: curl headers. Unit lock: `test_range_single_open_suffix`,
`test_if_range_stale_forces_full_200` (`backend/app/playback.py`
`parse_range` / `if_range_allows`).

## 1b. M4B export smoke (needs book audio + ffmpeg in image)

```bash
TOKEN=<per-device OPDS token>
# build (or serve cached) the audiobook; expect 200 + audio/mp4
curl -s -D - -o /tmp/book.m4b \
  "http://127.0.0.1/api/v1/opds/books/<id>.m4b?token=$TOKEN" | grep -E 'HTTP|Content-Type'
# chapters survived the muxer? expect titled chapters, END[i]==START[i+1]
ffprobe -v error -show_chapters -of default=noprint_wrappers=1 /tmp/book.m4b | grep -E '^(start|end|TAG)'
```

Evidence: ffprobe chapter list. Unit lock: `test_m4b_chapter_math` (+43ms
contiguity at ffmetadata level) and `test_m4b_live_encode_roundtrip`
(real concat->AAC when ffmpeg exists; skipped otherwise). Known muxer
behavior: chapter[0].START clamps to media start (0); the shift lives on
chapters 2..N.

## 2. RTF bench p50 <= 0.7

Stub-tone bench (Kokoro ONNX absent). Real-model number must be re-measured
on the target with `KOKORO_MODEL_PATH` set; the gate value stays p50 <= 0.7
(Kokoro-82M CPU is typically ~0.1-0.3).

```bash
cd /home/shreyas/projects/InkCast
mkdir -p /tmp/inkcast-rtf-bench
DATABASE_URL=sqlite:////tmp/inkcast-rtf-bench/bench.db \
KOKORO_MODEL_PATH= OMP_NUM_THREADS=2 python3 - <<'EOF'
import statistics, time
from backend.app.db import create_all
create_all()
from sqlmodel import Session
from backend.app import db
from backend.app.models import Novel
with Session(db.engine) as s:  # render FKs chapters -> novels
    if s.get(Novel, 1) is None:
        s.add(Novel(id=1, title="RTF bench")); s.commit()
from backend.app.render import render_chapter_text
TEXT = ("Hello world. This is a test chapter with several sentences! " * 40)
rts = []
for i in range(5):
    t0 = time.perf_counter()
    rc = render_chapter_text(1, 900 + i, TEXT, "af_heart", 1.0, "/tmp/inkcast-rtf-bench", title="bench")
    import json, os
    with open(os.path.join("/tmp/inkcast-rtf-bench", rc.sidecar_path)) as f:
        dur = json.load(f)["sentences"][-1]["t_end"]
    rts.append((time.perf_counter() - t0) / dur)
p50 = statistics.median(rts)
print(f"RTFs={[round(x, 3) for x in rts]} p50={p50:.3f} gate={'PASS' if p50 <= 0.7 else 'FAIL'}")
EOF
# expect: p50 ~= 0.01 (stub) PASS. Real model: re-run with KOKORO_MODEL_PATH=/data/models/kokoro-82M.onnx
```

Evidence: bench stdout (keep with the run log). Prior stub run:
`backend/app/bench_note.txt`.

## 3. Solver check (sidecar image)

```bash
# exec works at all?
docker exec inkcast-lncrawl crawl --help >/dev/null && echo EXEC_OK
# solver / challenge marks in recent sidecar logs?
docker logs inkcast-lncrawl --tail 200 2>&1 | grep -i -E 'solver|cloudflare|captcha|just a moment| 403 ' \
  && echo SOLVER_MARKS_FOUND || echo NO_SOLVER_MARKS
```

Handling rule (locked): solver-missing output classifies `blocked`
(`backend/app/scraper/client.py: classify_error` + `SOLVER_MISSING_MARKS`),
which writes a `blocked` jobs row and a `data/quarantine/` artifact — the
source quarantines (Task 3 scheduler), nothing crashes, zero chapter rows.
Verify the mapping without the sidecar:

```bash
cd /home/shreyas/projects/InkCast
python3 -c "
from backend.app.scraper.client import classify_error
assert classify_error(1, 'lncrawl: challenge solver missing in image, cannot bypass') == 'blocked'
assert classify_error(1, 'boom') == 'transient'
print('SOLVER_QUARANTINE_PATH_OK')"
```

Unit lock: `test_solver_missing_quarantines_not_crashes`. Loud-alert check
(live): `curl -H "Authorization: Bearer $API_TOKEN" localhost:8000/api/v1/jobs`
shows the `blocked` row.

## 4. Restore drill (backup ZIP round-trip)

Scratch paths only — live `/data` untouched until the final promote, which
targets the scratch copy.

```bash
cd /home/shreyas/projects/InkCast
cp /data/db/inkcast.db /tmp/drill-live.db && mkdir -p /tmp/drill-data
python3 - <<'EOF'
from backend.app.backup import export_backup, import_backup
export_backup("/data/db/inkcast.db", "/data", "/tmp/drill.zip", passphrase="drill-phrase")
print("EXPORT_OK")
rep = import_backup("/tmp/drill.zip", "/tmp/drill-live.db", "/tmp/drill-data", passphrase="drill-phrase")
print("IMPORT_1:", rep["noop"], rep["restored_files"], rep["warnings"])
rep2 = import_backup("/tmp/drill.zip", "/tmp/drill-live.db", "/tmp/drill-data", passphrase="drill-phrase")
assert rep2["noop"] is True, rep2
print("IMPORT_2_IDEMPOTENT_OK")
EOF
# expect: EXPORT_OK / IMPORT_1: False <n> [] / IMPORT_2_IDEMPOTENT_OK
# last-good rule: a failed export never replaces the previous good ZIP
# (tmp + verify-before-rotate in export_backup).
```

Full-disk variant: fill is simulated by the preflight check raising
`DiskFullError` with the last good backup intact (see `test_backup.py`).

## 5. 6-voice ABX shortlist build

Shortlist + pick-by-ear procedure: `ops/ABX.md`. Build (needs the model):

```bash
cd /home/shreyas/projects/InkCast
KOKORO_MODEL_PATH=/data/models/kokoro-82M.onnx OMP_NUM_THREADS=2 python3 - <<'EOF'
import os
from backend.app.render import render_chapter_text
TEXT = ("The lanterns swayed in the evening wind. Mara quickened her pace. "
        "\"Wait for me!\" he shouted, breathless. The gate was already closing. Dr. Chen smiled.")
os.makedirs("/tmp/abx", exist_ok=True)
for v in ["af_heart", "af_bella", "af_sarah", "am_adam", "bf_emma", "bm_george"]:
    rc = render_chapter_text(1, 950, TEXT, v, 1.0, "/tmp/abx-data")
    import shutil
    shutil.copy(os.path.join("/tmp/abx-data", rc.audio_path), f"/tmp/abx/{v}.opus")
    print("built", v)
EOF
# listen blind (shuffled), score per ops/ABX.md, record the winner as default voice
```

Evidence: `/tmp/abx/*.opus` + the filled score sheet (paste into `ops/ABX.md`
run log). Without the model the stub tone is voice-identical — do NOT run
ABX on stub output.
