# Design R2: UX Lead rebuttal

## Concessions
- **Data-viz is right about amber before red.** My breach-only header was too binary.
- **Adopted from Visual:** light default, Approve in sea rather than green, a hatched and locked disabled Approve, and crimson for breaches only.
- **Adopted from Data-viz:** the trade ladder, and FAILs then WARNs sorting first. PASS rows collapse into an expandable "9 passed". That's safe because anything within 20% of a limit is already a WARN.

## Conflicts and compromises
**1. Header: Data-viz's mini-meters and Visual's ink strip.** Seven 60px meters won't fit beside PAPER and the kill switch. And a dark strip on a light app reads as browser chrome, which leaves breach-red without a contrast jump.
*Compromise:* a 44px header on the surface colour with an ink bottom rule. Each chip reads "1.42 / 2.00%" in mono, with a 2px bullet underline as the meter, amber at 80% and red at the limit. The whole bar fills crimson on a breach or kill switch. The night theme has a dark header.

**2. Holdings: Data-viz's 12 columns.** About 860px of content width means horizontal scroll, and that hides P&L.
*Compromise:* 8 columns: Ticker · Sleeve · Thesis · Value € · Weight with micro-meter · Day P&L € · Total P&L € with FX effect as a second line · Sector. Qty, Avg cost and Price $ go in the row expansion. The AI rail is closed by default on Portfolio.

**3. Visual's serif AI prose.** A book serif reads as authority, which is the opposite of "this is an opinion".
*Compromise:* serif only in the AI rail and the case narratives. The AI's call (Add/Hold/Trim/Pass) is in sans caps under the "AI analysis" eyebrow. No serif on Trade approval.

**4. Data-viz's evidence density.** Six fields per row turns the Evidence tab into a log file.
*Compromise:* one line per item: claim · source · age · "n outlets". The CET time shows on hover and in the expansion. Show 5 items, then "Show all". The tab label reads "Evidence (4 independent)".
