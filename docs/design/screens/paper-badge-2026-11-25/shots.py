"""Before/after shots and measurements of the PAPER badge. Usage: shots.py DIST OUTDIR PREFIX"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "apps" / "web" / "scripts"))
import mobile_check as mc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

dist, out, prefix = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
out.mkdir(parents=True, exist_ok=True)
SIZES = [(360, 640), (390, 844), (1280, 800), (1440, 900)]
PAGES = [("home", "/", True), ("sign-in", "/", False), ("register", "/register?code=ABC", False)]
MEASURE = r"""() => {
  const q = (s) => document.querySelector(s);
  const r = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom].map((v) => Math.round(v * 100) / 100); };
  const pill = q('header.top .pill') || q('.brand-row .pill');
  const logo = q('header.top .logo-word') || q('.brand-row .logo-word');
  const hdr = q('header.top');
  const cs = pill && getComputedStyle(pill);
  const tops = hdr ? [...hdr.children].filter((e) => e.getBoundingClientRect().height > 0)
    .map((e) => Math.round(e.getBoundingClientRect().top + e.getBoundingClientRect().height / 2)) : [];
  return {
    logo: r(logo), pill: r(pill), pillText: pill && pill.textContent,
    font: cs && cs.fontSize, padding: cs && cs.padding, bg: cs && cs.backgroundColor, color: cs && cs.color,
    gap: pill && logo ? Math.round((pill.getBoundingClientRect().left - logo.getBoundingClientRect().right) * 100) / 100 : null,
    topDelta: pill && logo ? Math.round((pill.getBoundingClientRect().top - logo.getBoundingClientRect().top) * 100) / 100 : null,
    header: hdr ? Math.round(hdr.getBoundingClientRect().height) : null, rows: new Set(tops).size,
    overflow: document.documentElement.scrollWidth > innerWidth || (hdr ? hdr.scrollWidth > hdr.clientWidth : false),
    halt: !!q('header.top .ts-chip.red'), chip: r(q('header.top .engine-chip')),
    chipLabelWhole: (() => { const l = q('header.top .ts-chip b'); return l ? l.scrollWidth <= l.clientWidth : null; })(),
    nav: r(q('nav.topnav')),
  };
}"""
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        for name, path, signed in PAGES:
            for w, h in SIZES:
                phone = w < 761
                ctx = mc.context(b, mc.OPEN, viewport={"width": w, "height": h}, is_mobile=phone, has_touch=phone,
                                 reduced_motion="reduce")
                if signed:
                    ctx.add_init_script(mc.signed_in(mc.OPEN))
                ctx.add_init_script(f"document.documentElement.setAttribute('data-theme', '{theme}')")
                ctx.route("**/*", mc.serve(mc.mock_data.Mock("running", mc.OPEN), dist, mc.OPEN))
                pg = ctx.new_page()
                pg.goto(mc.audit.APP + path, wait_until="load")
                pg.wait_for_selector("main > *, .center")
                pg.wait_for_timeout(1300)
                pg.evaluate(f"document.documentElement.setAttribute('data-theme', '{theme}')")
                pg.wait_for_timeout(100)
                key = f"{name}-{theme}-{w}x{h}"
                res[key] = pg.evaluate(MEASURE)
                if name != "register" or (w, h) in ((390, 844), (1440, 900)):
                    pg.screenshot(path=str(out / f"{prefix}-{key}.png"))
                ctx.close()
    b.close()
(out / f"{prefix}-metrics.json").write_text(json.dumps(res, indent=1) + "\n")
for k, v in res.items():
    print(k, json.dumps({x: v[x] for x in ("logo", "pill", "font", "gap", "topDelta", "header", "rows", "overflow", "halt", "chipLabelWhole")}))
