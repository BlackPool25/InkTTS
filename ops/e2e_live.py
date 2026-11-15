#!/usr/bin/env python3
"""InkCast BRUTAL end-to-end: every flow through the real wall, real state.
Not unit tests — live HTTPS against Caddy + real login + real sidecar.
Usage: python3 ops/e2e_live.py [--quick]
Exit 0 only if EVERY check passes. Prints PASS/FAIL + evidence per check.
"""
import json, sys, time, urllib.request, urllib.error

BASE = "https://192.168.29.101:8085"
PORTAL = "https://192.168.29.101:8443"
CA = "/home/shreyas/projects/InkCast/ops/lan.crt"
import ssl
# Trust anchor = live served cert (fp 2A:F4, dumped 2026-11-14; Caddy still
# serves the pre-regen cert — disk EC:B0 not yet reloaded) + disk CA fallback.
CTX = ssl.create_default_context()
try:
    CTX.load_verify_locations(cafile=CA)
except Exception:
    pass
for _extra in ("/tmp/inkcast-served-8085.crt", "/tmp/inkcast-served-8443.crt"):
    try:
        CTX.load_verify_locations(cafile=_extra)
    except Exception:
        pass
QUICK = "--quick" in sys.argv
PASS, FAIL = [], []
TOKEN = open("/home/shreyas/projects/InkCast/.env").read().split("API_TOKEN=")[1].split()[0]
PASSWD = open("/tmp/inkcast-bootpass.txt").read().strip()

def req(method, url, body=None, cookie=None, token=None, timeout=30):
    r = urllib.request.Request(url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    if cookie: r.add_header("Cookie", cookie)
    if token: r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, context=CTX, timeout=timeout) as h:
            return h.status, h.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def check(name, cond, evidence=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{evidence}]" if evidence else ""))

# 1. UI root (public static, no wall by design)
s, b = req("GET", BASE + "/")
check("ui-root-200-html", s == 200 and b"<head" in b[:200].lower(), f"http={s}")

# 2. Wall: anon API -> 302 portal (browsers/curl) or 401+Location (Authelia
# answers non-browser UAs e.g. Python-urllib with 401; both block anon).
# Verified 2026-11-14: curl (any UA/header) -> 302; urllib -> 401 + Location
# to :8443. Either is the wall working; NOT blocked would be 200 + JSON.
s, b = req("GET", BASE + "/api/v1/books")
# capture headers too (req drops them on HTTPError): 401 must carry Location->portal
try:
    rh = urllib.request.Request(BASE + "/api/v1/books")
    urllib.request.urlopen(rh, context=CTX, timeout=30)
    loc = ""
except urllib.error.HTTPError as e:
    loc = e.headers.get("Location", "")
walled = (s == 302) or (s == 401 and ":8443" in loc)
check("wall-anon-blocked", walled, f"http={s} loc={loc[:40]}")
s, b = req("POST", PORTAL + "/api/firstfactor",
           {"username": "reader", "password": PASSWD, "rememberMe": True})
ok_login = s == 200 and b"redirect" in b
check("portal-login-200", ok_login, f"http={s}")
COOKIE = None
if ok_login:
    # re-login capturing cookie
    class H(urllib.request.HTTPSHandler):
        pass
    r = urllib.request.Request(PORTAL + "/api/firstfactor",
        data=json.dumps({"username": "reader", "password": PASSWD, "rememberMe": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        h = urllib.request.urlopen(r, context=CTX, timeout=30)
        sc = h.headers.get("Set-Cookie", "")
    except Exception as e:
        sc = ""
    COOKIE = sc.split(";")[0] if "inkcast_session=" in sc else None
    check("session-cookie-issued", bool(COOKIE), (COOKIE or "")[:30])

def authed(m, path, body=None, token=None, timeout=30):
    return req(m, BASE + path, body, COOKIE, token, timeout)

if COOKIE:
    s, b = authed("GET", "/api/v1/books")
    check("books-authed-200-list", s == 200 and b.strip().startswith(b"["), f"http={s}")
    s, b = authed("GET", "/api/v1/health")
    try: h = json.loads(b)
    except Exception: h = {}
    check("health-deep-ok", s == 200 and h.get("status") == "ok", f"http={s} {str(h)[:80]}")
    # 3. live search (SLOW) — skip in --quick
    if not QUICK:
        t0 = time.time()
        s, b = authed("GET", "/api/v1/search?q=shadow+slave", timeout=120)
        try: hits = json.loads(b).get("results", [])
        except Exception: hits = None
        check("live-search-200-hits", s == 200 and isinstance(hits, list) and len(hits) > 5,
              f"http={s} hits={len(hits) if isinstance(hits,list) else '?'} took={time.time()-t0:.0f}s")
    # 4. add novel -> job queued
    s, b = authed("POST", "/api/v1/books/add",
                  {"url": "https://novelbuddy.com/shadow-slave"})
    try: j = json.loads(b)
    except Exception: j = {}
    jid = j.get("job_id")
    check("add-queues-job", s == 200 and bool(jid), f"http={s} job={jid}")
    # 5. THE BRUTAL ONE: does anything ever RUN the job?
    if jid:
        ran = False
        for _ in range(6 if QUICK else 24):  # 30s quick / 2min full
            time.sleep(5)
            s2, b2 = authed("GET", f"/api/v1/books/add/status/{jid}")
            try: st = json.loads(b2)
            except Exception: st = {}
            state = str(st.get("status", st))
            if "running" in state or "done" in state or "complete" in state:
                ran = True
                break
        check("job-actually-runs", ran, f"last={state[:60]}")
    # 6. strict-token admin surface
    s, _ = authed("GET", "/api/v1/jobs")
    check("jobs-rejects-session-needs-token", s in (401, 403), f"http={s}")
    s, _ = authed("GET", "/api/v1/jobs", token=TOKEN)
    check("jobs-accepts-token", s == 200, f"http={s}")
    # 7. OPDS
    s, _ = req("GET", BASE + "/api/v1/opds/catalog.xml?token=wrong")
    check("opds-bad-token-401", s == 401, f"http={s}")
    # 8. backup export (token)
    s, b = authed("POST", "/api/v1/admin/backup", {}, token=TOKEN, timeout=60)
    check("backup-export-200", s == 200 and b"sha256" in b, f"http={s}")
    # 9. voice/pron surfaces (token-gated by design: PWA sends Bearer from
    # localStorage inkcast_token; session alone MUST 401 — see jobs test above)
    s, _ = authed("PUT", "/api/v1/books/1/voice-map", {"cast": {"narrator": "af_heart"}})
    check("voice-map-rejects-session-needs-token", s == 401, f"http={s}")
    s, b = authed("PUT", "/api/v1/books/1/voice-map", {"cast": {"narrator": "af_heart"}}, token=TOKEN)
    check("voice-map-put", s in (200, 404), f"http={s} {b[:80].decode(errors='replace')}")
    s, b = authed("POST", "/api/v1/voices/sample-preview", {"text": "hi", "voice": "af_heart"}, token=TOKEN)
    check("sample-preview", s in (200, 501), f"http={s} {b[:80].decode(errors='replace')}")
    # 10. progress
    s, _ = authed("PUT", "/api/v1/books/1/progress",
                  {"chapter": 1, "sent_id": 5, "position_s": 12.5})
    check("progress-rejects-session-needs-token", s == 401, f"http={s}")
    s, b = authed("PUT", "/api/v1/books/1/progress",
                  {"chapter": 1, "sent_id": 5, "position_s": 12.5}, token=TOKEN)
    check("progress-push", s in (200, 404), f"http={s} {b[:80].decode(errors='replace')}")
    # 11. WS handshake through the real wall (proper Host so Caddy routes to
    # backend; session cookie so forward-auth passes; token for the app gate).
    # NOTE 2026-11-14: FAILS live — backend StaticFiles mount (registered
    # before the WS route) closes ALL websocket handshakes -> 403. Product
    # bug in backend/app/main.py (route order), not a script artifact:
    # verified direct-to-backend (bypass Caddy) also 403 + uvicorn
    # '"WebSocket /api/v1/ws?token=..." 403'.
    try:
        import socket
        sk = socket.create_connection(("192.168.29.101", 8085), timeout=10)
        sk = CTX.wrap_socket(sk, server_hostname="192.168.29.101")
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        sk.sendall(f"GET /api/v1/ws?token={TOKEN} HTTP/1.1\r\nHost: 192.168.29.101:8085\r\nCookie: {COOKIE}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        resp = sk.recv(200).decode(errors="replace")
        sk.close()
        check("ws-upgrade-with-token", "101" in resp, resp.split("\r\n")[0][:60])
        # bad token must NOT upgrade (fail-closed at some layer: wall 401/302
        # without cookie; app 4401-close after fix; today: StaticFiles 403)
        sk = socket.create_connection(("192.168.29.101", 8085), timeout=10)
        sk = CTX.wrap_socket(sk, server_hostname="192.168.29.101")
        sk.sendall(f"GET /api/v1/ws?token=WRONG HTTP/1.1\r\nHost: 192.168.29.101:8085\r\nCookie: {COOKIE}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        resp2 = sk.recv(200).decode(errors="replace")
        sk.close()
        check("ws-rejects-bad-token", "101" not in resp2, resp2.split("\r\n")[0][:60])
    except Exception as e:
        check("ws-upgrade-with-token", False, f"{type(e).__name__}: {e}"[:80])

print(f"\n== {len(PASS)} passed, {len(FAIL)} failed ==")
for f in FAIL: print("FAILED:", f)
sys.exit(1 if FAIL else 0)
