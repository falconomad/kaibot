"""Screenshots, word counts and an axe-core pass of the pre-open review mockups (#215). Dev-only.

    python shoot.py <axe.min.js>      (metrics.js comes from docs/design/audit_2026-11-16/tools)
"""
import json, pathlib, sys
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
metrics = (HERE.parents[2] / "audit_2026-11-16/tools/metrics.js").read_text()
axe = pathlib.Path(sys.argv[1]).read_text()
SHOTS = [("plan", 390, 844, False), ("activity", 390, 844, False), ("sheet", 390, 844, False), ("states", 390, 844, True),
         ("home-failed", 390, 844, False), ("desktop", 1440, 900, False)]
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for name, w, h, full in SHOTS:
        mob = w < 500
        c = b.new_context(viewport={"width": w, "height": h}, is_mobile=mob, has_touch=mob, device_scale_factor=2 if mob else 1)
        p = c.new_page(); p.goto((HERE / f"{name}.html").as_uri()); p.wait_for_timeout(300)
        m = p.evaluate(metrics); p.evaluate(axe)
        ax = p.evaluate("async () => (await axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']})).violations.map(v => v.id + ':' + v.nodes.length + ' ' + v.nodes.slice(0,2).map(n => n.target.join(' ')).join(', '))")
        p.screenshot(path=str(HERE / f"{name}.png"), full_page=full)
        print(json.dumps({"page": name, "foldWords": m["foldWords"], "pageWords": m["pageWords"], "overflow": m["docOverflow"],
                          "tapSmall": [(t["sel"], t["text"][:20], t["w"], t["h"]) for t in m["tapSmall"]], "smallWords": m["smallWords"], "axe": ax}))
        c.close()
    b.close()
