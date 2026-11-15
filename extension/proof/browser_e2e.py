"""Browser E2E: real headless Chrome, unpacked extension, fixture chapter.
Proves the browser half: content script extracts real DOM, overlay mounts
with sentences, seek re-renders, error states render. Server-half E2E is
proof/e2e_live.py (live Kokoro render -> cache -> audio bytes).
"""
import http.server
import json
import socketserver
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

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
    ext = str(Path("/home/shreyas/projects/InkCast/extension/.output/chrome-mv3"))
    srv = socketserver.TCPServer(("127.0.0.1", 8901), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            "/tmp/pw-profile",
            channel="chrome",
            headless=True,
            args=[f"--disable-extensions-except={ext}",
                  f"--load-extension={ext}",
                  "--autoplay-policy=no-user-gesture-required"],
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto("http://127.0.0.1:8901/novelbuddy-ch1.html")
        page.wait_for_timeout(1500)
        # Drive the content script the way the popup would.
        overlay = page.evaluate("""async () => {
          const tabs = await chrome.tabs.query({active: true, currentWindow: true});
          return tabs.length;
        }""") if False else None
        # Content scripts listen for runtime messages: send via the SW.
        workers = list(ctx.service_workers)
        assert workers, "extension service worker did not start"
        awaitable = workers[0].evaluate("""async () => {
          const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
          await chrome.tabs.sendMessage(tab.id, {type: 'TOGGLE_OVERLAY'});
          return tab.id;
        }""")
        page.wait_for_timeout(1500)

        host = page.evaluate("() => document.getElementById('inkcast-overlay-host') !== null")
        print("overlay mounted:", host)
        lines = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelectorAll('[data-testid=line],[data-testid=line-active]').length")
        print("sentences rendered:", lines)
        active = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('[data-testid=line-active]')?.textContent?.slice(0, 40)")
        print("active line:", active)
        # Seek: click second sentence, active line follows.
        page.evaluate(
            """() => { const h = document.getElementById('inkcast-overlay-host');
               h.shadowRoot.querySelectorAll('[data-testid=line]')[0].click(); }""")
        page.wait_for_timeout(500)
        active2 = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('[data-testid=line-active]')?.textContent?.slice(0, 40)")
        print("after seek:", active2)
        assert active2 != active, "seek did not move the active line"
        # Challenge page -> loud error overlay, never narration.
        page.goto("http://127.0.0.1:8901/blocked.html")
        page.wait_for_timeout(1200)
        workers[0].evaluate("""async () => {
          const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
          await chrome.tabs.sendMessage(tab.id, {type: 'TOGGLE_OVERLAY'});
        }""")
        page.wait_for_timeout(1000)
        err = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            "?.shadowRoot.querySelector('[data-testid=overlay-error]')?.textContent?.slice(0, 60)")
        print("challenge overlay:", err)
        assert err and "bot check" in err, "challenge did not render loud error"
        print("pageerrors:", errors if errors else "none")
        ctx.close()
    print("BROWSER E2E DONE")


main()
