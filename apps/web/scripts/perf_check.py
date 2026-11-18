"""First-load numbers of the built dashboard on a mid-range phone profile (#297). Dev-only; lab numbers, not field data.

    cd apps/web && VITE_SUPABASE_URL=http://localhost:54321 VITE_SUPABASE_PUBLISHABLE_KEY=mock npx vite build --outDir /tmp/kb-dist
    python3 scripts/perf_check.py --dist /tmp/kb-dist [--runs 5] [--json out.json] [--shots DIR]

The profile, the same for every run:
- Chromium (Playwright), phone emulation: 390 x 844, device scale 2, touch, cold cache (a new browser context per run).
- Network (Chrome DevTools throttling): 1.6 Mbit/s down, 750 kbit/s up, 150 ms latency per request ("slow 4G").
- CPU: 4x slowdown.
- The built files come from a local HTTP/1.1 server on this machine with gzip, and pass through the throttle. Vercel
  serves brotli over HTTP/2, so production transfers are somewhat smaller and less serial than these.
- The data calls (the placeholder Supabase URL) are answered from the design audit's mock inside the browser, with no
  network delay: no production data, account or key is used, and the API is the same before and after a change.
- Other hosts (the company logos, Google Fonts) cannot be reached from the build machine: logos are answered with a
  1 px image and counted; Google Fonts is refused and counted. Their bytes and time are NOT in these numbers.

Printed per page (the medians of --runs): first contentful paint, largest contentful paint, total blocking time (long
tasks over 50 ms before the page settles), layout shift, and the bytes that crossed the throttled link by kind.
--stall-google MS answers the question the lab cannot otherwise ask: what if Google Fonts is slow? The host is pointed at
a local port that holds the connection for MS and then drops it, and the page is measured as usual.
--shots also saves a settled screenshot of the home page at 390 px and 1280 px (unthrottled), to compare by eye or by hash.
"""
# ruff: noqa: E501  (browser scripts are JavaScript inside strings)
from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import http.server
import json
import mimetypes
import os
import pathlib
import socket
import statistics
import sys
import threading
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docs" / "design" / "audit_2026-11-16" / "tools"))
import audit  # noqa: E402  (the placeholder Supabase URL)
import mock_data  # noqa: E402

PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
NETWORK = {"offline": False, "latency": 150, "downloadThroughput": 1.6e6 / 8, "uploadThroughput": 750e3 / 8}
CPU_SLOWDOWN = 4
PAGES = [("home", "/", True), ("sign-in", "/", False)]
GOOGLE = "fonts.googleapis.com"
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
TEXT = (".js", ".css", ".html", ".svg", ".json", ".webmanifest", ".txt")

INIT = """
window.__p = { lcp: 0, cls: 0, tbt: 0 };
try {
  new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__p.lcp = e.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true });
  new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__p.cls += e.value; }).observe({ type: 'layout-shift', buffered: true });
  new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__p.tbt += Math.max(0, e.duration - 50); }).observe({ type: 'longtask', buffered: true });
} catch (e) {}
"""
READ = """() => {
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  const nav = performance.getEntriesByType('navigation')[0];
  const faces = [...document.fonts].filter((f) => f.status === 'loaded').map((f) => `${f.family.replace(/"/g, '')} ${f.weight}`).sort();
  const logos = [...document.querySelectorAll('.badge.logo img')];
  return { fcp: fcp ? fcp.startTime : null, lcp: window.__p.lcp, cls: window.__p.cls, tbt: window.__p.tbt, dcl: nav.domContentLoadedEventEnd,
    load: nav.loadEventEnd, faces, logoTiles: logos.length, logoTilesInView: logos.filter((i) => i.getBoundingClientRect().top < innerHeight).length };
}"""


def static_server(dist: pathlib.Path) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            path = self.path.split("?")[0].split("#")[0]
            f = dist / path.lstrip("/")
            if path == "/" or not f.is_file():
                f = dist / "index.html"  # the single-page fallback, as vercel.json rewrites
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            if f.suffix in TEXT and "gzip" in self.headers.get("Accept-Encoding", ""):
                body = gzip.compress(body, 9)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def tarpit(ms: int) -> int:
    """A port that accepts a connection, says nothing for `ms`, and drops it: a slow third-party host."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)

    def hold(conn):
        time.sleep(ms / 1000)
        conn.close()

    def loop():
        while True:
            conn, _ = sock.accept()
            threading.Thread(target=hold, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return sock.getsockname()[1]


def routes(ctx, app: str, now: dt.datetime, others: dict, stalled: bool = False) -> None:
    mock = mock_data.Mock("running")

    def handler(route):
        url = route.request.url
        if url.startswith(app):
            return route.continue_()  # the real, throttled link
        if url.startswith(audit.SUPA):
            if "/rest/v1/rpc/" in url:
                fn = url.split("/rest/v1/rpc/")[1].split("?")[0]
                try:
                    args = json.loads(route.request.post_data or "{}")
                except ValueError:
                    args = {}
                body = json.dumps(mock.rpc(fn, args))
            else:
                body = json.dumps(mock_data.session_blob(now))
            return route.fulfill(status=200, content_type="application/json", body=body)
        host = url.split("/")[2]
        others[host] = others.get(host, 0) + 1
        if "finnhub" in host:
            return route.fulfill(status=200, content_type="image/png", body=PIXEL)
        if stalled and host == GOOGLE:
            return route.continue_()  # to the tarpit (the browser was started with the host mapped to it)
        return route.abort()

    ctx.route("**/*", handler)


def one_run(browser, app: str, path: str, signed_in: bool, now: dt.datetime, stalled: bool = False) -> dict:
    ctx = browser.new_context(**PHONE, reduced_motion="no-preference", color_scheme="dark")
    ctx.add_init_script(INIT)
    if signed_in:
        ctx.add_init_script(f"localStorage.setItem('sb-localhost-auth-token', JSON.stringify({json.dumps(mock_data.session_blob(now))}));")
    others: dict = {}
    routes(ctx, app, now, others, stalled)
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    kinds: dict = {}
    sizes: dict = {}
    cdp.on("Network.responseReceived", lambda e: kinds.__setitem__(e["requestId"], (e["type"], e["response"]["url"])))
    cdp.on("Network.loadingFinished", lambda e: sizes.__setitem__(e["requestId"], e["encodedDataLength"]))
    cdp.send("Network.emulateNetworkConditions", NETWORK)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": CPU_SLOWDOWN})
    page.goto(app + path, wait_until="load", timeout=120_000)
    page.wait_for_selector("main > *, .center, form", timeout=120_000)
    page.wait_for_load_state("networkidle", timeout=120_000)
    page.wait_for_timeout(2500)
    m = page.evaluate(READ)
    by_kind: dict = {}
    for rid, (kind, url) in kinds.items():
        if url.startswith(app):
            by_kind[kind] = by_kind.get(kind, 0) + sizes.get(rid, 0)
            by_kind["requests"] = by_kind.get("requests", 0) + 1
    m["bytes"] = by_kind
    m["others"] = others
    ctx.close()
    return m


def shots(browser, app: str, now: dt.datetime, out: pathlib.Path) -> None:
    """Settled, unthrottled screenshots of the home page (motion off, the clock frozen), to compare two builds."""
    out.mkdir(parents=True, exist_ok=True)
    for w, h, phone in [(390, 844, True), (1280, 800, False)]:
        ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=phone, has_touch=phone, device_scale_factor=1,
                                  reduced_motion="reduce", color_scheme="dark")
        ctx.add_init_script(f"localStorage.setItem('sb-localhost-auth-token', JSON.stringify({json.dumps(mock_data.session_blob(now))}));")
        routes(ctx, app, now, {})
        page = ctx.new_page()
        page.clock.install(time=now)
        page.goto(app + "/", wait_until="load")
        page.wait_for_selector("main > *")
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        page.clock.run_for(3000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out / f"home-{w}-fold.png"))
        page.screenshot(path=str(out / f"home-{w}-full.png"), full_page=True)
        ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json")
    ap.add_argument("--shots")
    ap.add_argument("--stall-google", type=int, default=0, metavar="MS")
    a = ap.parse_args()
    dist = pathlib.Path(a.dist).resolve()
    server = static_server(dist)
    app = f"http://127.0.0.1:{server.server_address[1]}"
    # A fixed clock for the mock, so two builds are measured (and photographed) against the same data.
    now = dt.datetime(2026, 11, 17, 19, 30, tzinfo=dt.UTC)
    out: dict = {"profile": {"phone": PHONE, "network": NETWORK, "cpu_slowdown": CPU_SLOWDOWN, "runs": a.runs,
                             "stall_google_ms": a.stall_google}, "pages": {}}
    med = lambda rows, k: statistics.median(r[k] for r in rows if r[k] is not None)  # noqa: E731
    with sync_playwright() as pw:
        stalled = a.stall_google > 0
        # Without the machine's proxy settings, or the browser would ask the proxy instead of the mapped port.
        direct = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        browser = (pw.chromium.launch(args=[f"--host-resolver-rules=MAP {GOOGLE} 127.0.0.1:{tarpit(a.stall_google)}"], env=direct)
                   if stalled else pw.chromium.launch())
        if stalled:
            print(f"Google Fonts holds every connection for {a.stall_google} ms, then drops it.")
        for name, path, signed_in in PAGES:
            rows = [one_run(browser, app, path, signed_in, now, stalled) for _ in range(a.runs)]
            last = rows[-1]
            row = {k: round(med(rows, k), 3 if k == "cls" else 0) for k in ("fcp", "lcp", "tbt", "cls", "dcl", "load")}
            row.update(bytes=last["bytes"], faces=last["faces"], logoTiles=last["logoTiles"], logoTilesInView=last["logoTilesInView"],
                       others=last["others"], lcp_runs=[round(r["lcp"]) for r in rows])
            out["pages"][name] = row
            b = last["bytes"]
            # LCP 0: the browser reported no largest-paint entry for this page (the sign-in card).
            print(f"{name:8} FCP {row['fcp']:.0f} ms  LCP {row['lcp']:.0f} ms  TBT {row['tbt']:.0f} ms  CLS {row['cls']:.3f}  load {row['load']:.0f} ms", flush=True)
            print(f"         over the link: {b.get('requests', 0)} requests; " + ", ".join(f"{k} {v / 1000:.1f} kB" for k, v in sorted(b.items()) if k != "requests"))
            print(f"         font faces used: {', '.join(last['faces']) or 'none'}; logo tiles {last['logoTiles']} ({last['logoTilesInView']} on the first screen); other hosts asked: {last['others']}")
        if a.shots:
            shots(browser, app, now, pathlib.Path(a.shots))
        browser.close()
    server.shutdown()
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
