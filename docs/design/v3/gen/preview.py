"""Render v3 boards with a headless browser and check their layout before a PR.

A local check only. It is not the design canvas: `preview_support.js` stands in for the canvas runtime and supports
just what the v3 boards use. Needs Playwright for Python and a Chrome or Chromium on the machine.

Run from this folder:
  python3 preview.py Home.dc.html                             one board at its own preview size
  python3 preview.py Overview.dc.html --width 390 --height 844 --full --check
  python3 preview.py --shots shots_m7.json --out /tmp/m7 --check   every shot in a list, with the checks
  python3 preview.py --shots shots_m7.json --kept --small --out ../preview/m7     refresh the pictures kept in the repo

Checks (--check): sideways overflow, clipped text, text that spills out of its card, text boxes that overlap, chart
labels outside their chart,
pointer targets under 44 px (and under the WCAG 2.2 minimum of 24 px), text contrast under WCAG AA, gain or loss
colour on a number that has no sign or arrow, and bindings that found no value. PNGs go to --out (default /tmp/kb-preview).
"""
import argparse
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BOARDS = HERE.parent / 'project' / 'v3'
SHIM = (HERE / 'preview_support.js').read_text()

CHECK_JS = r'''
(opts) => {
  const vw = document.documentElement.clientWidth;
  const out = { fonts: document.fonts.check('16px Inter'), scrollWidth: document.documentElement.scrollWidth, viewport: vw,
    overflow: [], clipped: [], spill: [], overlaps: [], svg: [], targets: [], contrast: [], colourOnly: [] };
  const cut = (s) => s.replace(/\s+/g, ' ').trim().slice(0, 60);
  const path = (el) => { const bits = []; let e = el;
    while (e && e.nodeType === 1 && bits.length < 4) { bits.unshift(e.localName + (e.id ? '#' + e.id : '') + (e.classList && e.classList.length ? '.' + e.classList[0] : '')); e = e.parentElement; }
    return bits.join('>'); };
  const shown = (el) => { for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.02) return false; } return true; };
  // nearest ancestor that clips sideways
  const clipper = (el, self) => { for (let e = self ? el : el.parentElement; e; e = e.parentElement) { const cs = getComputedStyle(e);
      if (/(auto|scroll|hidden|clip)/.test(cs.overflowX)) return e; } return null; };
  const srOnly = (el) => { for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.position === 'absolute' && (cs.clip === 'rect(0px, 0px, 0px, 0px)' || (e.offsetWidth <= 1 && e.offsetHeight <= 1))) return true; } return false; };

  // 1. sideways overflow
  if (out.scrollWidth > vw + 1) {
    document.querySelectorAll('body *').forEach((el) => { if (!shown(el)) return; const r = el.getBoundingClientRect();
      if (r.width && r.right > vw + 1 && !clipper(el)) out.overflow.push({ el: path(el), right: Math.round(r.right), text: cut(el.textContent || '') }); });
    out.overflow = out.overflow.slice(0, 12);
  }

  // 2. clipped content
  document.querySelectorAll('body *').forEach((el) => { if (!shown(el) || el.closest('svg')) return; const cs = getComputedStyle(el);
    const hx = /(hidden|clip)/.test(cs.overflowX), hy = /(hidden|clip)/.test(cs.overflowY);
    if ((hx && el.scrollWidth > el.clientWidth + 1) || (hy && el.scrollHeight > el.clientHeight + 1)) {
      if (el.classList.contains('sk') || el.classList.contains('phone') || srOnly(el) || el === document.body || el === document.documentElement) return;   // .phone = a drawn phone screen, cut at the fold on purpose
      out.clipped.push({ el: path(el), ellipsis: cs.textOverflow === 'ellipsis', need: el.scrollWidth + 'x' + el.scrollHeight, has: el.clientWidth + 'x' + el.clientHeight, text: cut(el.textContent || '') }); } });

  // text boxes: HTML text nodes and SVG <text>
  const boxes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.data.trim()) continue; const p = n.parentElement; if (!p || !shown(p) || srOnly(p)) continue;
    if (p.closest('svg') || p.closest('script') || p.closest('style') || p.closest('title')) continue;
    const range = document.createRange(); range.selectNodeContents(n);
    const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0.5 && r.height > 0.5);
    if (!rects.length) continue;
    const c = clipper(p, true); const cr = c ? c.getBoundingClientRect() : null;
    rects.forEach((r0) => { let r = r0;
      if (cr) {   // only the part a clipping ancestor lets through is on screen
        r = { left: Math.max(r0.left, cr.left), right: Math.min(r0.right, cr.right), top: Math.max(r0.top, cr.top), bottom: Math.min(r0.bottom, cr.bottom) };
        if (r.right - r.left < 1 || r.bottom - r.top < 1) return; }
      boxes.push({ node: p, r, text: cut(n.data), svg: false }); });
  }
  document.querySelectorAll('svg text').forEach((t) => { if (!shown(t) || !t.textContent.trim()) return; const r = t.getBoundingClientRect();
    if (r.width < 0.5) return; boxes.push({ node: t, r, text: cut(t.textContent), svg: true });
    const s = t.ownerSVGElement.getBoundingClientRect();
    if (r.left < s.left - 1 || r.right > s.right + 1 || r.top < s.top - 1 || r.bottom > s.bottom + 1)
      out.svg.push({ text: cut(t.textContent), by: [Math.round(s.left - r.left), Math.round(r.right - s.right), Math.round(s.top - r.top), Math.round(r.bottom - s.bottom)].map((v) => Math.max(0, v)).join('/') + ' px (l/r/t/b)' }); });

  // 3a. text that spills out of the box it sits in (a card, pill or row with its own border or background)
  const rgba = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return p.length > 3 ? p[3] : 1; };
  boxes.forEach((bx) => { if (bx.svg) return;
    for (let e = bx.node; e && e !== document.body; e = e.parentElement) { const cs = getComputedStyle(e);
      if (/(auto|scroll|hidden|clip)/.test(cs.overflowX + cs.overflowY)) break;   // cut there on purpose or reported as clipped
      const framed = parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth) > 0 || rgba(cs.backgroundColor) > 0;
      if (!framed) continue; const a = e.getBoundingClientRect();
      const by = [a.left - bx.r.left, bx.r.right - a.right, a.top - bx.r.top, bx.r.bottom - a.bottom].map((v) => Math.max(0, Math.round(v)));
      if (by[0] > 1 || by[1] > 1 || by[2] > 2 || by[3] > 2) { out.spill.push({ text: bx.text, box: path(e), by: by.join('/') + ' px (l/r/t/b)' }); break; } } });
  out.spill = out.spill.slice(0, 20);

  // 3. overlaps between text boxes of different elements
  const fixed = (el) => { for (let e = el; e && e.nodeType === 1; e = e.parentElement) if (getComputedStyle(e).position === 'fixed') return true; return false; };
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j]; if (a.node === b.node) continue;
    const w = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left), h = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
    if (w > 2 && h > 3) { if (fixed(a.node) !== fixed(b.node)) continue;   // a pinned bar over scrolled content is expected
      out.overlaps.push({ a: a.text, b: b.text, by: Math.round(w) + 'x' + Math.round(h) }); }
  }
  out.overlaps = out.overlaps.slice(0, 20);

  // 4. pointer targets
  const sel = 'a[href], button, input, select, textarea, summary, [role=button], [role=tab], [role=radio], [role=switch], [role=link], [tabindex]:not([tabindex="-1"])';
  document.querySelectorAll(sel).forEach((el) => { if (!shown(el) || srOnly(el)) return; const r = el.getBoundingClientRect(); if (!r.width) return;
    const c = clipper(el); if (c) { const cr = c.getBoundingClientRect(); if (r.right < cr.left + 1 || r.left > cr.right - 1) return; }
    const w = Math.round(r.width), h = Math.round(r.height);
    if (w < opts.target || h < opts.target) { const cs = getComputedStyle(el);
      const inline = cs.display === 'inline' && el.parentElement && cut(el.parentElement.textContent).length > cut(el.textContent).length + 8;
      out.targets.push({ el: path(el), size: w + 'x' + h, text: cut(el.getAttribute('aria-label') || el.textContent || ''), under24: w < 24 || h < 24, inline }); } });

  // 5. text contrast (WCAG 2.x relative luminance)
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => '#' + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();
  const backdrop = (el) => { const layers = []; let image = false;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') image = true;
      const c = parse(cs.backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; } }
    let bg = { r: 10, g: 11, b: 13, a: 1 }; for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
    return { bg, image }; };
  const seen = {};
  boxes.forEach((bx) => { const el = bx.node; const cs = getComputedStyle(el);
    const raw = parse(bx.svg ? cs.fill : cs.color); if (!raw) return;
    const host = bx.svg ? el.ownerSVGElement.parentElement : el; const { bg, image } = backdrop(host);
    const fg = over(raw, bg); const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700); const need = large ? 3 : 4.5; const got = ratio(fg, bg);
    const key = hex(fg) + ' on ' + hex(bg) + (large ? ' large' : '');
    if (!seen[key]) seen[key] = { pair: key, ratio: Math.round(got * 100) / 100, need, ok: got >= need, n: 0, minSize: size, sample: bx.text, image };
    seen[key].n += 1; seen[key].minSize = Math.min(seen[key].minSize, size); seen[key].image = seen[key].image || image;
    // 6. gain or loss colour on a number needs a sign or an arrow
    const h = hex(raw); if ((h === '#3DDC97' || h === '#FF6B6B') && /\d/.test(bx.text) && !/[▲▼+−\-✓✕]/.test(el.textContent))
      out.colourOnly.push({ colour: h, text: bx.text }); });
  out.contrast = Object.values(seen).sort((a, b) => a.ratio - b.ratio);
  return out;
}
'''


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve():
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(BOARDS)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def launch(pw):
    for kw in ({'channel': 'chrome'}, {}):
        try:
            return pw.chromium.launch(args=['--no-sandbox'], **kw)
        except Exception as e:  # noqa: BLE001
            err = e
    raise SystemExit(f'no Chrome or Chromium to launch: {err}')


def shoot(browser, base, shot, out_dir, check, scale, motion, small=False):
    board = shot['board']
    html = (BOARDS / board).read_text()
    spec = json.loads(html.split("data-props='", 1)[1].split("'>", 1)[0]) if "data-props='" in html else {}
    size = spec.get('$preview', {})
    w, h = int(shot.get('width') or size.get('width', 1440)), int(shot.get('height') or size.get('height', 900))
    ctx = browser.new_context(viewport={'width': w, 'height': h}, device_scale_factor=scale,
                              reduced_motion='no-preference' if motion else 'reduce', has_touch=w <= 760)
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append('uncaught: ' + str(e)[:300]))
    page.on('console', lambda m: m.type == 'error' and 'attribute' not in m.text and 'Failed to load resource' not in m.text
            and errors.append(m.text[:300]))
    page.route('**/support.js', lambda r: r.fulfill(body=SHIM, content_type='application/javascript'))
    page.route('https://**/*', lambda r: r.abort())          # fonts come from the machine; nothing leaves it
    page.add_init_script('window.__DC_PROPS__ = ' + json.dumps(shot.get('props') or {}))
    page.goto(f'{base}/{board}')
    page.wait_for_function('() => window.__dc && window.__dc.ready', timeout=10000)
    page.wait_for_timeout(int(shot.get('wait', 1300)))        # boards show a skeleton for about a second first
    if shot.get('state'):
        page.evaluate('(s) => window.__dc.comp.setState(s)', shot['state'])
        page.wait_for_timeout(250)
    for sel in shot.get('clicks') or []:
        page.locator(sel).first.click(timeout=4000)
        page.wait_for_timeout(350)
    if shot.get('after'):                                      # state to set once the clicks are done
        page.evaluate('(s) => window.__dc.comp.setState(s)', shot['after'])
        page.wait_for_timeout(250)
    if shot.get('css'):                                        # e.g. hide the pinned tab bar in a whole-page phone picture
        page.add_style_tag(content=shot['css'])
    if shot.get('scroll'):
        page.evaluate('(y) => window.scrollTo(0, y)', shot['scroll'])
        page.wait_for_timeout(150)
    if motion:
        page.wait_for_timeout(2200)
    name = shot.get('name') or Path(board).name.replace('.dc.html', '')
    png = out_dir / f'{name}.png'
    page.screenshot(path=str(png), full_page=bool(shot.get('full')))
    if small:                                                  # palette PNG, about a quarter of the size: for pictures kept in the repo
        from PIL import Image
        Image.open(png).convert('RGB').quantize(colors=160, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(png, optimize=True)
    report = None
    if check:
        report = page.evaluate(CHECK_JS, {'target': 44})
        report['height'] = page.evaluate('() => document.documentElement.scrollHeight')
        report['missing'] = page.evaluate('() => Object.keys(window.__dc.missing)')
    ctx.close()
    return name, png, (w, h), errors, report


def summarise(name, size, errors, rep, verbose):
    bad = 0
    print(f'\n== {name}  {size[0]}x{size[1]}' + (f"  page height {rep['height']}" if rep else ''))
    for e in errors:
        bad += 1
        print('  script error:', e)
    if not rep:
        return bad
    for m in rep['missing']:
        bad += 1
        print('  binding without a value: {{' + m + '}}')
    if not rep['fonts']:
        print('  note: Inter is not installed here, so text widths are only approximate')
    if rep['scrollWidth'] > rep['viewport'] + 1:
        bad += 1
        print(f"  SIDEWAYS OVERFLOW: page is {rep['scrollWidth']} px wide in a {rep['viewport']} px window")
        for o in rep['overflow']:
            print('    ', o)
    for c in rep['clipped']:
        if c['ellipsis'] and not verbose:
            continue
        bad += 0 if c['ellipsis'] else 1
        print('  clipped' + (' (ellipsis)' if c['ellipsis'] else '') + ':', c)
    for o in rep['spill']:
        bad += 1
        print('  TEXT SPILLS OUT OF ITS BOX:', o)
    for o in rep['overlaps']:
        bad += 1
        print('  OVERLAP:', o)
    for s in rep['svg']:
        bad += 1
        print('  chart label outside its chart:', s)
    small = [t for t in rep['targets'] if not t['inline']]
    under24 = [t for t in small if t['under24']]
    bad += len(under24)
    if size[0] <= 760:
        bad += len(small) - len(under24)
    for t in small:
        if t['under24'] or size[0] <= 760 or verbose:
            print('  target under ' + ('24 px (WCAG 2.5.8)' if t['under24'] else '44 px') + ':', t)
    for c in rep['contrast']:
        if not c['ok']:
            bad += 1
            print('  CONTRAST below AA:', c)
        elif verbose:
            print('  contrast ok:', c)
    for c in rep['colourOnly']:
        bad += 1
        print('  gain/loss colour without a sign or arrow:', c)
    if not bad:
        low = rep['contrast'][0] if rep['contrast'] else None
        print('  ok' + (f" · lowest text contrast {low['ratio']}:1 ({low['pair']})" if low else ''))
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('board', nargs='?', help='file name in ../project/v3')
    ap.add_argument('--shots', help='JSON list of shots: board, name, width, height, full, props, state, clicks, after, css, scroll, wait, keep')
    ap.add_argument('--only', help='with --shots: only names containing this text')
    ap.add_argument('--kept', action='store_true', help='with --shots: only the shots marked "keep", the pictures stored in the repo')
    ap.add_argument('--width', type=int)
    ap.add_argument('--height', type=int)
    ap.add_argument('--full', action='store_true', help='capture the whole page, not just the window')
    ap.add_argument('--props', default='{}', help='JSON of board props, e.g. {"showLoading": true}')
    ap.add_argument('--state', help='JSON merged into the board state after it loads')
    ap.add_argument('--click', action='append', help='CSS or text selector to click before the capture (repeatable)')
    ap.add_argument('--out', default='/tmp/kb-preview')
    ap.add_argument('--scale', type=float, default=1)
    ap.add_argument('--motion', action='store_true', help='leave animations on and wait for them to finish')
    ap.add_argument('--small', action='store_true', help='save palette PNGs (needs Pillow): for pictures kept in the repo')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--json', help='write the raw check reports to this file')
    a = ap.parse_args()
    if a.shots:
        shots = json.loads(Path(a.shots).read_text())
        if a.only:
            shots = [s for s in shots if a.only in (s.get('name') or s['board'])]
        if a.kept:
            shots = [s for s in shots if s.get('keep')]
    elif a.board:
        shots = [{'board': a.board, 'width': a.width, 'height': a.height, 'full': a.full, 'props': json.loads(a.props),
                  'state': json.loads(a.state) if a.state else None, 'clicks': a.click}]
    else:
        ap.error('give a board or --shots')
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    srv = serve()
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    bad, raw = 0, {}
    with sync_playwright() as pw:
        browser = launch(pw)
        for shot in shots:
            name, png, size, errors, rep = shoot(browser, base, shot, out_dir, a.check, a.scale, a.motion, a.small)
            raw[name] = rep
            bad += summarise(name, size, errors, rep, a.verbose) if (a.check or errors) else 0
            if not a.check:
                print(png)
        browser.close()
    srv.shutdown()
    if a.json:
        Path(a.json).write_text(json.dumps(raw, indent=1))
    if a.check:
        print(f'\n{len(shots)} shot(s), {bad} finding(s). PNGs in {out_dir}')
        sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
