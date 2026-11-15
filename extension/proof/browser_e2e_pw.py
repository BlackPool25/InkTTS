"""Browser E2E via Playwright + unbranded Chromium (branded Chrome blocks
CLI sideloading; Playwright's bundled Chromium allows it).

Real browser, unpacked extension, fixture chapter: content extracts real
DOM -> overlay mounts with 3 sentences -> seek moves highlight ->
challenge page -> loud error. Screenshots saved as build proof.
Server-half E2E is tts-stack/proof/e2e_live.py.
"""
import http.server
import socketserver
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

EXT = str(Path("/home/shreyas/projects/InkCast/extension/.output/chrome-mv3").resolve())
EXE = "/home/shreyas/.cache/ms-playwright/chromium-1255/chrome-linux64/chrome"

FIXTURE = """<html><head><title>Ch 1 - Sunny Day</title></head><body>
<h1>Chapter 1: Sunny Day</h1><nav>Home Library</nav>
<div id="chapter-content">
<p>The lanterns swayed in the evening wind over the quiet harbor town where old sailors mended their nets.</p>
<p>"You should read more books," she said, and left the library quietly without another word.</p>
<p>He walked slowly through the dark marketplace, thinking of nothing at all in particular.</p>
</div><div id="comments"><div class="comment-list"><p>Nice chapter!!!</p></div></div>
</body></html>"""

CHALLENGE = """<html><head><title>Just a moment...</title></head><body>
<iframe src="https://challenges.cloudflare.com/turnstile/v0/x"></iframe>
<div id="chapter-content"><p>Short teaser paragraph here for testing.</p></div>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = CHALLENGE if "blocked" in self.path else FIXTURE
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def main():
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            "/tmp/pw-e2e", executable_path=EXE, headless=False,
            args=[f"--disable-extensions-except={EXT}",
                  f"--load-extension={EXT}",
                  "--headless=new", "--no-sandbox",
                  "--disable-dev-shm-usage"])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"http://127.0.0.1:{port}/ch1.html")
        page.wait_for_timeout(2000)

        # Find OUR service worker (background.js + manifest name check).
        ours = None
        for _ in range(20):
            for w in ctx.service_workers:
                try:
                    name = w.evaluate(
                        "() => chrome.runtime.getManifest().name")
                    if name == "InkCast Read-Along":
                        ours = w
                        break
                except Exception:
                    continue
            if ours:
                break
            page.wait_for_timeout(1000)
        assert ours, "our service worker never appeared"
        print("worker: ours")

        ours.evaluate(
            """async () => {
              const tabs = await chrome.tabs.query({currentWindow: true});
              const tab = tabs.find(t => (t.url || '').includes('ch1.html'));
              await chrome.tabs.sendMessage(tab.id, {type: 'TOGGLE_OVERLAY'});
            }""")
        page.wait_for_timeout(1500)
        host = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host') !== null")
        print("overlay mounted:", host)
        assert host, "overlay did not mount"
        n = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelectorAll('[data-testid=line],[data-testid=line-active]').length")
        print("sentences rendered:", n)
        assert n == 3, f"expected 3, got {n}"
        a1 = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('[data-testid=line-active]').textContent.slice(0, 40)")
        print("active line:", (a1 or "").strip()[:60])
        page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelectorAll('[data-testid=line]')[0].click()")
        page.wait_for_timeout(500)
        a2 = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('[data-testid=line-active]').textContent.slice(0, 40)")
        print("after seek:", (a2 or "").strip()[:60])
        assert a2 != a1, "seek did not move the active line"
        page.screenshot(path="/home/shreyas/projects/InkCast/extension/proof/overlay.png")
        print("screenshot saved: proof/overlay.png")

        page.goto(f"http://127.0.0.1:{port}/blocked.html")
        page.wait_for_timeout(1500)
        ours.evaluate(
            """async () => {
              const tabs = await chrome.tabs.query({currentWindow: true});
              const tab = tabs.find(t => (t.url || '').includes('blocked.html'));
              await chrome.tabs.sendMessage(tab.id, {type: 'TOGGLE_OVERLAY'});
            }""")
        page.wait_for_timeout(1000)
        err = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            "?.shadowRoot.querySelector('[data-testid=overlay-error]')?.textContent?.slice(0, 80)")
        print("challenge overlay:", (err or "").strip()[:80])
        assert err and "bot check" in err, "challenge did not render loud error"
        print("pageerrors:", errors if errors else "none")
        ctx.close()
    print("BROWSER E2E DONE")


main()
