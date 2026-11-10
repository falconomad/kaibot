# Position memo: Investor / User Advocate (Round 1)

## 1. A day in my life
- **07:40, before work (3 minutes).** What changed overnight in *my* holdings? Pre-market moves, today's earnings and macro events, theses that flipped to Under pressure, alerts I must act on. If nothing needs me, say so in one line.
- **12:30, lunch opportunity.** A semis breakout gets flagged, and the US opens at 15:30 CET. In under 2 minutes I need the risk-gate result, the effect on my semis exposure, entry/stop/target/qty in € and $, and the AI's reasoning. I approve now, and the order is staged for the open with a price re-check.
- **21:00, evening review.** Fills, stops hit, core vs satellite P&L, theses due for review, tomorrow's catalysts. When I ask "Should I add €2,000 to Nebius?" I want a decision, not a lecture.

## 2. What I need on each screen (ranked)
**Command Centre**
1. "Needs you" queue: approvals, triggered alerts and thesis changes, sorted by urgency.
2. Today's catalysts for holdings and watchlist, with times in CET.
3. Overnight changes, one line each, linked to evidence.
4. Value and day P&L in €, FX effect shown separately; core vs satellite.
5. AI panel, one click away.
Cut: market heatmaps, tickers for indices I don't hold, "AI confidence" gauges.

**Discover & Research**
1. Portfolio fit on every idea: sector and theme overlap, correlation with my top holdings, room left under the 10% and 30% caps.
2. Bull/base/bear, each with the assumption that would flip it, plus a valuation range.
3. Timestamped sources with a de-duplicated evidence count.
4. Saved screens, and an archive I can search by ticker and date.

**My Portfolio**
1. Theses: status, a one-line reason, date last reviewed.
2. Exposure by sector, theme, currency and correlation cluster. That's real concentration, not ticker weights.
3. Open tactical risk against the 2% budget, as one bar.
4. Performance against a benchmark I choose, core and satellite separately.
5. Transactions linked to the decision record behind them.

**Trade approval**
1. One card: entry, stop, target, qty, max loss (€ and % of equity), R:R, all labelled as computed in Python.
2. Gate results per rule: the value against the limit.
3. Exposure after the trade.
4. AI rationale in three bullets, plus the strongest counter-argument.
5. Approve / Reject / Adjust-and-recheck; one tap to log why I rejected.
6. Active paper orders, live status, one-tap cancel.

## 3. Pain points Kaibot must fix
- Broker apps show P&L, not exposure. I only find out I'm 45% semis after buying.
- Everything is in USD, so FX hides my real return.
- Feeds repeat one wire story ten times and call it momentum.
- The bloodbath bot sized positions from an LLM's guess and traded without approval. I learned nothing from it and trusted nothing.
- AI that answers "it depends, consult a professional."
- Alerts fire on a 3% dip but miss the earnings date.
- No memory of why I bought something six months ago.

## 4. Hills I'll fight on
1. **The AI commits to a call (add / hold / trim / pass), with a confidence level and the condition that would kill it.** The deterministic gate already provides the safety, so hedging on top of it is just noise.
2. **When the gate passes cleanly, approval is one screen and one tap.** No cooling-off timers or retyped confirmations. Extra steps only when a rule is within 20% of its limit.
3. **Portfolio fit and the catalyst calendar are v1, not "later".** They're why I'd open Kaibot instead of my broker app.
