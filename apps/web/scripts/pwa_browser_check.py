"""The installable web app in a real browser (M13 4.7, #481). Dev-only, as scripts/mobile_check.py.

    cd apps/web && npm run check:pwa      (builds to /tmp/kb-dist-pwa, then runs this)

Serves the build from a local port as Vercel does (a missing path answers the page), next to a second port that
stands in for the data API (another host), and opens the site in headless Chromium at a phone's size. It checks:

1. Chromium reads the manifest and finds nothing that stops an install (its own list of installability errors;
   "in-incognito" is the test browser's private window, not the site).
2. The service worker registers, takes control of the page, and its store holds exactly the app shell of the build.
3. Data is never stored: /api/marks and the data API answer differently on every call, online, with the worker in
   control; neither is in the store afterwards; offline both fail instead of answering from anywhere.
4. Offline, the app still opens on any route (the stored page and scripts), and shows its own screen.
5. A new build replaces the old store.

Exit code 1 and one line per failure.
"""
from __future__ import annotations

import argparse
import contextlib
import http.server
import itertools
import json
import mimetypes
import pathlib
import re
import sys
import threading

from playwright.sync_api import sync_playwright

PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "device_scale_factor": 2}
IGNORED = {"in-incognito"}  # Playwright's context is a private window; a real phone's browser is not


def site(dist: pathlib.Path, hits: itertools.count) -> http.server.ThreadingHTTPServer:
    """The build, as Vercel serves it: files as they are, /api/marks from "the function" (a new answer every call),
    any other path the page."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path.startswith("/api/"):
                body, kind = json.dumps({"n": next(hits)}).encode(), "application/json"
            else:
                f = dist / path.lstrip("/")
                if path == "/" or not f.is_file():
                    f = dist / "index.html"
                body = f.read_bytes()
                kind = ("application/manifest+json" if f.suffix == ".webmanifest"
                        else mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "public, max-age=0, must-revalidate")  # Vercel's answer for a file
            _send(self, body)

        def log_message(self, *_):
            pass

    return _start(Handler)


def data_api(hits: itertools.count) -> http.server.ThreadingHTTPServer:
    """Another host, as Supabase is to the site: answers every call with a new number, with CORS open. (It sends no
    caching header, as the data API sends none: the browser's own cache obeys the server and is not the worker's.)"""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"n": next(hits)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            _send(self, body)

        def log_message(self, *_):
            pass

    return _start(Handler)


def _send(h: http.server.BaseHTTPRequestHandler, body: bytes) -> None:
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):  # the browser went offline mid-answer
        h.wfile.write(body)


def _start(handler) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


STORE = """async () => {
  const out = {};
  for (const k of await caches.keys()) {
    out[k] = (await (await caches.open(k)).keys()).map((r) => new URL(r.url).pathname).sort();
  }
  return out;
}"""
ASK = """async (url) => {
  try { const r = await fetch(url); return (await r.json()).n; } catch (e) { return 'failed'; }
}"""


def run(dist: pathlib.Path) -> list[str]:
    fails: list[str] = []
    need = lambda ok, msg: None if ok else fails.append(msg)  # noqa: E731
    shell = json.loads(re.search(r"^const SHELL = (\[.*\]);$", (dist / "sw.js").read_text(), re.M)[1])
    store_name = re.search(r'^const CACHE = "([^"]+)";$', (dist / "sw.js").read_text(), re.M)[1]
    hits = itertools.count(1)
    web, api = site(dist, hits), data_api(hits)
    app, other = f"http://127.0.0.1:{web.server_port}", f"http://localhost:{api.server_port}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(**PHONE, color_scheme="dark")
        page = ctx.new_page()
        page.goto(app + "/", wait_until="load")

        # 1. installable
        cdp = ctx.new_cdp_session(page)
        manifest = cdp.send("Page.getAppManifest")
        need(not manifest.get("errors"), f"manifest: Chromium reports {manifest.get('errors')}")
        parsed = json.loads(manifest.get("data") or "{}")
        need(parsed.get("display") == "standalone" and parsed.get("start_url") == "/",
             "manifest: not standalone at the root")

        # 2. the worker controls the page and holds the shell, nothing else
        page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=20_000)
        store = page.evaluate(STORE)
        need(list(store) == [store_name], f"store: expected only {store_name}, found {list(store)}")
        need(store.get(store_name) == sorted(shell), "store: its files are not exactly the build's shell list")
        errors = [e["errorId"] for e in cdp.send("Page.getInstallabilityErrors").get("installabilityErrors", [])]
        need(not [e for e in errors if e not in IGNORED], f"installability: Chromium reports {errors}")

        # 3. data is never stored
        for url, what in ((app + "/api/marks", "/api/marks"), (other + "/rest/v1/rpc/kb_overview", "the data API")):
            a, b = page.evaluate(ASK, url), page.evaluate(ASK, url)
            need(isinstance(a, int) and isinstance(b, int) and b > a,
                 f"{what}: two calls answered {a} and {b}; each must reach the server")
        after = page.evaluate(STORE)
        need(after == store, f"store: changed after data calls: {sorted(set(sum(after.values(), [])) - set(shell))}")

        # 4. offline: the app opens on any route; data fails
        ctx.set_offline(True)
        page.goto(app + "/positions", wait_until="load")
        page.wait_for_selector("#root > *", timeout=10_000)
        need(len(page.inner_text("#root").strip()) > 20, "offline: the page is empty")
        for url, what in ((app + "/api/marks", "/api/marks"), (other + "/rest/v1/rpc/kb_overview", "the data API")):
            need(page.evaluate(ASK, url) == "failed", f"offline: {what} answered; it must fail")
        ctx.set_offline(False)

        # 5. a new build replaces the store
        old = (dist / "sw.js").read_text()
        try:
            (dist / "sw.js").write_text(old.replace(store_name, store_name + "-next"))
            page.goto(app + "/", wait_until="load")
            page.evaluate("navigator.serviceWorker.getRegistration().then((r) => r.update())")
            page.wait_for_function(f"caches.keys().then((k) => k.length === 1 && k[0] === '{store_name}-next')",
                                   timeout=20_000)
        except Exception as e:  # noqa: BLE001
            fails.append(f"update: the new build's store did not replace the old one ({str(e)[:80]})")
        finally:
            (dist / "sw.js").write_text(old)
        browser.close()
    web.shutdown()
    api.shutdown()
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dist", type=pathlib.Path, required=True)
    fails = run(ap.parse_args().dist.resolve())
    for f in fails:
        print(f"installable web app: {f}", file=sys.stderr)
    if not fails:
        print("installable web app: installable, the worker holds only the app shell, data is never stored, "
              "opens offline")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
