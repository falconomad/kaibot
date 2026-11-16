"""Draw the favicon and the app icons from the one logo file (#254). Dev-only.

    cd apps/web && npm run icons          (python3 scripts/gen_icons.py)

Inputs, both in src/brand/:
- symbol.svg   the logo's symbol. The header, sign-in and New run show this same file (components/Logo.tsx).
- icons.json   which icons to draw: file, sizes, how much of the square the symbol fills, and the colour behind it.

Each icon is drawn in headless Chromium (Playwright, as scripts/mobile_check.py uses) and written to public/. The script
also writes src/brand/icons.lock.json: a hash of the two inputs and of every icon it wrote. src/brand.test.tsx compares
them, so a new logo with the old icons, or an icon changed by hand, fails `npm test`.

A new logo: replace symbol.svg, run this, commit what changed. Nothing else names the logo.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import re
import struct
import sys

from playwright.sync_api import sync_playwright

WEB = pathlib.Path(__file__).resolve().parents[1]
BRAND = WEB / "src" / "brand"
PUBLIC = WEB / "public"
COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inputs_hash(symbol: bytes, spec: bytes) -> str:
    """The same bytes, in the same order, as src/brand.test.tsx hashes."""
    return sha(symbol + b"\n" + spec)


def png_size(png: bytes) -> tuple[int, int]:
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    return struct.unpack(">II", png[16:24])


def ico(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico whose images are PNGs (every current browser and Windows since Vista read these)."""
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    return struct.pack("<HHH", 0, 1, len(images)) + entries + blobs


def draw(browser, svg_b64: str, size: int, scale: float, background: str | None) -> bytes:
    """One square PNG: the symbol centred, `scale` of the square wide, on `background` (None = transparent)."""
    if background is not None and not COLOUR.match(background):
        raise ValueError(f"background must be #RRGGBB or null, got {background!r}")
    if not 0 < scale <= 1:
        raise ValueError(f"scale must be above 0 and at most 1, got {scale!r}")
    fill = background or "transparent"
    inner = size * scale
    html = (
        f'<!doctype html><html><body style="margin:0;background:{fill}">'
        f'<div style="width:{size}px;height:{size}px;display:flex;align-items:center;justify-content:center;'
        f'overflow:hidden;background:{fill}">'
        f'<img alt="" src="data:image/svg+xml;base64,{svg_b64}" style="flex:none;width:{inner}px;height:{inner}px">'
        "</div></body></html>"
    )
    side = max(size, 64)  # a viewport smaller than this is not reliable; the clip below cuts the icon out
    ctx = browser.new_context(viewport={"width": side, "height": side}, device_scale_factor=1)
    try:
        page = ctx.new_page()
        page.set_content(html, wait_until="load")
        page.wait_for_function("document.images[0].complete && document.images[0].naturalWidth > 0")
        png = page.screenshot(clip={"x": 0, "y": 0, "width": size, "height": size}, omit_background=background is None)
    finally:
        ctx.close()
    if png_size(png) != (size, size):
        raise RuntimeError(f"drew {png_size(png)}, wanted {size}x{size}")
    return png


def main() -> int:
    symbol = (BRAND / "symbol.svg").read_bytes()
    spec_bytes = (BRAND / "icons.json").read_bytes()
    spec = json.loads(spec_bytes)
    svg_b64 = base64.b64encode(symbol).decode()
    files: dict[str, str] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for icon in spec["icons"]:
                background = icon["background"] if "background" in icon else spec["background"]
                images = [(s, draw(browser, svg_b64, s, icon["scale"], background)) for s in icon["sizes"]]
                name = icon["file"]
                if name.endswith(".ico"):
                    data = ico(images)
                elif name.endswith(".png") and len(images) == 1:
                    data = images[0][1]
                else:
                    raise ValueError(f"{name}: a .png takes one size, an .ico one or more")
                out = PUBLIC / name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                files[name] = sha(data)
                print(f"wrote public/{name}  {'+'.join(str(s) for s, _ in images)} px  {len(data)} bytes")
        finally:
            browser.close()
    lock = {"inputs": inputs_hash(symbol, spec_bytes), "files": files}
    (BRAND / "icons.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    print("wrote src/brand/icons.lock.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
