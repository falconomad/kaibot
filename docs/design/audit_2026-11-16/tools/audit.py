"""Design audit runner (#226). Dev-only: renders the built dashboard against mock_data.py and measures every route.

    cd apps/web && npm ci && VITE_SUPABASE_URL=http://localhost:54321 VITE_SUPABASE_PUBLISHABLE_KEY=mock npm run build
    python docs/design/audit_2026-11-16/tools/audit.py --dist apps/web/dist --axe /path/to/axe.min.js --out /tmp/kb-audit

The Supabase URL above is a placeholder that nothing listens on: every request to it is answered here from mock_data.
No production data, account or key is used. One browser at a time (the machine is small).
"""
from __future__ import annotations

import argparse, datetime as dt, json, mimetypes, pathlib, sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mock_data  # noqa: E402

APP = "http://kaibot.test"
SUPA = "http://localhost:54321"
VIEWPORTS = [(375, 812), (390, 844), (430, 932), (768, 1024), (1280, 800), (1440, 900)]
# name, path, optional action
ROUTES = [("home", "/", None), ("trades", "/trades", None), ("tape", "/tape", None), ("trade-detail", "/trades/137", None),
          ("report", "/report", None), ("engine", "/health", None), ("gate", "/gate", None), ("settings", "/settings", None),
          ("configure", "/strategy", None), ("my-lists", "/strategy", "lists"), ("fine-tune", "/strategy/fine-tune", None),
          ("new-run", "/start", None), ("how", "/how", None), ("sign-in", "/", "signed-out")]
EMPTY = ["home", "trades", "tape", "report", "engine", "gate"]
INIT = """
window.__cls = 0; window.__shifts = 0;
try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) { window.__cls += e.value; window.__shifts++; } })
  .observe({ type: 'layout-shift', buffered: true }); } catch (e) {}
"""
FOCUS_JS = """
() => { const el = document.activeElement; if (!el || el === document.body) return null; const s = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  return { sel: el.tagName.toLowerCase() + '.' + (el.getAttribute('class') || '').split(' ')[0], text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 30),
    ring: (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || s.boxShadow !== 'none', visible: r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight }; }
"""


def run(a):
    dist, out = pathlib.Path(a.dist).resolve(), pathlib.Path(a.out)
    (out / "shots").mkdir(parents=True, exist_ok=True)
    axe, metrics = pathlib.Path(a.axe).read_text(), (HERE / "metrics.js").read_text()
    now = dt.datetime.now(dt.timezone.utc)
    results, errors = [], []

    def serve(mock):
        def handler(route):
            req = route.request
            url = req.url
            if url.startswith(SUPA):
                if "/rest/v1/rpc/" in url:
                    fn = url.split("/rest/v1/rpc/")[1].split("?")[0]
                    try: args = json.loads(req.post_data or "{}")
                    except Exception: args = {}
                    return route.fulfill(status=200, content_type="application/json", body=json.dumps(mock.rpc(fn, args)))
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(mock_data.session_blob(now)))
            if url.startswith(APP):
                p = url[len(APP):].split("?")[0].split("#")[0]
                f = dist / p.lstrip("/")
                if not (p != "/" and f.is_file()): f = dist / "index.html"
                return route.fulfill(status=200, content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream", body=f.read_bytes())
            if "fonts.g" in url: return route.continue_()
            return route.abort()
        return handler

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        jobs = [(n, p, act, "running") for n, p, act in ROUTES] + [(n, p, act, "empty") for n, p, act in ROUTES if n in EMPTY]
        for w, h in VIEWPORTS:
            for name, path, act, scenario in jobs:
                if scenario == "empty" and w not in (390, 1440): continue
                mobile = w <= 430
                ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile, device_scale_factor=1,
                                          reduced_motion="no-preference", color_scheme="dark")
                ctx.add_init_script(INIT)
                if act != "signed-out":
                    ctx.add_init_script("localStorage.setItem('sb-localhost-auth-token', JSON.stringify(%s));" % json.dumps(mock_data.session_blob(now)))
                ctx.route("**/*", serve(mock_data.Mock(scenario)))  # fresh clock per page, so the engine heartbeat stays live
                page = ctx.new_page()
                tag = f"{name}{'-empty' if scenario == 'empty' else ''}-{w}x{h}"
                page.on("pageerror", lambda e, tag=tag: errors.append({"page": tag, "error": str(e)[:300]}))
                page.on("console", lambda m, tag=tag: errors.append({"page": tag, "console": m.text[:300]}) if m.type == "error" and "ERR_FAILED" not in m.text and "fonts.googleapis" not in m.text else None)
                page.goto(APP + path, wait_until="load")
                try: page.wait_for_selector("main > *, .center", timeout=8000)
                except Exception: errors.append({"page": tag, "error": "no main content"})
                page.wait_for_timeout(1800)
                if act == "lists":
                    try: page.get_by_role("radio", name="My lists").click(); page.wait_for_timeout(900)
                    except Exception as e: errors.append({"page": tag, "error": f"lists: {e}"[:200]})
                m = page.evaluate(metrics)
                page.evaluate(axe)
                ax = page.evaluate("async () => { const r = await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] });"
                                   " return r.violations.map(v => ({ id: v.id, impact: v.impact, n: v.nodes.length, help: v.help,"
                                   " nodes: v.nodes.slice(0, 6).map(n => ({ target: n.target.join(' '), html: n.html.slice(0, 120),"
                                   " data: n.any[0] && n.any[0].data ? { fg: n.any[0].data.fgColor, bg: n.any[0].data.bgColor, ratio: n.any[0].data.contrastRatio, size: n.any[0].data.fontSize } : null })) })); }")
                page.screenshot(path=str(out / "shots" / f"{tag}-fold.png"))
                page.screenshot(path=str(out / "shots" / f"{tag}-full.png"), full_page=True)
                focus = None
                if scenario == "running" and w in (390, 1440):
                    seen, noring = [], []
                    for _ in range(45):
                        page.keyboard.press("Tab")
                        f = page.evaluate(FOCUS_JS)
                        if not f: continue
                        seen.append(f)
                        if f["visible"] and not f["ring"]: noring.append(f)
                    focus = {"tabbed": len(seen), "noRing": noring[:12], "noRingCount": len(noring)}
                results.append({"page": name, "scenario": scenario, "w": w, "h": h, "tag": tag, "metrics": m, "axe": ax, "focus": focus})
                print(tag, "overflow" if m["docOverflow"] else "", "words", m["foldWords"], "tap<44", len(m["tapSmall"]), flush=True)
                ctx.close()
        # reduced motion: does anything still animate?
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, reduced_motion="reduce")
        ctx.add_init_script("localStorage.setItem('sb-localhost-auth-token', JSON.stringify(%s));" % json.dumps(mock_data.session_blob(now)))
        ctx.route("**/*", serve(mock_data.Mock("running")))
        page = ctx.new_page(); page.goto(APP + "/"); page.wait_for_selector("main > *"); page.wait_for_timeout(300)
        rm = page.evaluate("() => ({ running: document.getAnimations().filter(a => a.playState === 'running').length })")
        ctx.close(); browser.close()
    (out / "results.json").write_text(json.dumps({"generated": now.isoformat(), "results": results, "errors": errors, "reducedMotion": rm}, indent=1))
    print("errors:", len(errors), "reduced-motion running animations:", rm)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True); ap.add_argument("--axe", required=True); ap.add_argument("--out", default="/tmp/kb-audit")
    run(ap.parse_args())
