"""Before/after shots of the My lists cards (stakeholder, 25 Nov 2026). Dev-only.

    cd apps/web && VITE_SUPABASE_URL=http://localhost:54321 VITE_SUPABASE_PUBLISHABLE_KEY=mock npm run build
    python docs/design/screens/lists-icons-2026-11-25/shoot.py --dist apps/web/dist --out <folder> --tag after

Uses the design audit's mock (docs/design/audit_2026-11-16/tools): no production data, account or key. The logo CDNs
cannot be reached from the machine that took these shots, so every company logo is a flat stand-in drawn here (a coloured
tile with a white mark), and APLD's lookup answers "no logo" so its letter fallback shows.
"""
from __future__ import annotations

import argparse, datetime as dt, json, mimetypes, pathlib, sys, zlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "docs" / "design" / "audit_2026-11-16" / "tools"))
import mock_data  # noqa: E402

APP, SUPA = "http://kaibot.test", "http://localhost:54321"
HIGH_BETA = ["NBIS", "CRWV", "FSLY", "IREN", "APLD"]
AI_INFRA = ["PLTR", "CRWV", "NBIS", "VRT", "ANET", "SMCI", "FSLY", "MU"]
COLOURS = ["#4F46E5", "#0EA5E9", "#F97316", "#10B981", "#E11D48", "#8B5CF6", "#14B8A6", "#F59E0B"]


def stand_in(sym: str) -> bytes:
    c = COLOURS[zlib.crc32(sym.encode()) % len(COLOURS)]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="{c}"/>'
            f'<path d="M18 44 L32 16 L46 44 Z" fill="#fff"/></svg>').encode()


class Mock(mock_data.Mock):
    def rpc(self, fn, a):
        if fn == "kb_ticker_lists":
            iso = lambda d: (self.now - dt.timedelta(days=d)).isoformat()
            return {"max_tickers": 40, "max_lists": 20, "min_only": 5, "lists": [
                {"name": "High beta", "updated_at": iso(1), "symbols": HIGH_BETA},  # stored as plain symbols
                {"name": "AI infrastructure", "updated_at": iso(3), "symbols": [{"symbol": s, "status": "accepted", "etf": "XLK"} for s in AI_INFRA]}]}
        return super().rpc(fn, a)


def run(a):
    dist, out = pathlib.Path(a.dist).resolve(), pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)

    def handler(route):
        req, url = route.request, route.request.url
        if url.startswith(SUPA):
            if "/rest/v1/rpc/" in url:
                fn = url.split("/rest/v1/rpc/")[1].split("?")[0]
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(Mock("running").rpc(fn, json.loads(req.post_data or "{}"))))
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(mock_data.session_blob(now)))
        if url.startswith(APP + "/api/logo-domain"):
            sym = url.split("symbol=")[1].split("&")[0]
            if sym == "APLD": return route.fulfill(status=404, body="")
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"domain": sym.lower() + "-example.com"}))
        if url.startswith(APP):
            p = url[len(APP):].split("?")[0]
            f = dist / p.lstrip("/")
            if not (p != "/" and f.is_file()): f = dist / "index.html"
            return route.fulfill(status=200, content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream", body=f.read_bytes())
        if req.resource_type == "image" and "APLD" not in url.upper():
            sym = url.rstrip("/").split("/")[-1].split(".")[0].split("-")[0].upper()
            return route.fulfill(status=200, content_type="image/svg+xml", body=stand_in(sym))
        return route.abort()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for theme in ("dark", "light"):
            for w, h in ((1280, 900), (390, 844)):
                mobile = w <= 430
                ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile, device_scale_factor=2,
                                          reduced_motion="reduce", color_scheme=theme)
                ctx.add_init_script("localStorage.setItem('sb-localhost-auth-token', JSON.stringify(%s));" % json.dumps(mock_data.session_blob(now)))
                ctx.route("**/*", handler)
                page = ctx.new_page()
                page.goto(APP + "/strategy", wait_until="load")
                page.wait_for_selector("main > *", timeout=8000)
                if theme == "light": page.evaluate("document.documentElement.dataset.theme = 'light'")  # defined, not switched on in the app yet
                page.wait_for_timeout(1200)
                page.get_by_role("radio", name="My lists").click()
                page.wait_for_selector(".kb-lcard", timeout=8000)
                page.locator(".kb-lcard").first.click()  # High beta selected, AI infrastructure unselected
                page.wait_for_timeout(1500)
                page.locator(".kb-ml").scroll_into_view_if_needed()
                page.locator(".kb-lgrid").screenshot(path=str(out / f"{a.tag}-{theme}-{w}.png"))
                ctx.close()
        browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True); ap.add_argument("--out", required=True); ap.add_argument("--tag", required=True)
    run(ap.parse_args())
