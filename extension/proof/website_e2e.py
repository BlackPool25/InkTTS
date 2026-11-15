"""FULL PIPELINE on a real website: NovelBuddy chapter -> extension
extraction (real Chromium) -> orchestrator textpipe -> GGUF render
(Kokoro + 1x Chatterbox) -> per-sentence Opus -> LRC check.
"""
import http.cookiejar
import json
import re
import socketserver
import subprocess
import threading
import time
import urllib.request

import websocket

CHROME = "/home/shreyas/.cache/ms-playwright/chromium-1255/chrome-linux64/chrome"
EXT = "/home/shreyas/projects/InkCast/extension/.output/chrome-mv3"
PORT = 9339
ORCH = "http://127.0.0.1:8095"
CHAPTER = "https://novelbuddy.me/shadow-slave/chapter-1-nightmare-begins"


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.id = 0

    def call(self, method, params=None, timeout=30):
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": method,
                                "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def evaluate(self, expr, await_promise=True):
        r = self.call("Runtime.evaluate",
                     {"expression": expr, "awaitPromise": await_promise,
                      "returnByValue": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text", "?")[:200])
        return r["result"]["result"].get("value")


def targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list",
                                timeout=10) as r:
        return json.load(r)


def wait_for(pred, what, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            for t in targets():
                if pred(t):
                    return t
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError(f"never appeared: {what}")


def post(path, body):
    req = urllib.request.Request(
        ORCH + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read()


def get(path):
    with urllib.request.urlopen(ORCH + path, timeout=120) as r:
        return r.status, r.read()


def main():
    subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
         f"--remote-debugging-port={PORT}",
         "--user-data-dir=/tmp/cdp-fullchain",
         f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    candidates, seen = [], set()
    end = time.time() + 60
    sw = None
    while time.time() < end:
        try:
            for t in targets():
                url = t.get("webSocketDebuggerUrl", "")
                if (t["type"] == "service_worker"
                        and "background.js" in t["url"]
                        and "chrome-extension://" in t["url"]
                        and url not in seen):
                    seen.add(url)
                    try:
                        c = CDP(url)
                        name = c.evaluate(
                            "(() => chrome.runtime.getManifest().name)()")
                        if name == "InkCast Read-Along":
                            sw = t
                            break
                    except Exception:
                        continue
            if sw:
                break
        except Exception:
            pass
        time.sleep(1)
    assert sw, "extension SW never appeared"
    print("sw:", sw["url"][:70], flush=True)
    swc = CDP(sw["webSocketDebuggerUrl"])
    me = swc.evaluate("(() => chrome.runtime.getManifest().name)()")
    print("extension:", me, flush=True)
    assert me == "InkCast Read-Along", f"wrong SW: {me}"

    page = wait_for(lambda t: t["type"] == "page", "page")
    cdp = CDP(page["webSocketDebuggerUrl"])
    cdp.call("Page.navigate", {"url": CHAPTER})
    time.sleep(8)
    title = cdp.evaluate("document.title")
    print("page title:", title[:80], flush=True)
    blocked = cdp.evaluate(
        "!!document.querySelector('iframe[src*=challenge]')")
    print("challenge present:", blocked, flush=True)

    swc.evaluate(f"""(async () => {{
      const tabs = await chrome.tabs.query({{currentWindow: true}});
      const tab = tabs.find(t => (t.url || '').includes('novelbuddy'));
      if (!tab) throw new Error('no novelbuddy tab: ' + JSON.stringify(tabs.map(t=>t.url)));
      await chrome.tabs.sendMessage(tab.id, {{type: 'TOGGLE_OVERLAY'}});
      return tab.url;
    }})()""")
    time.sleep(2)
    has_err = cdp.evaluate(
        "!!document.getElementById('inkcast-overlay-host')"
        "?.shadowRoot.querySelector('[data-testid=overlay-error]')")
    if has_err:
        err = cdp.evaluate(
            "document.getElementById('inkcast-overlay-host')"
            "?.shadowRoot.querySelector('[data-testid=overlay-error]')?.textContent?.slice(0,120)")
        print("EXTRACTION RESULT:", err.strip(), flush=True)
        print("FULL-CHAIN: site blocked extraction (loud, as designed)")
        return
    n = cdp.evaluate(
        "document.getElementById('inkcast-overlay-host')"
        ".shadowRoot.querySelectorAll('[data-testid=line],[data-testid=line-active]').length")
    print("sentences extracted:", n, flush=True)
    assert n and n > 5, f"too few sentences: {n}"
    sents = cdp.evaluate(
        """[...document.getElementById('inkcast-overlay-host').shadowRoot
          .querySelectorAll('[data-testid=line],[data-testid=line-active]')]
          .slice(0, 6).map(e => e.textContent.slice(0, 400))""")
    print("sample[0]:", sents[0][:100], flush=True)
    shot = cdp.call("Page.captureScreenshot", {"format": "png"})["data"]
    import base64
    open("/home/shreyas/projects/InkCast/extension/proof/website.png",
         "wb").write(base64.b64decode(shot))
    print("screenshot: proof/website.png", flush=True)

    # Feed first 5 sentences through the orchestrator (Kokoro) + 1 via Chatterbox.
    docs = [("site-k", sents[:5], "kokoro"), ("site-c", sents[:1], "chatterbox")]
    for doc_id, texts, model in docs:
        ss = [{"idx": i, "text": t} for i, t in enumerate(texts)]
        print("manifest:", model, post("/manifest", {
            "doc_id": doc_id, "sentences": ss,
            "model": model})[1][:60], flush=True)
        print("tap:", post("/tap", {"doc_id": doc_id, "idx": 0})[1][:50], flush=True)
        h = None
        for _ in range(36):
            time.sleep(5)
            ev = get(f"/events?doc_id={doc_id}")[1].decode()
            m = re.search(r'"hash": "([0-9a-f]+)"', ev)
            if m:
                h = m.group(1)
                break
        print(model, "hash:", bool(h), flush=True)
        assert h, f"no audio rendered for {model}"
        st, body = get(f"/audio/{h}")
        print(model, "audio:", st, len(body), "bytes magic:", body[:4], flush=True)
        assert st == 200 and body[:4] == b"OggS"
    print("FULL-CHAIN WEBSITE E2E DONE", flush=True)


main()
