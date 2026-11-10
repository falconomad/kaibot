# Design R1: Data-viz & Information-Density Specialist

## 1. Encoding principles
1. **One limit, one meter.** A bullet bar: the actual value as a fill, the limit as a hard tick, a warn tick at 80% of the limit (the PRD's 20% band).
2. **Colour means state only.** Neutral below 80%, amber from 80%, red at the limit. P&L always carries a sign and an arrow, never colour alone.
3. **Provenance is encoded.** Calculated values use tabular monospace with a `Py` or `Alpaca` tag. AI text sits in a tinted, proportional-type panel, and every number in it is a link.
4. **Stale means 40% opacity, a clock glyph and an as-of stamp.** Never hidden.
5. **Position over area.** No pies, donuts, gauges, 3D or gradients.
6. **Labels sit on the marks:** value, limit and headroom.
7. **Right-align numbers and align decimals.**

## 2. Viz specs
- **Limit meters (10/30/2/1.5/8%):** bullet bar scaled from 0 to 125% of the limit, labelled "Open risk 1.42% / 2.00% · 0.58% left". Header: 60 px mini-meters with numbers. After-trade ghost segment: "1.42 → 1.91%".
- **Allocation:** sorted bars (Stock / Sector / Theme tabs), each with a limit line and headroom; core and satellite in two tints.
- **Core/satellite:** one 100% stacked bar with a target tick at 70, labelled "Core 73.4% · target 70% · +3.4 pp".
- **Bull/base/bear + valuation:** a strip on a price axis. Bear-to-bull is a line, base a dot, the DCF range a band and the current price a rule, each with its price and upside %. Flip assumptions sit below; the footer gives model version and data date.
- **Trade ladder:** a vertical, to-scale ladder of target, entry, stop and the last price (with timestamp). The risk zone is red-tinted and the reward zone neutral. Distances in $ and %; "R:R 2.4".
- **Thesis status:** a chip combining shape, colour and word (● Intact, ◐ Under pressure, ✕ Invalidated, ? Needs review), plus "reviewed 12 d ago", which turns amber after 30 days.
- **Correlation:** one number on a 0–1 dot strip with a tick at 0.7: "⚠ 0.78 vs NVDA". No matrix.
- **Evidence:** each row shows the claim, source, CET time, age and an "n outlets" badge that expands to list them. The header reads "4 independent sources (11 articles)".
- **Staleness:** "08:12 CET · 3 min". It greys out when a price is older than 60 s, tactical inputs are older than 24 h, or core research is older than 30 days or past earnings.
- **€ / $ / FX:** € is primary, with $ muted below. P&L breaks down as "Total +€412 = price +€530 · FX −€118", and the rate shows as "1.0842 USD/EUR · 07:30 CET".

## 3. Tables
**Holdings:** Ticker · Sleeve · Thesis · Qty · Avg cost $ · Price $ · Value € · Weight % with a 10% micro-meter · Day P&L € · Total P&L € and % · FX effect € · Sector.

**Gate results:** Rule · Actual · Limit · Headroom · Mini-meter · PASS/WARN/FAIL (word and icon) · As-of. FAILs first, then WARNs. PASS rows collapse to "9 passed".

**Formatting**
- **€:** 0 decimals on overviews, 2 on Trade approval and Transactions. One locale (en-GB: €2,000.00).
- **$ prices:** 2 decimals (4 below $1).
- **Percentages:** weights to 1 decimal; risk values to 2 (0.50%). Weight changes in pp.
- **Signs:** always an explicit + or true minus (−). No parentheses.
- **Ratios and counts:** R:R to 1 decimal, correlation to 2, quantities as integers.
- **Timestamps:** 24-hour CET/CEST, with US market times converted. The audit trail uses ISO 8601 with the offset.

## 4. Hills I'll fight on
1. **Gate rows show actual vs limit, not ticks.** A pass at 98% of a limit is a different decision from a pass at 20%.
2. **Header meters always show numbers and turn amber before red.** A breach-only icon gives no warning.
3. **FX effect is its own signed number next to every € P&L.** Otherwise currency moves pass for stock-picking skill.
