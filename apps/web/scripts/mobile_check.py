"""Mobile check for the v4 PRs (docs/design/mobile_first_v4.md). Dev-only.

At 375, 390 and 430 px (phone emulation) it fails on a page wider than the screen or a tap target under 44 px (inline
text links excepted). It also prints text under 12 px, tables wider than their box and layout shift, and measures
1280 and 1440 px for layout shift. It reuses the design audit's mock and measurements
(docs/design/audit_2026-11-16/tools): every request to the placeholder Supabase URL is answered from the mock, so no
production data, account or key is used.

    cd apps/web && VITE_SUPABASE_URL=http://localhost:54321 VITE_SUPABASE_PUBLISHABLE_KEY=mock npm run build
    python scripts/mobile_check.py --dist dist [--routes home,tape] [--widths 390] [--json out.json] [--interact]

--interact (v4 PR 2) also checks, at 390 px: the top bar is 64 px (72 px at 1280); the tab bar has five 48 px items; every page
is reachable from the tab bar or More; the experiment sheet opens as a dialog with focus inside, closes on Escape and
returns focus; and with reduced motion on, nothing animates.

Text contrast (#265): axe-core's color-contrast rule runs on every page at every width; a failure fails the check.

The display name (#254): every page at every width fails if it shows the name wrongly in its text, its tab title or in
what a screen reader says. Wrong is the earlier name ("kaibot"), or today's name with a capital, written so or put in
capitals by CSS: it is always lowercase, as the first word of a sentence too (stakeholder, 16 Nov 2026). Text inside
<code> is a literal path or command and is not counted. --interact also checks the tab title, the logo in the header,
and that the favicon, the home-screen icon and the manifest load.

The clock: the check never reads the wall clock. Every page's clock, and the mock behind it, reads OPEN (a Thursday
at 12:40 in New York) or CLOSED (17:30 the same day), so a run gives the same answer at any hour and on any day.

--stress also puts the daily note (#311) at its worst on Home after the close: a long note, a long template fallback
with its reason, and text with nowhere to break (a link, a ticker list), on the card and in its sheet.

--stress also measures the live state (stakeholder, 19 Nov 2026), with the market open: /api/marks is answered from the
stress mock, so Home and Positions mark the crowded book at live prices. The freshness line ("Live · prices 4 s ago"),
six-digit values and gains, each row's change today and the position sheet must fit at 375, 390 and 430 px.
"""
# ruff: noqa: E501  (the browser scripts in this file are JavaScript inside strings; a few lines run past 120)
from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"
TOOLS = ROOT / "docs" / "design" / "audit_2026-11-16" / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import audit  # noqa: E402  (routes, init script, app and mock URLs)
import mock_data  # noqa: E402

PHONES = [(375, 812), (390, 844), (430, 932)]
DESKTOP = [(1280, 800), (1440, 900)]

# The check's clock. No result may depend on when the check runs: Home shows the daily note only outside market hours
# and the engine's line changes at the open and the close, so a run at 14:00 New York failed where the same code passed
# at 03:00. Every page's clock reads one of these two instants, and the mock is built at the same instant.
OPEN = dt.datetime(2026, 11, 19, 17, 40, tzinfo=dt.UTC)    # a Thursday, 12:40 in New York: the market is open
CLOSED = dt.datetime(2026, 11, 19, 22, 30, tzinfo=dt.UTC)  # the same day, 17:30 in New York: after the close


def context(browser, at: dt.datetime, **kw):
    """A browser context whose clock reads `at` and stays there (timers and animations keep running)."""
    kw.setdefault("service_workers", "block")  # #481: these checks read the pages, not the app shell's store
    ctx = browser.new_context(**kw)
    ctx.clock.set_fixed_time(at)
    return ctx


def signed_in(at: dt.datetime) -> str:
    """The init script of a signed-in session for the mock project, issued at `at`."""
    return "localStorage.setItem('sb-localhost-auth-token', JSON.stringify(" + json.dumps(mock_data.session_blob(at)) + "));"


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
            marks = getattr(mock, "marks", None)
            if p == "/api/marks" and marks and marks():
                # the live marks function (api/marks.ts), answered from the mock; any other mock serves the page
                # below instead, as a deployment without the function does, and the app falls back
                return route.fulfill(status=200, content_type="application/json", headers={"cache-control": "no-store"}, body=json.dumps(marks()))
            f = dist / p.lstrip("/")
            if not (p != "/" and f.is_file()):
                f = dist / "index.html"
            kind = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            return route.fulfill(status=200, content_type=kind, body=f.read_bytes())
        return route.abort()
    return handler


def brand_name() -> str:
    """The display name, read from the one place it is written (src/brand/brand.ts)."""
    source = (WEB / "src" / "brand" / "brand.ts").read_text()
    return re.search(r'^export const BRAND = "([^"]+)";$', source, re.M).group(1)


# Where the app's name is shown wrongly: the earlier name ("kaibot", in any case), today's name with a capital, or
# today's name put in capitals by CSS (text-transform). Read: the tab title, the page's text (hidden text included,
# <code> left out) and the attributes a person reads or hears. Text nodes are joined with a space, so a word never runs
# into its neighbour's. KAIBOT_SOMETHING in an engine message is an env var, not the name.
WRONG_NAME = r"""(brand) => {
  const literal = (n) => n.parentElement.closest('code, script, style');
  const skip = (n) => (literal(n) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT);
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: skip });
  const anyCase = new RegExp(`\\b(?:kaibot|${brand})\\b`, 'gi');
  const around = (s, m) => s.slice(Math.max(0, m.index - 24), m.index + m[0].length + 24).trim();
  const wrong = (s) => [...s.matchAll(anyCase)].filter((m) => m[0] !== brand).map((m) => around(s, m));
  const words = [];
  const byCss = [];
  while (walk.nextNode()) {
    const node = walk.currentNode;
    words.push(node.nodeValue);
    const shape = getComputedStyle(node.parentElement).textTransform;
    if (shape !== 'none' && new RegExp(`\\b${brand}\\b`, 'i').test(node.nodeValue))
      byCss.push(`${node.nodeValue.trim().slice(0, 40)} (CSS text-transform: ${shape})`);
  }
  const said = [...document.querySelectorAll('[aria-label], [alt], [title], [placeholder]')]
    .flatMap((e) => ['aria-label', 'alt', 'title', 'placeholder'].map((a) => e.getAttribute(a) || ''));
  return [document.title, words.join(' ').replace(/\s+/g, ' '), ...said].flatMap(wrong).concat(byCss);
}"""


GLOW_LAYER = """() => { const st = getComputedStyle(document.body, '::before');
  return { position: st.position, z: st.zIndex, image: st.backgroundImage.slice(0, 4), transform: st.transform }; }"""
# axe-core (dev dependency) checks text contrast on every page at every width: WCAG 2 AA, the color-contrast rule.
AXE = (WEB / "node_modules" / "axe-core" / "axe.min.js").read_text()
AXE_RUN = """async () => {
  const r = await axe.run(document, { runOnly: { type: 'rule', values: ['color-contrast'] }, resultTypes: ['violations'] });
  return r.violations.flatMap((v) => v.nodes.map((n) => `${n.target.join(' ')}: ${(n.any[0] || {}).message || v.help}`));
}"""


def check(page, name: str, w: int, metrics: str, fails: list[str]) -> dict:
    m = page.evaluate(metrics)
    layer = page.evaluate(GLOW_LAYER)
    if layer["position"] != "fixed" or not layer["image"].startswith("url(") or layer["z"] != "-1":
        fails.append(f"{name}@{w}: the page glow layer is missing or not fixed: {layer}")
    page.evaluate(AXE)
    low = page.evaluate(AXE_RUN)
    if low:
        fails.append(f"{name}@{w}: {len(low)} text contrast failures (axe): {low[:2]}")
    wrong = page.evaluate(WRONG_NAME, brand_name())
    if wrong:
        fails.append(f"{name}@{w}: the name is shown wrongly (always lowercase {brand_name()!r}): {wrong[:3]}")
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
    print(f"{name:13} {w:5}  overflow={over:3}  tap<44={len(m['tapSmall']):3}  text<12={m['smallWords']:4}  contrast={len(low)}  "
          f"tables-wider={row['tables_wider']}  cls={m['cls']:.3f}  words={m['foldWords']}", flush=True)
    return row


REACH = ["/", "/positions", "/activity", "/health", "/more", "/report", "/gate", "/strategy", "/settings", "/start"]

# The name and the logo as the browser has them (#254): the tab title, the header, the manifest, and every icon the page
# and the manifest point to (each must load as an image of the size it claims).
BRAND_JS = r"""async () => {
  const img = (src) => new Promise((done) => {
    const i = new Image();
    i.onload = () => done([i.naturalWidth, i.naturalHeight]);
    i.onerror = () => done(null);
    i.src = src;
  });
  const href = (sel) => document.querySelector(sel)?.getAttribute('href') || null;
  const m = await fetch(href('link[rel=manifest]')).then((r) => r.json()).catch(() => null);
  const icons = {};
  for (const sel of ['link[rel=icon][type="image/svg+xml"]', 'link[rel=apple-touch-icon]'])
    icons[href(sel)] = !!(await img(href(sel)));
  for (const i of (m?.icons || [])) { const s = await img(i.src); icons[i.src] = !!s && `${s[0]}x${s[1]}` === i.sizes; }
  const symbol = document.querySelector('header.top .brand svg.logo-word');
  return {
    title: document.title,
    label: document.querySelector('header.top .brand')?.getAttribute('aria-label') || null,
    word: symbol?.getAttribute('aria-label') || null,
    symbol: !!symbol && symbol.querySelectorAll('path').length === 10 && !document.querySelector('header.top .brand img'),
    logos: document.querySelectorAll('.logo').length,
    homeLabel: document.querySelector('meta[name=apple-mobile-web-app-title]')?.getAttribute('content') || null,
    manifest: m ? [m.name, m.short_name, m.display] : null,
    icons,
    ico: href('link[rel=icon][sizes]'),
  };
}"""


# The header's parts on a phone: sizes as drawn, tap areas, one row, and the logo's box against its drawn letters.
HEADER_JS = r"""() => {
  const h = (sel) => document.querySelector(sel).getBoundingClientRect();
  const logo = document.querySelector('header.top .logo');
  const drawn = logo.querySelector('g').getBoundingClientRect(), box = logo.getBoundingClientRect();
  const tops = [...document.querySelector('header.top').children].filter((e) => e.getBoundingClientRect().height > 0)
    .map((e) => Math.round(e.getBoundingClientRect().top + e.getBoundingClientRect().height / 2));
  return {
    logo: Math.round(box.height * 10) / 10,
    pill: Math.round(h('header.top .pill').height),
    chip: Math.round(h('header.top .engine-chip').height - 2 * parseFloat(getComputedStyle(document.querySelector('header.top .engine-chip'), '::before').top)),
    chipTap: Math.round(h('header.top .engine-chip').height),
    homeTap: Math.round(h('header.top .brand').height),
    rows: new Set(tops).size,
    hug: Math.round((Math.abs(drawn.width - box.width) + Math.abs(drawn.height - box.height)) * 10) / 10,
  };
}"""

# The logo's motion (docs/design/logo): which of its animations are running now.
LOGO_MOTION = "document.getAnimations().filter(a => /^kb-logo-/.test(a.animationName) && a.playState === 'running').map(a => a.animationName)"


def interact(browser, dist: pathlib.Path, now: dt.datetime, session: str, fails: list[str]) -> None:
    def page(reduced: bool = False):
        ctx = context(browser, now, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                      reduced_motion="reduce" if reduced else "no-preference")
        ctx.add_init_script(session)
        ctx.route("**/*", serve(mock_data.Mock("running", now), dist, now))
        pg = ctx.new_page()
        pg.goto(audit.APP + "/", wait_until="load")
        pg.wait_for_selector("main > *")
        early = sorted(set(pg.evaluate(LOGO_MOTION)))
        pg.wait_for_timeout(1500)
        return ctx, pg, early

    ctx, pg, early = page()
    # The logo's motion: plays once as the header first shows, is over within a second and a half, and never again:
    # not on a tab change, and not when the header is drawn anew after New run.
    later = pg.evaluate(LOGO_MOTION)
    home_at = pg.evaluate("getComputedStyle(document.body, '::before').transform")
    pg.click("nav.tabbar a[href='/positions']")
    pg.wait_for_timeout(120)
    on_tab = pg.evaluate(LOGO_MOTION)
    drift = pg.evaluate("document.getAnimations().filter(a => a.transitionProperty === 'transform' && a.effect?.pseudoElement === '::before')"
                        ".map(a => Math.round(a.effect.getComputedTiming().duration))")
    pg.wait_for_timeout(700)
    tab_at = pg.evaluate("getComputedStyle(document.body, '::before').transform")
    print("glow drift on a tab change:", drift, "ms | moved:", home_at != tab_at, "| move:", pg.evaluate("document.body.dataset.glowMove"))
    if drift != [600] or home_at == tab_at:
        fails.append(f"the glow did not drift once in 600 ms on a tab change: {drift}, moved={home_at != tab_at}")
    pg.evaluate("history.pushState({}, '', '/start'); dispatchEvent(new PopStateEvent('popstate'))")
    pg.wait_for_selector(".steps")
    on_start = pg.evaluate(LOGO_MOTION)
    pg.evaluate("history.pushState({}, '', '/'); dispatchEvent(new PopStateEvent('popstate'))")
    pg.wait_for_selector("nav.tabbar")
    pg.wait_for_timeout(120)
    on_back = pg.evaluate(LOGO_MOTION)
    print("logo motion: at first paint", early, "| after 1.5 s", later, "| tab change", on_tab, "| New run", on_start, "| back", on_back)
    if early != ["kb-logo-draw", "kb-logo-pop", "kb-logo-pulse"]:
        fails.append(f"the logo's load motion did not play at first paint: {early}")
    if later or on_tab or on_start or on_back:
        fails.append(f"the logo moved again: after 1.5 s {later}, tab change {on_tab}, New run {on_start}, back {on_back}")
    still = pg.evaluate("document.querySelectorAll('.logo-moving, .logo clipPath, .logo-pulse').length")
    if still:
        fails.append(f"the logo keeps {still} motion parts after it has finished")
    pg.wait_for_timeout(600)
    # The page glow (stakeholder, 16 Nov 2026): one layer fixed to the viewport, so a scroll moves content over it.
    # The sanity check: the layer is fixed and behind everything, and after a scroll the same pixel in the bottom-left
    # corner still shows the glow at the same strength (it did not scroll away or get repainted elsewhere).
    glow = pg.evaluate("""() => { const st = getComputedStyle(document.body, '::before');
      return { position: st.position, z: st.zIndex, image: st.backgroundImage.includes('radial-gradient') ? 'radial-gradient' : st.backgroundImage.slice(0, 15), att: getComputedStyle(document.body).backgroundAttachment }; }""")
    corner = lambda: pg.screenshot(clip={"x": 2, "y": 520, "width": 1, "height": 1})  # noqa: E731
    before = corner()
    pg.mouse.wheel(0, 600)
    pg.wait_for_timeout(300)
    scrolled = pg.evaluate("scrollY")
    print("glow layer:", glow, "| scrolled", scrolled, "px, corner pixel unchanged:", corner() == before)
    if glow["position"] != "fixed" or glow["z"] != "-1" or not glow["image"].startswith("radial-gradient") or glow["att"] == "fixed":
        fails.append(f"the page glow is not one fixed layer behind the page: {glow}")
    pg.mouse.wheel(0, -600)
    # The name is written once, in src/brand/brand.ts; the built app must show that name and no other.
    name = brand_name()
    brand = pg.evaluate(BRAND_JS)
    # Playwright never lets a favicon.ico request through, so that one file is read from the build instead.
    ico = dist / str(brand["ico"]).split("?")[0].lstrip("/")
    brand["icons"][brand["ico"]] = ico.is_file() and ico.read_bytes()[:4] == b"\x00\x00\x01\x00"
    print("name:", brand)
    if (brand["title"], brand["label"], brand["word"]) != (f"{name} · paper", f"{name} home", name):
        fails.append(f"the name: tab title {brand['title']!r}, header link {brand['label']!r}, word {brand['word']!r}")
    if not brand["symbol"] or brand["logos"] != 1:
        fails.append(f"the header logo: symbol drawn={brand['symbol']}, logos={brand['logos']}")
    if brand["manifest"] != [name, name, "browser"] or brand["homeLabel"] != name:
        fails.append(f"the manifest or home-screen label: {brand['manifest']}, {brand['homeLabel']!r}")
    if len(brand["icons"]) < 6 or not all(brand["icons"].values()):
        fails.append(f"an icon is missing or does not load at its size: {brand['icons']}")
    header = pg.evaluate("Math.round(document.querySelector('header.top').getBoundingClientRect().height)")
    tabs = pg.evaluate("[...document.querySelectorAll('nav.tabbar a')].map(a => [a.textContent.trim(), "
                       "Math.round(a.getBoundingClientRect().height)])")
    print("header", header, "px; tabs", tabs)
    if header != 64:
        fails.append(f"top bar is {header} px on a phone (want 64)")
    hdr = pg.evaluate(HEADER_JS)
    print("header parts:", hdr)
    if not 29.5 <= hdr["logo"] <= 30.5 or hdr["pill"] != 36 or hdr["chip"] != 36 or hdr["chipTap"] < 44 or hdr["homeTap"] < 44:
        fails.append(f"header parts on a phone (logo 30, Paper 36, chip 36 drawn and 44 to tap, home link 44): {hdr}")
    if hdr["rows"] != 1 or abs(hdr["hug"]) > 0.5:
        fails.append(f"header: one row, and the logo's box must hug its letters: {hdr}")
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
    btn = pg.locator(".status-row.exp, .status-row.info, .exp-btn").first
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
    # Landing from the emailed sign-in link (/?from=signin, then signed in): the glow starts at sign-in's place and
    # drifts once to Home's, in 900 ms, and the marker leaves the address bar.
    ctx = context(browser, now, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.add_init_script(session)
    ctx.route("**/*", serve(mock_data.Mock("running", now), dist, now))
    pg = ctx.new_page()
    pg.goto(audit.APP + "/?from=signin", wait_until="load")
    start_at = pg.evaluate("[document.body.dataset.glow, getComputedStyle(document.body, '::before').transform]")
    pg.wait_for_selector("nav.tabbar")
    pg.wait_for_timeout(60)
    landing = pg.evaluate("document.getAnimations().filter(a => a.transitionProperty === 'transform' && a.effect?.pseudoElement === '::before')"
                          ".map(a => Math.round(a.effect.getComputedTiming().duration))")
    pg.wait_for_timeout(1100)
    end_at = pg.evaluate("[document.body.dataset.glow, getComputedStyle(document.body, '::before').transform, location.search]")
    print("sign-in landing: start", start_at, "| drift", landing, "ms | end", end_at)
    if start_at[0] != "signin" or landing != [900] or end_at[0] != "home" or end_at[1] == start_at[1] or "from=" in end_at[2]:
        fails.append(f"the glow did not drift once from sign-in to Home on landing: start {start_at}, drift {landing}, end {end_at}")
    ctx.close()
    ctx, pg, early = page(reduced=True)
    running = pg.evaluate("document.getAnimations().filter(a => a.playState === 'running').length")
    paths = pg.evaluate("document.querySelectorAll('header.top .logo path').length")
    print("reduced motion: running animations", running, "| logo motion at first paint", early, "| logo paths", paths)
    if running or early or paths != 10:
        fails.append(f"reduced motion: {running} animations run, the logo moved {early}, or its mark is not whole ({paths} paths)")
    ctx.close()
    # 1280 px: the header is 72 px, the wordmark 32 px.
    ctx = context(browser, now, viewport={"width": 1280, "height": 800})
    ctx.add_init_script(session)
    ctx.route("**/*", serve(mock_data.Mock("running", now), dist, now))
    pg = ctx.new_page()
    pg.goto(audit.APP + "/", wait_until="load")
    pg.wait_for_selector("main > *")
    pg.wait_for_timeout(1200)
    wide = pg.evaluate("[Math.round(document.querySelector('header.top').getBoundingClientRect().height), "
                       "Math.round(document.querySelector('header.top .logo').getBoundingClientRect().height)]")
    print("1280 px: header", wide[0], "px, wordmark", wide[1], "px")
    if wide != [72, 32]:
        fails.append(f"at 1280 px the header is {wide[0]} px and the wordmark {wide[1]} px (want 72 and 32)")
    ctx.close()


# Rows whose parts overlap or stick out: the name and the number of a row must sit side by side, inside the row.
MISALIGN = r"""() => {
  const out = [];
  for (const row of document.querySelectorAll('.row, .hold-row, .kv')) {
    const r = row.getBoundingClientRect();
    if (!r.width) continue;
    // screen-reader-only text is a 1 px box by design: it never overlaps anything a person sees
    const kids = [...row.children].filter((k) => k.getBoundingClientRect().width > 0 && !k.matches('.sr-only'));
    for (const k of kids) { const b = k.getBoundingClientRect(); if (b.right > r.right + 1 || b.left < r.left - 1) out.push(`${row.className}: ${k.className || k.tagName} sticks out`); }
    if (row.matches('.row, .hold-row')) for (let i = 1; i < kids.length; i++) {
      const x = kids[i - 1].getBoundingClientRect(), y = kids[i].getBoundingClientRect();
      if (y.top < x.bottom - 1 && y.left < x.right - 1) out.push(`${row.className}: ${kids[i - 1].className} and ${kids[i].className} overlap (${(row.textContent || '').trim().slice(0, 30)})`);
    }
  }
  return [...new Set(out)].slice(0, 6);
}"""


# The daily note's card on Home (#311), measured: its text's lines, whether it is cut, whether anything in it is wider
# than its box (a word with nowhere to break is clipped, not wrapped), where the button sits and how tall it is.
NOTE_JS = """() => { const c = document.querySelector('.note-card'); if (!c) return null;
  const b = c.querySelector('.note-btn'), t = c.querySelector('.note-text'), r = b.getBoundingClientRect();
  const wide = (e) => e.scrollWidth > e.clientWidth + 1;
  return { lines: Math.round(t.getBoundingClientRect().height / parseFloat(getComputedStyle(t).lineHeight)), cut: t.scrollHeight > t.clientHeight + 1,
    over: wide(c) || wide(b) || wide(t) || wide(c.querySelector('.note-head')), left: Math.round(r.left), right: Math.round(r.right),
    tap: Math.round(r.height), w: innerWidth, head: c.querySelector('.note-head b').textContent }; }"""
# The note's sheet: nothing in it wider than the sheet or past the screen's edge, and the whole paragraph in it.
NOTE_SHEET_JS = """() => { const d = document.querySelector('[role=dialog]'), body = d.querySelector('.sheet-body'), r = d.getBoundingClientRect();
  const out = [...body.querySelectorAll('*')].filter((e) => { const x = e.getBoundingClientRect(); return x.right > innerWidth + 1 || x.left < -1; }).length;
  return { over: d.scrollWidth > d.clientWidth + 1 || body.scrollWidth > body.clientWidth + 1, out, right: Math.round(r.right), w: innerWidth,
    full: (d.querySelector('.note-full')?.textContent || '').length, text: d.textContent }; }"""


def note_bad(note: dict | None) -> bool:
    """The card is missing, wider than its box, off the screen, under 44 px to tap, or its text runs past three lines."""
    return (not note or note["over"] or note["lines"] > 3 or note["left"] < 0 or note["right"] > note["w"] or note["tap"] < 44)


def stress_notes(browser, dist: pathlib.Path, metrics: str, fails: list[str]) -> None:
    """The daily note at its worst (stress_mock.StressMock.notes), after the close, at 375, 390 and 430 px: a long note,
    a long template fallback with its reason, and a note with a link and a ticker list that have nowhere to break.
    Each: the card stays three lines, inside the screen and 44 px to tap; its sheet shows the whole paragraph and the
    reason, and nothing in it is wider than the screen. And once, with the market open: no card (Home's rule)."""
    import stress_mock
    notes = stress_mock.StressMock.notes(stress_mock.StressMock("running", CLOSED).today)
    for w, h in PHONES:
        for name, n in notes.items():
            ctx = context(browser, CLOSED, viewport={"width": w, "height": h}, is_mobile=True, has_touch=True)
            ctx.add_init_script(audit.INIT)
            ctx.add_init_script(signed_in(CLOSED))
            ctx.route("**/*", serve(stress_mock.StressMock("running", CLOSED, note=n), dist, CLOSED))
            pg = ctx.new_page()
            # by way of Activity, as a person arrives: it loads the engine's events, where the note's reason is
            pg.goto(audit.APP + "/activity", wait_until="load")
            pg.wait_for_selector("main > *")
            pg.wait_for_timeout(1200)
            pg.click("nav.tabbar a[href='/']")
            pg.wait_for_selector("main .pot, main .note-card, main section")
            pg.wait_for_timeout(1500)
            check(pg, f"stress-note-{name}", w, metrics, fails)
            note = pg.evaluate(NOTE_JS)
            print(f"stress note {name} {w}: {note}")
            if note_bad(note) or not note["cut"]:
                fails.append(f"stress-note-{name}@{w}: the daily note card is missing, overflows, is off the screen, under 44 px or not cut to three lines: {note}")
                ctx.close()
                continue
            pg.click(".note-btn")
            pg.wait_for_selector("[role=dialog]")
            pg.wait_for_timeout(500)
            sheet = pg.evaluate(NOTE_SHEET_JS)
            text = sheet.pop("text")
            why = n.get("why")
            said = why is None or why[:60] in text
            print(f"stress note sheet {name} {w}: {sheet}, reason shown {said}")
            if sheet["over"] or sheet["out"] or sheet["right"] > sheet["w"] or sheet["full"] != len(n["text"]) or not said:
                fails.append(f"stress-note-sheet-{name}@{w}: the sheet overflows, or does not show the whole note ({len(n['text'])} characters) and its reason: {sheet}, reason shown {said}")
            ctx.close()
    ctx = context(browser, OPEN, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.add_init_script(signed_in(OPEN))
    ctx.route("**/*", serve(stress_mock.StressMock("running", OPEN), dist, OPEN))
    pg = ctx.new_page()
    pg.goto(audit.APP + "/", wait_until="load")
    pg.wait_for_selector("main > *")
    pg.wait_for_timeout(1500)
    shown = pg.evaluate("!!document.querySelector('.note-card')")
    print(f"stress note, market open 390: card shown {shown}")
    if shown:
        fails.append("stress-note@390: the daily note shows during market hours (it belongs after the close, until the next open)")
    ctx.close()


# The live state on a page: the freshness line (its text, and that it fits its box and the screen), how many rows carry
# a change today, and the longest figure in a row's end column.
LIVE_JS = """() => { const f = document.querySelector('.fresh'); const r = f ? f.getBoundingClientRect() : null;
  const ends = [...document.querySelectorAll('.hold-end, .row-end')];
  const wide = ends.filter((e) => e.scrollWidth > e.clientWidth + 1 || [...e.children].some((k) => k.getBoundingClientRect().left < e.parentElement.getBoundingClientRect().left)).length;
  return { line: f ? f.textContent : null, live: !!f && f.classList.contains('live'), lines: f ? Math.round(r.height / parseFloat(getComputedStyle(f).lineHeight)) : 0,
    over: !!f && (f.scrollWidth > f.clientWidth + 1 || r.right > innerWidth || r.left < 0), size: f ? getComputedStyle(f).fontSize : null,
    days: document.querySelectorAll('.hold-end .day, .row-end .day').length, wide,
    longest: ends.flatMap((e) => [...e.children].map((k) => k.textContent.trim())).sort((a, b) => b.length - a.length)[0] || '',
    hero: (document.querySelector('.hero') || {}).textContent || null,
    heroOver: !!document.querySelector('.hero') && document.querySelector('.hero').scrollWidth > document.querySelector('.hero').clientWidth + 1,
    moving: document.getAnimations().filter((a) => a.effect && a.effect.getComputedTiming().iterations === Infinity
      && a.effect.target && a.effect.target.closest('.home-hero, .hold-rows, .rows, .pos-head')).length }; }"""


def stress_live(browser, dist: pathlib.Path, metrics: str, fails: list[str]) -> None:
    """The live state (stakeholder, 19 Nov 2026), with the market open (OPEN) and the crowded book held at the broker:
    Home, Positions and the position sheet at 375, 390 and 430 px. /api/marks is answered by the stress mock, with
    prices far from the close for the biggest holdings. Each: no overflow, no tap target under 44 px, no row whose
    parts overlap, the freshness line on one line inside the screen, a change today on every row, and nothing that
    loops. Once per width also the fallback (the function not answering): the engine's line, and no row says today."""
    import stress_mock
    for w, h in PHONES:
        for name, path, live in [("home", "/", True), ("positions", "/positions", True), ("home-fallback", "/", False)]:
            ctx = context(browser, OPEN, viewport={"width": w, "height": h}, is_mobile=True, has_touch=True)
            ctx.add_init_script(audit.INIT)
            ctx.add_init_script(signed_in(OPEN))
            ctx.route("**/*", serve(stress_mock.StressMock("running", OPEN, live=live), dist, OPEN))
            pg = ctx.new_page()
            pg.goto(audit.APP + path, wait_until="load")
            pg.wait_for_selector("main > *")
            pg.wait_for_timeout(1800)
            check(pg, f"stress-live-{name}", w, metrics, fails)
            bad = pg.evaluate(MISALIGN)
            st = pg.evaluate(LIVE_JS)
            print(f"stress live {name} {w}: {st}, misaligned {bad}")
            if bad:
                fails.append(f"stress-live-{name}@{w}: {bad}")
            if st["lines"] != 1 or st["over"] or st["size"] != "13px" or st["wide"] or st["heroOver"] or st["moving"]:
                fails.append(f"stress-live-{name}@{w}: the freshness line or a row's figures do not fit, or something loops: {st}")
            want = 3 if name == "home" else 27 if name == "positions" else 0
            if live and (not st["live"] or not (st["line"] or "").startswith("Live · prices ") or st["days"] != want or len(st["longest"]) < 12):
                fails.append(f"stress-live-{name}@{w}: not live, or a row has no change today, or no long figure was measured: {st}")
            if not live and (st["live"] or not re.fullmatch(r"Updated .+ by the engine", st["line"] or "") or st["days"]):
                fails.append(f"stress-live-{name}@{w}: the fallback does not say the engine's figure is shown, or a row says today: {st}")
            if name == "positions":
                pg.locator(".rows .row").first.click()
                pg.wait_for_selector("[role=dialog]")
                pg.wait_for_timeout(500)
                sheet = pg.evaluate("(() => { const d = document.querySelector('[role=dialog] .sheet, [role=dialog]'); const r = d.getBoundingClientRect(); "
                                    "return { over: d.scrollWidth > d.clientWidth + 1, right: Math.round(r.right), w: innerWidth, "
                                    "now: d.textContent.includes('Price now'), today: /Today[^A-Za-z]*%/.test(d.textContent), live: d.textContent.includes('Prices are live') }; })()")
                bad = pg.evaluate(MISALIGN)
                print(f"stress live sheet {w}: {sheet}, misaligned {bad}")
                if sheet["over"] or sheet["right"] > sheet["w"] or bad or not (sheet["now"] and sheet["today"] and sheet["live"]):
                    fails.append(f"stress-live-sheet@{w}: {sheet} {bad}")
            ctx.close()


def stress(browser, dist: pathlib.Path, metrics: str, fails: list[str]) -> None:
    """--stress: the first real holdings, crowded (scripts/stress_mock.py): Home's top holdings, Positions, the position
    sheet and the book card with 28 multi-day positions, at 375, 390 and 430 px. Fails on overflow, a tap target under
    44 px, a row whose parts overlap or stick out, a position with 0 shares shown as held, or text contrast.
    After the close (CLOSED): the plan is tonight's, the unfilled order is today's and Home shows the daily note."""
    import stress_mock
    now, session = CLOSED, signed_in(CLOSED)
    for w, h in PHONES:
        for name, path in [("home", "/"), ("positions", "/positions"), ("engine", "/health")]:
            ctx = context(browser, now, viewport={"width": w, "height": h}, is_mobile=True, has_touch=True)
            ctx.add_init_script(audit.INIT)
            ctx.add_init_script(session)
            ctx.route("**/*", serve(stress_mock.StressMock("running", now), dist, now))
            pg = ctx.new_page()
            pg.goto(audit.APP + path, wait_until="load")
            pg.wait_for_selector("main > *")
            pg.wait_for_timeout(1500)
            check(pg, f"stress-{name}", w, metrics, fails)
            bad = pg.evaluate(MISALIGN)
            rows = pg.evaluate("document.querySelectorAll('.hold-row, .rows .row').length")
            zero = pg.evaluate("[...document.querySelectorAll('.rows .row, .hold-row')].some(r => [...r.querySelectorAll('b')].some(b => b.textContent.trim() === 'ORCL'))")
            print(f"stress {name} {w}: {rows} rows, misaligned {bad}, 0-share ORCL shown {zero}")
            if bad:
                fails.append(f"stress-{name}@{w}: {bad}")
            if zero:
                fails.append(f"stress-{name}@{w}: a position with 0 shares left is shown as held")
            if name == "positions":
                # 3 orders tonight; one held row (SUPERLONG, still at the broker) is tagged; NFLX is filled, TSLA not held yet
                plan = pg.evaluate("[document.querySelectorAll('.nc-list li').length, [...document.querySelectorAll('.row-plan')].map(t => t.closest('.row').querySelector('b').textContent + ': ' + t.textContent).join(), "
                                   "(document.querySelector('.next-close')?.scrollWidth || 0) > (document.querySelector('.next-close')?.clientWidth || 0) + 1]")
                print(f"stress plan {w}: {plan[0]} orders, tags [{plan[1]}], overflow {plan[2]}")
                if plan[0] != 3 or plan[1] != "SUPERLONG: Selling tonight" or plan[2]:
                    fails.append(f"stress-positions@{w}: the next close's plan is missing or overflows: {plan}")
                pg.locator(".rows .row").first.click()
                pg.wait_for_selector("[role=dialog]")
                pg.wait_for_timeout(500)
                sheet = pg.evaluate("(() => { const d = document.querySelector('[role=dialog] .sheet, [role=dialog]'); const r = d.getBoundingClientRect(); "
                                    "return { over: d.scrollWidth > d.clientWidth + 1, right: Math.round(r.right), w: innerWidth }; })()")
                bad = pg.evaluate(MISALIGN)
                print(f"stress sheet {w}: {sheet}, misaligned {bad}")
                if sheet["over"] or sheet["right"] > sheet["w"] or bad:
                    fails.append(f"stress-sheet@{w}: {sheet} {bad}")
            if name == "home":
                # the daily note (#311): one small card, its text cut to three lines, the full text in a sheet
                note = pg.evaluate(NOTE_JS)
                print(f"stress note {w}: {note}")
                if note_bad(note):
                    fails.append(f"stress-note@{w}: the daily note card is missing, overflows, is off the screen, under 44 px or not cut to three lines: {note}")
            if name == "engine":
                # the shadow pre-open review (#310): one row labelled observer; its reasons open in a sheet that fits
                pg.get_by_role("button", name=re.compile("Pre-open reviewer")).click()
                pg.wait_for_selector("[role=dialog]")
                pg.wait_for_timeout(500)
                sheet = pg.evaluate("(() => { const d = document.querySelector('[role=dialog]'); const r = d.getBoundingClientRect(); "
                                    "return { over: d.scrollWidth > d.clientWidth + 1, right: Math.round(r.right), w: innerWidth, observer: d.textContent.includes('observer · no effect on trades') }; })()")
                print(f"stress shadow sheet {w}: {sheet}")
                if sheet["over"] or sheet["right"] > sheet["w"] or not sheet["observer"]:
                    fails.append(f"stress-shadow@{w}: {sheet}")
            if name == "positions":
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(400)
                pg.get_by_role("tab", name="Closed").click()
                pg.wait_for_timeout(1200)
                sold = pg.evaluate("document.querySelectorAll('[aria-label=\\'Sold from the multi-day book\\'] .row').length")
                bad = pg.evaluate(MISALIGN)
                print(f"stress sold {w}: {sold} sold rows, misaligned {bad}")
                if sold != 4 or bad:
                    fails.append(f"stress-sold@{w}: {sold} sold rows, {bad}")
                check(pg, "stress-sold", w, metrics, fails)
            ctx.close()
    stress_notes(browser, dist, metrics, fails)
    stress_live(browser, dist, metrics, fails)


def main(a) -> int:
    dist = pathlib.Path(a.dist).resolve()
    metrics = (TOOLS / "metrics.js").read_text()
    now = OPEN  # never the wall clock: see OPEN and CLOSED above
    want = set(a.routes.split(",")) if a.routes else None
    routes = [r for r in audit.ROUTES if want is None or r[0] in want]
    sizes = [s for s in PHONES + DESKTOP if not a.widths or str(s[0]) in a.widths.split(",")]
    session = signed_in(now)
    fails: list[str] = []
    rows: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for w, h in sizes:
            for name, path, act in routes:
                phone = w <= 430
                ctx = context(browser, now, viewport={"width": w, "height": h}, is_mobile=phone, has_touch=phone,
                              color_scheme="dark")
                ctx.add_init_script(audit.INIT)
                if act != "signed-out":
                    ctx.add_init_script(session)
                ctx.route("**/*", serve(mock_data.Mock("running", now), dist, now))
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
        if a.stress:
            stress(browser, dist, metrics, fails)
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
    ap.add_argument("--stress", action="store_true", help="also a crowded multi-day book (scripts/stress_mock.py)")
    sys.exit(main(ap.parse_args()))
