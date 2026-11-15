import json
import re
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8099"


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.status, r.read()


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return r.status, r.read(), dict(r.headers)


import uvicorn
from orchestrator import api, server

stop = server.start()
srv = threading.Thread(
    target=uvicorn.run,
    kwargs={"app": api.app, "host": "127.0.0.1", "port": 8099,
            "log_level": "error"},
    daemon=True)
srv.start()
time.sleep(3)

print("health:", get("/health")[1][:120], flush=True)
sents = [
    {"idx": 0, "text": "The lanterns swayed in the evening wind over the quiet harbor town."},
    {"idx": 1, "text": "She said nothing and kept walking through the empty marketplace."},
    {"idx": 2, "text": "He followed at a distance, silent as the morning fog itself."},
]
print("manifest:", post("/manifest", {
    "doc_id": "e2e1", "sentences": sents, "voice": "af_heart",
    "speed": 1.0, "model": "kokoro"})[1][:80], flush=True)
print("tap:", post("/tap", {"doc_id": "e2e1", "idx": 1})[1][:80], flush=True)
time.sleep(60)  # CPU Kokoro renders 3 sentences
ev = get("/events?doc_id=e2e1")[1].decode()
readies = [ln for ln in ev.splitlines() if "ready" in ln]
print("ready events:", len(readies), flush=True)
m = re.search(r'"hash": "([0-9a-f]+)"', ev)
print("hash found:", bool(m), flush=True)
if m:
    st, body, hdrs = get(f"/audio/{m.group(1)}")
    print("audio:", st, len(body), "bytes, magic:", body[:4], flush=True)
stop.set()
print("E2E DONE", flush=True)
