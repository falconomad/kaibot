"""Mobile check for the v4 PRs (docs/design/mobile_first_v4.md). Dev-only.

At 375, 390 and 430 px (phone emulation) it fails on a page wider than the screen or a tap target under 44 px (inline
text links excepted). It also prints text under 12 px, tables wider than their box and layout shift, and measures
1280 and 1440 px for layout shift. It reuses the design audit's mock and measurements
(docs/design/audit_2026-11-16/tools): every request to the placeholder Supabase URL is answered from the mock, so no
production data, account or key is used.

    cd apps/web && VITE_SUPABASE_URL=http://localhost:54321 VITE_SUPABASE_PUBLISHABLE_KEY=mock npm run build
    python scripts/mobile_check.py --dist dist [--routes home,tape] [--widths 390] [--json out.json] [--interact]

--interact (v4 PR 2) also checks, at 390 px: the top bar is at most 60 px; the tab bar has five 48 px items; every page
is reachable from the tab bar or More; the experiment sheet opens as a dialog with focus inside, closes on Escape and
returns focus; and with reduced motion on, nothing animates.
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
           "tables_wider": sum(t["wider"] for t in m["tables"]), "cls": m["cls"], "header": m["headerH"],
           "words": m["foldWords"], "fold": m["foldText"]}
    if phone and m["docOverflow"]:
        fails.append(f"{name}@{w}: page wider than the screen ({m['docScrollWidth']} px)")
    if phone and m["tapSmall"]:
        fails.append(f"{name}@{w}: {len(m['tapSmall'])} tap targets under 44 px")
    over = "YES" if m["docOverflow"] else "no"
    print(f"{name:13} {w:5}  overflow={over:3}  tap<44={len(m['tapSmall']):3}  text<12={m['smallWords']:4}  "
          f"tables-wider={row['tables_wider']}  cls={m['cls']:.3f}  words={m['foldWords']}", flush=True)
    return row


REACH = ["/", "/positions", "/activity", "/health", "/more", "/report", "/gate", "/holdings", "/strategy", "/settings",
         "/start"]


def interact(browser, dist: pathlib.Path, now: dt.datetime, session: str, fails: list[str]) -> None:
    def page(reduced: bool = False):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                  reduced_motion="reduce" if reduced else "no-preference")
        ctx.add_init_script(session)
        ctx.route("**/*", serve(mock_data.Mock("running"), dist, now))
        pg = ctx.new_page()
        pg.goto(audit.APP + "/", wait_until="load")
        pg.wait_for_selector("main > *")
        pg.wait_for_timeout(1500)
        return ctx, pg

    ctx, pg = page()
    header = pg.evaluate("Math.round(document.querySelector('header.top').getBoundingClientRect().height)")
    tabs = pg.evaluate("[...document.querySelectorAll('nav.tabbar a')].map(a => [a.textContent.trim(), "
                       "Math.round(a.getBoundingClientRect().height)])")
    print("header", header, "px; tabs", tabs)
    if header > 60:
        fails.append(f"top bar is {header} px (want <= 60)")
    if [t[0] for t in tabs] != ["Home", "Positions", "Activity", "Engine", "More"] or any(t[1] < 48 for t in tabs):
        fails.append(f"tab bar: {tabs}")
    links = set(pg.evaluate("[...document.querySelectorAll('nav.tabbar a')].map(a => a.getAttribute('href'))"))
    pg.click("nav.tabbar a[href='/more']")
    pg.wait_for_selector(".more-list")
    links |= set(pg.evaluate("[...document.querySelectorAll('.more-list a')].map(a => a.getAttribute('href'))"))
    missing = [r for r in REACH if r not in links]
    print("reachable on a phone:", sorted(links))
    if missing:
        fails.append(f"not reachable on a phone: {missing}")
    pg.click("nav.tabbar a[href='/']")
    pg.wait_for_timeout(800)
    btn = pg.locator(".status-row.info, .exp-btn").first
    if btn.count():
        btn.click()
        pg.wait_for_selector("[role=dialog]")
        pg.wait_for_timeout(400)
        inside = pg.evaluate("!!document.activeElement.closest('[role=dialog]')")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        gone = pg.locator("[role=dialog]").count() == 0
        back = pg.evaluate("!!document.activeElement && document.activeElement.matches('.status-row, .exp-btn')")
        print("sheet: focus inside", inside, "closed on Escape", gone, "focus returned", back)
        if not (inside and gone and back):
            fails.append(f"sheet: focus inside={inside} closed={gone} focus back={back}")
    else:
        fails.append("no experiment row on Home to open the sheet")
    ctx.close()
    ctx, pg = page(reduced=True)
    running = pg.evaluate("document.getAnimations().filter(a => a.playState === 'running').length")
    print("reduced motion: running animations", running)
    if running:
        fails.append(f"{running} animations run with reduced motion on")
    ctx.close()


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
        if a.interact:
            interact(browser, dist, now, session, fails)
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
    ap.add_argument("--interact", action="store_true")
    sys.exit(main(ap.parse_args()))
