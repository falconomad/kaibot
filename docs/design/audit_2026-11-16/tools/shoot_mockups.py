"""Screenshots and word counts of the v4 mockups (#226). Dev-only.  python shoot_mockups.py <mockups dir> <metrics.js>"""
import json, pathlib, sys
from playwright.sync_api import sync_playwright

d, metrics = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).read_text()
out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    p = c.new_page()
    for name in ["home", "positions", "activity", "engine", "sheet"]:
        p.goto((d / f"{name}.html").as_uri()); p.wait_for_timeout(300)
        m = p.evaluate(metrics)
        out[name] = {k: m[k] for k in ("foldWords", "foldNums", "pageWords", "screens", "docOverflow", "smallWords")} | {"tapSmall": m["tapSmall"], "foldText": m["foldText"]}
        p.screenshot(path=str(d / f"{name}.png"))
    b.close()
print(json.dumps(out, indent=1))
