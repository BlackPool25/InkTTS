"""Browser E2E: real headless Chrome, unpacked extension, fixture chapter.

Drives through the POPUP page (full extension APIs) the way a user would:
popup -> tabs.sendMessage(TOGGLE_OVERLAY) -> content extracts real DOM ->
overlay mounts -> seek moves highlight. Challenge page -> loud error.
Server-half E2E is proof/e2e_live.py (live Kokoro render -> cache -> audio).
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


def ext_id(profile: str) -> str:
    import time
    pref = Path(profile, "Default/Preferences")
    for _ in range(40):
        if pref.exists():
            try:
                prefs = json.loads(pref.read_text())
                settings = prefs["extensions"]["settings"]
                for eid, info in settings.items():
                    if info.get("manifest", {}).get("name", "") == "InkCast Read-Along":
                        return eid
                ids = list(settings.keys())
                if len(ids) == 1:
                    return ids[0]
            except (json.JSONDecodeError, KeyError):
                pass
        time.sleep(0.5)
    raise AssertionError("extension id not found in Preferences")


TOGGLE = """async () => {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  await chrome.tabs.sendMessage(tab.id, {type: 'TOGGLE_OVERLAY'});
  return true;
}"""


def main():
    ext = str(Path("/home/shreyas/projects/InkCast/extension/.output/chrome-mv3"))
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            "/tmp/pw-profile",
            channel="chrome",
            headless=True,
            args=[f"--disable-extensions-except={ext}",
                  f"--load-extension={ext}"],
        )
        page = ctx.new_page()
        await_probe = page
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"http://127.0.0.1:{port}/novelbuddy-ch1.html")
        page.wait_for_timeout(1500)
        eid = ext_id("/tmp/pw-profile")
        print("extension id:", eid[:8] + "…")
        popup = ctx.new_page()
        for attempt in range(5):
            try:
                popup.goto(f"chrome-extension://{eid}/popup.html")
                break
            except Exception as e:
                print(f"popup goto retry {attempt}: {str(e)[:80]}")
                page.wait_for_timeout(2000)
                eid = ext_id("/tmp/pw-profile")
        else:
            raise AssertionError("popup.html never loaded")
        popup.wait_for_timeout(1000)
        popup.evaluate(TOGGLE)
        page.wait_for_timeout(1500)

        host = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host') !== null")
        print("overlay mounted:", host)
        assert host, "overlay did not mount"
        lines = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelectorAll('[data-testid=line],[data-testid=line-active]').length")
        print("sentences rendered:", lines)
        assert lines == 3, f"expected 3 sentences, got {lines}"
        active = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('[data-testid=line-active]').textContent.slice(0, 40)")
        print("active line:", active)
        page.evaluate(
            "() => { const h = document.getElementById('inkcast-overlay-host');"
            " h.shadowRoot.querySelectorAll('[data-testid=line]')[0].click(); }")
        page.wait_for_timeout(500)
        active2 = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('[data-testid=line-active]').textContent.slice(0, 40)")
        print("after seek:", active2)
        assert active2 != active, "seek did not move the active line"

        page.goto(f"http://127.0.0.1:{port}/blocked.html")
        page.wait_for_timeout(1200)
        popup.bring_to_front()
        popup.evaluate(TOGGLE)
        page.wait_for_timeout(1000)
        err = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            "?.shadowRoot.querySelector('[data-testid=overlay-error]')?.textContent?.slice(0, 60)")
        print("challenge overlay:", (err or "").strip()[:60])
        assert err and "bot check" in err, "challenge did not render loud error"
        print("pageerrors:", errors if errors else "none")
        ctx.close()
    print("BROWSER E2E DONE")


main()
