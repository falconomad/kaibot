# Design R2: Data-viz rebuttal

## Concessions
- **UX lead:** no sparklines in the header. A fixed Approve with no hotkey and the gate before the opinion are right.
- **Visual designer:** no red/green, with crimson kept for breaches only. Mono `tnum` numbers, shape plus label on gate and thesis states.

## Pushback and compromises
**1. UX lead, "seven chips, value shown only on breach."** Breach-only gives no warning: 1.9% of 2% must be visible before it becomes 2.1%.
*Compromise:* 44px chips, no sparklines. Each chip shows the label, "1.42/2.00%" and a 3px under-bar with a limit tick. It turns amber at 80%, and on breach the whole bar turns red.

**2. Visual designer, blue/orange P&L.** Orange loss (#C2560C) sits next to warn amber (#A86A12), so "down today" will read as "near a limit".
*Compromise:* blue/orange appears only on P&L text and its ▲/▼. Meters and gate rows use only neutral, amber and crimson. Loss moves towards a desaturated rust, checked under deuteranopia and greyscale.

**3. Visual designer, serif AI prose at 15/24.** Good cue, but it wraps badly in a narrow rail.
*Compromise:* keep the serif at 14/21 in the rail, with every quoted number in mono and linked.

**4. UX lead, collapsed AI commentary on Trade approval.** Fully collapsed hides the challenge the PRD wants Claude to make.
*Compromise:* it stays below the gate, with an always-visible two-line summary: the call, the strongest counter-argument and "n independent sources".

**5. UX lead, 380px AI rail.** 1440 − 200 nav − 380 rail leaves 860px, but the 12-column Holdings table needs about 1,000px.
*Compromise:* the rail is closed by default on My Portfolio and Trade approval, and the nav collapses to 64px icons while the rail is open. Below 1,000px, FX effect and Sector move to a row expander.

**6. Visual designer, "% to 1 decimal" and as-of on hover.** 1.95% and 2.00% are different decisions; a hidden age is a hidden risk.
*Compromise:* weights to 1 decimal and risk values to 2. The relative age ("3 min") is always visible; the absolute CET time shows on hover.
