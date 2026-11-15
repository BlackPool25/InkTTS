"""Browser E2E via Playwright + unbranded Chromium (branded Chrome blocks
CLI sideloading; Playwright's bundled Chromium allows it).

Real browser, unpacked extension, fixture chapter: content extracts real
DOM -> overlay mounts with 3 sentences -> seek moves highlight ->
challenge page -> loud error. Screenshots saved as build proof.
Server-half E2E is tts-stack/proof/e2e_live.py.

Speed: fixed sleeps replaced by poll-until-condition (interval 0.25s,
generous timeouts). Settings screenshots could NOT overlap playback in a
thread: Playwright sync API forbids cross-thread calls AND the background
routes player->page messages to the active tab, so a 2nd frontmost tab
would blackhole LINE_AT. They run just before PLAY instead.
"""
import http.server
import socketserver
import threading
import time
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


def wait_until(fn, timeout, interval=0.25):
    """Poll fn() until truthy; return last value (falsy on timeout)."""
    deadline = time.monotonic() + timeout
    val = None
    while True:
        try:
            val = fn()
        except Exception:
            val = None
        if val:
            return val
        if time.monotonic() >= deadline:
            return val
        time.sleep(interval)


def main():
    t_all = time.monotonic()
    spent: dict[str, float] = {}

    def mark(name, t0):
        spent[name] = time.monotonic() - t0

    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            "/tmp/pw-e2e", executable_path=EXE, headless=False,
            ignore_default_args=["--mute-audio"],
            args=[f"--disable-extensions-except={EXT}",
                  f"--load-extension={EXT}",
                  "--headless=new", "--no-sandbox",
                  "--autoplay-policy=no-user-gesture-required",
                  "--disable-dev-shm-usage"])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"http://127.0.0.1:{port}/ch1.html")

        # Find OUR service worker (background.js + manifest name check).
        # No fixed initial sleep: poll immediately at 0.25s, generous 15s
        # ceiling covers cold start.
        t0 = time.monotonic()
        ours = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
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
            time.sleep(0.25)
        mark("worker-poll", t0)
        assert ours, "our service worker never appeared"
        print("worker: ours")
        eid = ours.evaluate("() => chrome.runtime.id")
        print("extension id:", eid[:8] + "…")

        # Content scripts run at document_idle: the listener may not exist
        # yet when goto resolves. PING until the tab answers instead of
        # sleeping a fixed 2s (PING has no handler; delivery alone proves
        # the listener is registered — a missing listener rejects).
        def content_ready(substr, timeout=10):
            return wait_until(
                lambda: ours.evaluate(
                    """async (substr) => {
                      const tabs = await chrome.tabs.query({currentWindow: true});
                      const tab = tabs.find(t => (t.url || '').includes(substr));
                      if (!tab) return false;
                      try { await chrome.tabs.sendMessage(tab.id, {type: 'PING'}); return true; }
                      catch (e) { return false; }
                    }""",
                    substr),
                timeout=timeout)

        t0 = time.monotonic()
        assert content_ready("ch1.html"), "content script never registered"
        ours.evaluate(
            """async () => {
              const tabs = await chrome.tabs.query({currentWindow: true});
              const tab = tabs.find(t => (t.url || '').includes('ch1.html'));
              await chrome.tabs.sendMessage(tab.id, {type: 'TOGGLE_OVERLAY'});
            }""")
        host = wait_until(
            lambda: page.evaluate(
                "() => document.getElementById('inkcast-overlay-host') !== null"),
            timeout=10)
        mark("mount", t0)
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
        t0 = time.monotonic()
        page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelectorAll('[data-testid=line]')[0].click()")
        a2 = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            a2 = page.evaluate(
                "() => document.getElementById('inkcast-overlay-host')"
                ".shadowRoot.querySelector('[data-testid=line-active]').textContent.slice(0, 40)")
            if a2 != a1:
                break
            time.sleep(0.25)
        mark("seek", t0)
        print("after seek:", (a2 or "").strip()[:60])
        assert a2 != a1, "seek did not move the active line"
        page.screenshot(path="/home/shreyas/projects/InkCast/extension/proof/overlay.png")
        print("screenshot saved: proof/overlay.png")

        # Modes: immersive-only (always on) + title header; theme cycle; settings panel.
        click = """(text) => {
          const h = document.getElementById('inkcast-overlay-host');
          const btns = [...h.shadowRoot.querySelectorAll('button')];
          const b = btns.find(x => x.textContent.includes(text));
          if (!b) throw new Error('no button ' + text);
          b.click();
        }"""
        t0 = time.monotonic()
        # Immersive-only: no Focus toggle exists anymore; the lit band + title are always on.
        n_imm = wait_until(
            lambda: (lambda v: v if v == 3 else None)(page.evaluate(
                "() => document.getElementById('inkcast-overlay-host')"
                ".shadowRoot.querySelectorAll('[data-testid=line],[data-testid=line-active]').length")),
            timeout=5)
        mark("immersive", t0)
        print("immersive lines:", n_imm)
        assert n_imm == 3, f"immersive should keep all 3, got {n_imm}"
        ink_cls = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('[data-testid=overlay]').className")
        assert "ink-immersive" in ink_cls, "overlay must be immersive-only"
        title = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('[data-testid=chapter-title]')?.textContent")
        print("chapter title:", (title or "").strip()[:60])
        far = page.evaluate(
            "() => !!document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelector('.ink-far, .ink-dim')")
        print("dim wash present:", far)
        page.screenshot(path="/home/shreyas/projects/InkCast/extension/proof/overlay-immersive.png")
        t0 = time.monotonic()
        page.evaluate(f"({click})('Theme')")
        cls = wait_until(
            lambda: (lambda v: v if "ink-theme-ambient" in (v or "") else None)(page.evaluate(
                "() => document.getElementById('inkcast-overlay-host')"
                ".shadowRoot.querySelector('[data-testid=overlay]').className")),
            timeout=5)
        mark("theme", t0)
        print("theme class:", cls)
        assert "ink-theme-ambient" in cls, "theme did not cycle to ambient"
        t0 = time.monotonic()
        page.evaluate(f"({click})('Settings')")
        panel = wait_until(
            lambda: page.evaluate(
                "() => !!document.getElementById('inkcast-overlay-host')"
                ".shadowRoot.querySelector('[data-testid=settings-panel]')"),
            timeout=5)
        mark("panel", t0)
        print("settings panel:", panel)
        assert panel, "in-overlay settings panel missing"
        page.screenshot(path="/home/shreyas/projects/InkCast/extension/proof/overlay-settings.png")

        # Settings pages render (popup + options) — layout proof. Runs on a
        # 2nd page BEFORE playback: the background routes player->page
        # messages (MANIFEST_OK/LINE_AT) to the ACTIVE tab, so no other tab
        # may be frontmost while audio advances. bringToFront() restores the
        # fixture tab before PLAY.
        t0 = time.monotonic()
        pop = ctx.new_page()
        try:
            pop.goto(f"chrome-extension://{eid}/popup.html")
            pop.wait_for_load_state("domcontentloaded", timeout=10000)
            pop.screenshot(path="/home/shreyas/projects/InkCast/extension/proof/popup.png")
            pop.goto(f"chrome-extension://{eid}/options.html")
            pop.wait_for_load_state("domcontentloaded", timeout=10000)
            pop.screenshot(path="/home/shreyas/projects/InkCast/extension/proof/options.png",
                           full_page=True)
        finally:
            pop.close()
        page.bring_to_front()
        mark("settings", t0)
        print("settings screenshots saved")

        # --- REAL PLAYBACK: server URL in storage, PLAY, auto-advance ---
        t0 = time.monotonic()
        ours.evaluate(
            "chrome.storage.local.set({'inkcast.settings': {serverUrl: 'http://127.0.0.1:8095'}})")
        ours.evaluate(
            """async () => {
              const tabs = await chrome.tabs.query({currentWindow: true});
              const tab = tabs.find(t => (t.url || '').includes('127.0.0.1'));
              await chrome.tabs.sendMessage(tab.id, {type: 'PLAY'});
            }""")
        a3 = None
        deadline = time.monotonic() + 180  # unchanged ceiling
        while time.monotonic() < deadline:
            a3 = page.evaluate(
                "() => document.getElementById('inkcast-overlay-host')"
                ".shadowRoot.querySelector('[data-testid=line-active]')?.textContent?.slice(0, 40)")
            if a3 and a3 != a2:
                break
            time.sleep(1)  # warm server advances in ~5s; no 5s quantization
        mark("playback", t0)
        print("auto-advanced to:", (a3 or "").strip()[:60], flush=True)
        assert a3 and a3 != a2, "playback did not auto-advance (no audio chain)"
        page.screenshot(path="/home/shreyas/projects/InkCast/extension/proof/overlay-playing.png")
        print("screenshot saved: proof/overlay-playing.png", flush=True)

        t0 = time.monotonic()
        page.goto(f"http://127.0.0.1:{port}/blocked.html")
        assert content_ready("blocked.html"), "content script never registered (blocked)"
        ours.evaluate(
            """async () => {
              const tabs = await chrome.tabs.query({currentWindow: true});
              const tab = tabs.find(t => (t.url || '').includes('blocked.html'));
              await chrome.tabs.sendMessage(tab.id, {type: 'TOGGLE_OVERLAY'});
            }""")
        err = wait_until(
            lambda: (lambda v: v if v and "bot check" in v else None)(page.evaluate(
                "() => document.getElementById('inkcast-overlay-host')"
                "?.shadowRoot.querySelector('[data-testid=overlay-error]')?.textContent?.slice(0, 80)")),
            timeout=15)
        mark("challenge", t0)
        print("challenge overlay:", (err or "").strip()[:80])
        assert err and "bot check" in err, "challenge did not render loud error"
        print("pageerrors:", errors if errors else "none")

        ctx.close()
    print("BROWSER E2E DONE")
    print("stage times (s): " + ", ".join(f"{k}={v:.1f}" for k, v in spent.items())
          + f", total={time.monotonic() - t_all:.1f}")


main()
