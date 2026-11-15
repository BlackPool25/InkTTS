"""FULL PIPELINE on a real website (Playwright path — proven extension
loading; raw CDP can't reliably sideload in this env).

NovelBuddy chapter -> extension extraction (real Chromium) -> orchestrator
textpipe -> GGUF render (Kokoro + 1x Chatterbox) -> per-sentence Opus.
"""
import json
import re
import time
import urllib.request

from playwright.sync_api import sync_playwright

EXT = "/home/shreyas/projects/InkCast/extension/.output/chrome-mv3"
EXE = "/home/shreyas/.cache/ms-playwright/chromium-1255/chrome-linux64/chrome"
ORCH = "http://127.0.0.1:8095"
CHAPTER = "https://novelbuddy.me/shadow-slave/chapter-1-nightmare-begins"


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
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            "/tmp/pw-site", executable_path=EXE, headless=False,
            args=[f"--disable-extensions-except={EXT}",
                  f"--load-extension={EXT}",
                  "--headless=new", "--no-sandbox",
                  "--disable-dev-shm-usage"])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(CHAPTER, timeout=60000)
        page.wait_for_timeout(8000)
        print("title:", page.title()[:80], flush=True)

        ours = None
        for _ in range(30):
            for w in ctx.service_workers:
                try:
                    if w.evaluate("() => chrome.runtime.getManifest().name") == "InkCast Read-Along":
                        ours = w
                        break
                except Exception:
                    continue
            if ours:
                break
            page.wait_for_timeout(1000)
        assert ours, "extension SW never appeared"
        print("worker: ours", flush=True)

        ours.evaluate("""async () => {
          const tabs = await chrome.tabs.query({currentWindow: true});
          const tab = tabs.find(t => (t.url || '').includes('novelbuddy'));
          if (!tab) throw new Error('no novelbuddy tab');
          await chrome.tabs.sendMessage(tab.id, {type: 'TOGGLE_OVERLAY'});
        }""")
        page.wait_for_timeout(2000)
        err = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            "?.shadowRoot.querySelector('[data-testid=overlay-error]')?.textContent?.slice(0,120)")
        if err:
            print("EXTRACTION RESULT:", err.strip()[:120], flush=True)
            print("FULL-CHAIN: site blocked extraction (loud, as designed)")
            ctx.close()
            return
        n = page.evaluate(
            "() => document.getElementById('inkcast-overlay-host')"
            ".shadowRoot.querySelectorAll('[data-testid=line],[data-testid=line-active]').length")
        print("sentences extracted:", n, flush=True)
        assert n and n > 5, f"too few sentences: {n}"
        sents = page.evaluate(
            """[...document.getElementById('inkcast-overlay-host').shadowRoot
              .querySelectorAll('[data-testid=line],[data-testid=line-active]')]
              .slice(0, 6).map(e => e.textContent.slice(0, 400))""")
        print("sample[0]:", sents[0][:100], flush=True)
        page.screenshot(path="/home/shreyas/projects/InkCast/extension/proof/website.png")
        print("screenshot: proof/website.png", flush=True)
        print("pageerrors:", errors if errors else "none", flush=True)

        for doc_id, texts, model in [
                ("site-k", sents[:5], "kokoro"),
                ("site-c", sents[:1], "chatterbox")]:
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
        ctx.close()
    print("FULL-CHAIN WEBSITE E2E DONE", flush=True)


main()
