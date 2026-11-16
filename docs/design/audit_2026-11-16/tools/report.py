"""Turns audit.py's results.json into markdown tables and compressed screenshots (#226). Dev-only.

    python report.py /tmp/kb-audit <docs/design/audit_2026-11-16>
"""
import collections, json, pathlib, sys
from PIL import Image

src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
d = json.loads((src / "results.json").read_text()); R = d["results"]
by = {(r["page"], r["scenario"], r["w"]): r for r in R}
PAGES = [r["page"] for r in R if r["w"] == 390 and r["scenario"] == "running"]
out = []

def table(head, rows):
    out.append("| " + " | ".join(head) + " |"); out.append("|" + "---|" * len(head))
    out.extend("| " + " | ".join(str(c) for c in r) + " |" for r in rows); out.append("")

out.append("### T1. Phone (390x844), running scenario\n")
rows = []
for p in PAGES:
    m = by[(p, "running", 390)]["metrics"]
    rows.append([p, m["foldWords"], m["foldNums"], m["pageWords"], m["screens"], f'{len(m["tapSmall"])} / {m["tapTotal"]}', len(m["tapTiny"]), m["smallWords"],
                 sum(1 for t in m["tables"] if t["wider"]), max([t["scrollWidth"] for t in m["tables"]] or [0]) or "", m["layoutWidth"] if m["docOverflow"] else "ok"])
table(["Page", "Words above fold", "Numbers above fold", "Words on page", "Screens tall", "Tap targets under 44 px / all", "Under 24 px", "Words under 12 px",
       "Tables wider than screen", "Widest table (px)", "Page width (px)"], rows)

out.append("### T2. Words above the fold by viewport (running scenario)\n")
W = [375, 390, 430, 768, 1280, 1440]
table(["Page"] + [str(w) for w in W], [[p] + [by[(p, "running", w)]["metrics"]["foldWords"] for w in W] for p in PAGES])

out.append("### T3. Horizontal overflow of the page itself (layout width against the screen)\n")
rows = []
for r in R:
    m = r["metrics"]
    if m["docOverflow"]:
        rows.append([r["tag"], m["vw"], max(m["layoutWidth"], m["docScrollWidth"]), "; ".join(f'{o["sel"]} "{o["text"][:18]}" ends at {o["right"]}' for o in m["offscreen"][:3])])
table(["Page-viewport", "Screen", "Page width", "Elements past the right edge"], rows)

out.append("### T4. Layout shift on load (CLS; good is under 0.1)\n")
table(["Page"] + [str(w) for w in W], [[p] + [by[(p, "running", w)]["metrics"]["cls"] for w in W] for p in PAGES])

out.append("### T5. Tap targets under 44 px at 390 (most common)\n")
rows = []
for p in PAGES:
    m = by[(p, "running", 390)]["metrics"]
    c = collections.Counter((t["sel"], f'{t["w"]}x{t["h"]}') for t in m["tapSmall"])
    rows.append([p, len(m["tapSmall"]), ", ".join(f"{s} {sz} x{n}" for (s, sz), n in c.most_common(4))])
table(["Page", "Count", "Which"], rows)

out.append("### T6. Tables at 390 (table width against the 324 px it has)\n")
rows = []
for p in PAGES:
    for t in by[(p, "running", 390)]["metrics"]["tables"]:
        rows.append([p, t["text"][:28], t["scrollWidth"], t["clientWidth"], f'{t["colsShown"]} of {t["cols"]}', t["rows"]])
table(["Page", "Table starts with", "Table width", "Visible width", "Columns shown", "Rows"], rows)

out.append("### T7. axe-core (WCAG 2 A/AA, 2.1 AA, 2.2 AA), all viewports\n")
ax = collections.defaultdict(lambda: [0, set(), ""])
for r in R:
    for a in r["axe"]:
        ax[a["id"]][0] += a["n"]; ax[a["id"]][1].add(r["page"]); ax[a["id"]][2] = a["help"]
table(["Rule", "Meaning", "Nodes (summed over viewports)", "Pages"], [[k, v[2], v[0], ", ".join(sorted(v[1]))] for k, v in ax.items()])

out.append("### T8. Keyboard focus (45 Tab presses per page at 390 and 1440)\n")
table(["Page-viewport", "Stops", "Stops with no visible ring on the element"], [[r["tag"], r["focus"]["tabbed"], r["focus"]["noRingCount"]] for r in R if r["focus"]])

out.append("### T9. Empty scenario (new run, nothing traded), 390\n")
table(["Page", "Words above fold", "Words on page", "Screens tall"], [[p, m["foldWords"], m["pageWords"], m["screens"]] for (p, s, w), r in by.items() if s == "empty" and w == 390 for m in [r["metrics"]]])
(src / "tables.md").write_text("\n".join(out))

# screenshots: webp, phone folds and full pages, desktop folds
keep = []
for p in PAGES:
    keep += [(f"{p}-390x844-fold", 78), (f"{p}-390x844-full", 60), (f"{p}-1440x900-fold", 60)]
keep += [(f"{p}-empty-390x844-fold", 70) for p in ["home", "trades", "tape", "report", "engine", "gate"]]
keep += [("home-768x1024-fold", 60), ("configure-375x812-full", 60), ("new-run-375x812-fold", 75), ("tape-430x932-fold", 70)]
total = 0
for name, q in keep:
    im = Image.open(src / "shots" / f"{name}.png").convert("RGB")
    if im.height > 5000: im = im.crop((0, 0, im.width, 5000))
    f = dst / f"{name}.webp"; im.save(f, "WEBP", quality=q, method=6); total += f.stat().st_size
print(len(keep), "screenshots", round(total / 1e6, 2), "MB")
