import hashlib
import json
import re
import time
import urllib.request

BASE = "http://127.0.0.1:8095"


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.status, r.read()


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return r.status, r.read()


outs = {}
for model, did, text in [
        ("kokoro", "final-k",
         "A frail-looking young man with pale skin sat on a rusty bench."),
        ("chatterbox", "final-c",
         "She said nothing and kept walking through the empty marketplace.")]:
    post("/manifest", {"doc_id": did, "sentences": [{"idx": 0, "text": text}],
                       "model": model})
    post("/tap", {"doc_id": did, "idx": 0})
    h, dg = None, []
    for _ in range(36):
        time.sleep(5)
        ev = get(f"/events?doc_id={did}")[1].decode()
        dg = re.findall(r'"degraded": (true|false)', ev)
        m = re.search(r'"hash": "([0-9a-f]+)"', ev)
        if m:
            h = m.group(1)
            break
    st, body = get(f"/audio/{h}")
    outs[model] = body
    print(model, "degraded:", dg, "audio:", st, len(body),
          "magic:", body[:4], flush=True)

open("/tmp/final-k.opus", "wb").write(outs["kokoro"])
open("/tmp/final-c.opus", "wb").write(outs["chatterbox"])
print("kokoro md5:", hashlib.md5(outs["kokoro"]).hexdigest(), flush=True)
print("chatterbox md5:", hashlib.md5(outs["chatterbox"]).hexdigest(), flush=True)
print("distinct engines:", outs["kokoro"] != outs["chatterbox"], flush=True)
print("BOTH-ENGINES DONE", flush=True)
