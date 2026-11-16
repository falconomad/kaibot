"""Mobile check for the v4 PRs (docs/design/mobile_first_v4.md). Dev-only.

At 375, 390 and 430 px (phone emulation) it fails on a page wider than the screen or a tap target under 44 px (inline
text links excepted). It also prints text under 12 px, tables wider than their box and layout shift, and measures
1280 and 1440 px for layout shift. It reuses the design audit's mock and measurements
(docs/design/audit_2026-11-16/tools): every request to the placeholder Supabase URL is answered from the mock, so no
production data, account or key is used.

    cd apps/web && VITE_SUPABASE_URL=http://localhost:54321 VITE_SUPABASE_PUBLISHABLE_KEY=mock npm run build
    python scripts/mobile_check.py --dist dist [--routes home,tape] [--widths 390] [--json out.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[3]
TOOLS = ROOT / "docs" / "design" / "audit_2026-11-16" / "tools"
sys.path.insert(0, str(TOOLS))
import audit  # noqa: E402  (routes, init script, app and mock URLs)
import mock_data  # noqa: E402

PHONES = [(375, 812), (390, 844), (430, 932)]
DESKTOP = [(1280, 800), (1440, 900)]


def serve(mock, dist: pathlib.Path, now: dt.datetime):
    def handler(route):
        url = route.request.url
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
        if url.startswith(audit.APP):
            p = url[len(audit.APP):].split("?")[0].split("#")[0]
            f = dist / p.lstrip("/")
            if not (p != "/" and f.is_file()):
                f = dist / "index.html"
            kind = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            return route.fulfill(status=200, content_type=kind, body=f.read_bytes())
        return route.abort()
    return handler


def check(page, name: str, w: int, metrics: str, fails: list[str]) -> dict:
    m = page.evaluate(metrics)
    phone = w <= 430
    row = {"page": name, "w": w, "overflow": m["docOverflow"], "offscreen": m["offscreen"][:3],
           "tap<44": len(m["tapSmall"]), "taps": m["tapSmall"], "text<12": m["smallWords"],
           "small_font": m["smallFont"],
           "tables_wider": sum(t["wider"] for t in m["tables"]), "cls": m["cls"], "header": m["headerH"]}
    if phone and m["docOverflow"]:
        fails.append(f"{name}@{w}: page wider than the screen ({m['docScrollWidth']} px)")
    if phone and m["tapSmall"]:
        fails.append(f"{name}@{w}: {len(m['tapSmall'])} tap targets under 44 px")
    over = "YES" if m["docOverflow"] else "no"
    print(f"{name:13} {w:5}  overflow={over:3}  tap<44={len(m['tapSmall']):3}  text<12={m['smallWords']:4}  "
          f"tables-wider={row['tables_wider']}  cls={m['cls']:.3f}", flush=True)
    return row


def main(a) -> int:
    dist = pathlib.Path(a.dist).resolve()
    metrics = (TOOLS / "metrics.js").read_text()
    now = dt.datetime.now(dt.UTC)
    want = set(a.routes.split(",")) if a.routes else None
    routes = [r for r in audit.ROUTES if want is None or r[0] in want]
    sizes = [s for s in PHONES + DESKTOP if not a.widths or str(s[0]) in a.widths.split(",")]
    blob = json.dumps(mock_data.session_blob(now))
    session = "localStorage.setItem('sb-localhost-auth-token', JSON.stringify(" + blob + "));"
    fails: list[str] = []
    rows: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for w, h in sizes:
            for name, path, act in routes:
                phone = w <= 430
                ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=phone, has_touch=phone,
                                          color_scheme="dark")
                ctx.add_init_script(audit.INIT)
                if act != "signed-out":
                    ctx.add_init_script(session)
                ctx.route("**/*", serve(mock_data.Mock("running"), dist, now))
                page = ctx.new_page()
                page.goto(audit.APP + path, wait_until="load")
                try:
                    page.wait_for_selector("main > *, .center", timeout=8000)
                except Exception:
                    fails.append(f"{name}@{w}: no content")
                page.wait_for_timeout(1500)
                if act == "lists":
                    page.get_by_role("radio", name="My lists").click()
                    page.wait_for_timeout(700)
                rows.append(check(page, name, w, metrics, fails))
                ctx.close()
        browser.close()
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=1))
    print("\n".join("FAIL " + f for f in fails) or "PASS: no overflow, no tap target under 44 px at 375/390/430")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True)
    ap.add_argument("--routes")
    ap.add_argument("--widths")
    ap.add_argument("--json")
    sys.exit(main(ap.parse_args()))
