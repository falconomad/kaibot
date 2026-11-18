"""Sample each company logo's colours for its tile (dev-only; `npm run logo-colors`). Stakeholder, 17 Nov 2026.

Reads src/data/symbols.json, downloads each logo once into a temporary folder, and writes src/data/logo_colors.json:
for each symbol {"bg", "fg", "pad", "sampled", "from"}. `from` is the URL of the image sampled: the first source the app
shows (AllInvestView's https://cdn.tickerlogos.com/{domain} when the symbol has a domain, stakeholder 18 Nov 2026, else
our Finnhub logo; the next one when that fails). The app uses a colour only for that very image (StockBadge.tsx);
for any other image the browser samples the tickerlogos logo itself, with the same rule as `colours()` below
(src/lib/logos.ts, tileFromPixels). The images are third-party: they are read here, at build time only,
and deleted; only the colours are committed.

- The logo has a solid background (its edge is opaque and even): the tile takes that colour. White or near-white
  stays a soft white tile with 12% padding; any other colour goes full-bleed (padding 0), as the logo was drawn.
- The logo is transparent at its edge: it sits on a neutral tile chosen for contrast with its ink: a soft white tile
  for dark ink, a dark tile for light ink, 12% padding.
- A logo that can't be fetched keeps "sampled": false (the app shows it on a soft white tile until it is sampled); one
  that is fetched but can't be read is marked "unreadable" and the app shows the initials tile.

    cd apps/web && npm run logo-colors        (needs network access to the logo host)
"""
from __future__ import annotations

import io
import json
import pathlib
import statistics
import sys
import urllib.request

WEB = pathlib.Path(__file__).resolve().parents[1]
SYMBOLS = WEB / "src" / "data" / "symbols.json"
OUT = WEB / "src" / "data" / "logo_colors.json"
SOFT_WHITE, DARK_TILE, PAD = "#F4F5F7", "#1B1E23", 12
rgb_of = lambda h: [int(h[i:i + 2], 16) for i in (1, 3, 5)]  # noqa: E731


def hexc(rgb) -> str:
    return "#" + "".join(f"{int(round(c)):02X}" for c in rgb[:3])


def lum(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def colours(png: bytes) -> dict:
    from PIL import Image  # dev-only dependency

    im = Image.open(io.BytesIO(png)).convert("RGBA")
    im.thumbnail((96, 96))
    w, h = im.size
    px = im.load()
    edge = [px[x, y] for x in range(w) for y in range(h) if min(x, y, w - 1 - x, h - 1 - y) < max(2, w // 25)]
    opaque = [p for p in edge if p[3] > 240]
    ink = [p for p in (px[x, y] for x in range(w) for y in range(h)) if p[3] > 200]
    if len(opaque) > 0.9 * len(edge):
        mean = [statistics.fmean(p[i] for p in opaque) for i in range(3)]
        spread = max(statistics.pstdev([p[i] for p in opaque]) for i in range(3))
        if spread < 18:  # an even, solid background
            if lum(mean) > 0.8:
                return {"bg": SOFT_WHITE, "pad": PAD}
            return {"bg": hexc(mean), "pad": 0}
    if not ink:
        raise ValueError("empty image")
    ink_rgb = [statistics.fmean(p[i] for p in ink) for i in range(3)]
    tile = SOFT_WHITE if contrast(ink_rgb, rgb_of(SOFT_WHITE)) >= contrast(ink_rgb, rgb_of(DARK_TILE)) else DARK_TILE
    return {"bg": tile, "fg": hexc(ink_rgb), "pad": PAD}


def main() -> int:
    meta = json.loads(SYMBOLS.read_text())
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    out, ok = {}, 0
    for sym, m in sorted(meta.items()):
        cdn = f"https://cdn.tickerlogos.com/{m['domain']}" if m.get("domain") else None
        got, unreadable, why = None, False, "no logo"
        for url in (u for u in (cdn, m.get("logo")) if u):  # in the app's order: the next source when one fails
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "logo-colors (build script)"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read()
            except Exception as e:  # noqa: BLE001
                why = f"{type(e).__name__}: {str(e)[:80]}"
                continue
            try:
                got = {**colours(data), "sampled": True, "from": url}
                break
            except Exception as e:  # noqa: BLE001  fetched but unreadable
                why = f"unreadable {type(e).__name__}: {str(e)[:80]}"
                unreadable = unreadable or url == m.get("logo")  # only our own logo is skipped by the app
        if got:
            out[sym] = got
            ok += 1
        elif unreadable:  # the app skips our logo and shows the next source, or the initials
            out[sym] = {"bg": None, "pad": PAD, "sampled": False, "unreadable": True}
        else:  # not fetched: keep an earlier sample, else "pending" (a soft white tile in the app)
            out[sym] = old.get(sym) if old.get(sym, {}).get("sampled") else {"bg": None, "pad": PAD, "sampled": False}
        if not got:
            print(f"{sym}: not sampled ({why})", file=sys.stderr)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(WEB)}: {ok} of {len(meta)} logos sampled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
