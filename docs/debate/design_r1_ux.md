# Design R1: UX Lead position memo

## 1. App shell (1440px)
- **Safety header (G1):** fixed at the top, 44px, never collapses. It holds seven chips. The bar turns red on a breach, and the breached chip expands to show its value against the limit. Clicking a chip deep-links to its source.
- **Left nav rail, 200px, labelled:** Command Centre (with a "Needs you" count), Discover & Research, My Portfolio, Trade approval (with a pending count), then Budget and Settings. Four destinations don't need a hamburger menu.
- **AI panel:** a docked right rail, 380px, collapsible ("/"). It reflows the page and never overlays it. It's open by default on Command Centre and closed on Trade approval. It has a tinted surface labelled "AI analysis" (G4).

## 2. Screens
**Command Centre.** The main column holds the "Needs you" queue (type, ticker, one-line reason, age, "Review"), then the briefing (overnight changes, catalysts in CET), then macro dates. The secondary column holds € value and P&L, the core/satellite bar against 70/30, 3 candidates labelled "Candidate, not a recommendation", and the latest notes. **Primary action:** review the top queue item.

**Discover & Research.** The 6 filters sit on one row. Results take the left 40%. The report takes the right 60%, with tabs in a fixed order: Portfolio fit → Bull/Base/Bear → Valuation → Financials → Evidence. A staleness strip with "Refresh and continue" sits above the tabs. **Primary action:** "Save as thesis". "Draft trade" is secondary.

**My Portfolio.** A reconciliation banner when there's a mismatch (not dismissible), then a risk strip (tactical risk against 2%, drawdown against 8%), then the holdings table with thesis status inline, then allocation with limit lines, then transactions linked to their decisions. **Primary action:** "Review thesis" on rows that aren't Intact.

**Trade approval.** The left 65% runs top to bottom: setup card (each value tagged Python with a timestamp), qty and stop steppers, gate rows (actual against limit), exposure after the trade, then collapsed AI commentary. The right 35% is a sticky Decision panel: countdown, Approve, and Reject reason chips. Active orders sit on a tab.

**Approve states.** The button stays in a fixed position throughout:
- **Clean:** "Approve: buy 42 NBIS ≤ $61.20". One click.
- **Warning:** the list of warnings plus one checkbox, "I've read all 3 warnings". Approve enables once it's ticked.
- **Hard fail:** disabled, and the button text names the failing rule. Adjust or Reject only.
- **Expired:** the button becomes "Recheck prices" and re-runs the gate.
- **Staged:** "Approve conditionally", showing max entry, qty and loss. Afterwards it becomes a "Fires 15:35, lapses 16:00 CET" card with Cancel.
- **Transitional:** Submitting → Submitted → Reconciled ✓, or a mismatch alert.

## 3. Flows
**(a) 07:40.** Glance at the header, read the queue, skim the briefing. No scrolling at 1440×900.

**(b) Clean pass (under 2 minutes).** Queue → "Review" → read the setup card and confirm the gate is green → Approve → a toast linking to the order. Two clicks.

**(c) Warning.** Amber gate rows pin to the top of the gate list, and the Decision panel lists each warning against its limit. Tick once, then Approve. The acknowledgement is audited. There's never a checkbox per warning.

**(d) Lunch (staged).** Review the Python-derived limits (read-only) → Approve conditionally → a staged card appears in the queue and in Active orders. A push at 15:35 reports the result. If the order cancelled, the item returns to "Needs you" as "Needs re-approval", with the changes in red.

## 4. Hills I'll fight on
1. **Approve never moves, never hides, and has no hotkey.** Muscle memory plus a shortcut gets trades approved without being read.
2. **The AI rail reflows the page, and on Trade approval the commentary sits below the gate.** The user reads the verdict before the opinion, and nothing covers calculated values.
3. **The header stays as seven compact chips, with no sparklines.** It's a smoke alarm. Density belongs in the screens.
