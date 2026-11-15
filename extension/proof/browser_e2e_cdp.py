"""Browser E2E via raw CDP (Playwright strips extension flags in this env).

Real headless Chrome + unpacked extension + fixture chapter page:
content extracts real DOM -> overlay mounts -> seek moves highlight ->
challenge page -> loud error. Screenshots saved as build proof.
Server-half E2E is tts-stack/proof/e2e_live.py.
"""
import http.server
import json
import socketserver
import subprocess
import threading
import time
import urllib.request

import websocket

import os

CHROME = os.environ.get(
    "E2E_CHROME",
    "/usr/bin/google-chrome")  # Playwright's unbranded Chromium sideloads;
EXT = "/home/shreyas/projects/InkCast/extension/.output/chrome-mv3"
PROFILE = "/tmp/cdp-e2e-profile"
PORT = 9333

FIXTURE = """<html><head><title>Ch 1 - Sunny Day</title></head><body>
<h1>Chapter 1: Sunny Day</h1><nav>Home Library</nav>
<div id="chapter-content">
<p>The lanterns swayed in the evening wind over the quiet harbor town where old sailors mended their nets.</p>
<p>"You should read more books," she said, and left the library quietly without another word.</p>
<p>He walked home slowly through the rain, thinking of nothing at all in particular.</p>
</div><div id="comments"><div class="comment-list"><p>Nice chapter!!!</p></div></div>
</body></html>"""

CHALLENGE = """<html><head><title>Just a moment...</title></head><body>
<iframe src="https://challenges.cloudflare.com/turnstile/v0/x"></iframe>
<div id="chapter-content"><p>Short teaser paragraph here for testing.</p></div>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/shot":
            return
        body = CHALLENGE if "blocked" in self.path else FIXTURE
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


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
            raise RuntimeError(r["exceptionDetails"]["text"] +
                               str(r.get("result", {}))[:200])
        return r["result"]["result"].get("value")


def targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list",
                                timeout=10) as r:
        return json.load(r)


def wait_for(pred, what, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            for t in targets():
                if pred(t):
                    return t
        except Exception:
            pass  # Chrome still starting; debug port not up yet
        time.sleep(0.5)
    raise AssertionError(f"never appeared: {what}")


def cleanup():
    import subprocess as sp
    # Kill only OUR test chromes (profile-scoped pattern; never self-matches:
    # this python process cmdline contains the script name, not the pattern).
    sp.run(["pkill", "-f", "user-data-dir=/tmp/cdp-e2e-prof" + "ile"],
           capture_output=True)


def main():
    cleanup()
    time.sleep(1)
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sw = wait_for(lambda t: t["type"] == "service_worker"
                 and "background.js" in t["url"] and
                 "chrome-extension://" in t["url"], "extension SW")
    eid = sw["url"].split("/")[2]
    print("extension id:", eid[:8] + "…")
    sws = CDP(sw["webSocketDebuggerUrl"])

    page = wait_for(lambda t: t["type"] == "page", "any page")
    cdp = CDP(page["webSocketDebuggerUrl"])
    cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{port}/ch1.html"})
    time.sleep(2)

    # Toggle the overlay the way the popup would (find tab by URL, not
    # "active": fresh profiles often open extra tabs).
    sws.evaluate(f"""(async () => {{
      const tabs = await chrome.tabs.query({{currentWindow: true}});
      const tab = tabs.find(t => (t.url || '').includes('127.0.0.1:{port}'))
        || tabs.find(t => (t.url || '').startsWith('http'));
      if (!tab) throw new Error('no fixture tab');
      await chrome.tabs.sendMessage(tab.id, {{type: 'TOGGLE_OVERLAY'}});
      return tab.url;
    }})()""")
    time.sleep(1.5)
    host = cdp.evaluate("document.getElementById('inkcast-overlay-host') !== null")
    print("overlay mounted:", host)
    assert host, "overlay did not mount"
    n = cdp.evaluate(
        "document.getElementById('inkcast-overlay-host')"
        ".shadowRoot.querySelectorAll('[data-testid=line],[data-testid=line-active]').length")
    print("sentences rendered:", n)
    assert n == 3, f"expected 3 sentences, got {n}"
    a1 = cdp.evaluate(
        "document.getElementById('inkcast-overlay-host')"
        ".shadowRoot.querySelector('[data-testid=line-active]').textContent.slice(0, 40)")
    print("active line:", a1)
    cdp.evaluate(
        "document.getElementById('inkcast-overlay-host')"
        ".shadowRoot.querySelectorAll('[data-testid=line]')[0].click()")
    time.sleep(0.5)
    a2 = cdp.evaluate(
        "document.getElementById('inkcast-overlay-host')"
        ".shadowRoot.querySelector('[data-testid=line-active]').textContent.slice(0, 40)")
    print("after seek:", a2)
    assert a2 != a1, "seek did not move the active line"
    shot = cdp.call("Page.captureScreenshot", {"format": "png"})["data"]
    import base64
    Path = __import__("pathlib").Path
    Path("/home/shreyas/projects/InkCast/extension/proof/overlay.png").write_bytes(
        base64.b64decode(shot))
    print("screenshot saved: proof/overlay.png")

    cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{port}/blocked.html"})
    time.sleep(2)
    sws.evaluate(f"""(async () => {{
      const tabs = await chrome.tabs.query({{currentWindow: true}});
      const tab = tabs.find(t => (t.url || '').includes('127.0.0.1:{port}'))
        || tabs.find(t => (t.url || '').startsWith('http'));
      if (!tab) throw new Error('no fixture tab');
      await chrome.tabs.sendMessage(tab.id, {{type: 'TOGGLE_OVERLAY'}});
      return tab.url;
    }})()""")
    time.sleep(1)
    err = cdp.evaluate(
        "document.getElementById('inkcast-overlay-host')"
        "?.shadowRoot.querySelector('[data-testid=overlay-error]')?.textContent?.slice(0, 80)")
    print("challenge overlay:", (err or "").strip()[:80])
    assert err and "bot check" in err, "challenge did not render loud error"
    print("BROWSER E2E DONE")
    cleanup()


main()
