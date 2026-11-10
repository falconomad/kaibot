# Kaibot: PM rebuttal, round 2

## (a) Concessions
- **Advocate, €:** € becomes the primary display, with the FX effect shown separately and the rate timestamped (also Risk's point).
- **Both, adjust-and-recheck:** I drop "reject and re-propose". Qty and stop get steppers that re-run the Python gate. No free text (Risk's hill 3).
- **Risk, persistent header in v1:** PAPER badge, kill switch, AI spend, last reconcile, stale values greyed out.
- **Advocate, the AI commits to a call:** add, hold, trim or pass, plus the condition that kills it. MUST for the chat panel.

## (b) Pushback
- **Advocate, correlation clusters and currency exposure in v1: no.** They need a returns pipeline, a lookback choice and a clustering method. Weeks of work; v1.1. Saved screens and a user-chosen benchmark are also out.
- **Risk, rationale as a precondition for Approve: no.** It ties order safety to the research pipeline, so a news outage would block a trade that cleanly passed the gates. The rationale is shown but gates nothing.
- **Risk, cost on every agent run: no.** One budget page. Only the $150 and $190 thresholds surface in the UI.

## (c) Compromises
1. **Lunch approval vs out-of-hours: "approve to stage".** A 12:30 approval records intent, and nothing goes to Alpaca. At open plus 5 minutes, Python re-prices (quote <60s old) and re-runs every gate. It submits only if the gates pass, entry is within ±0.5% and qty is unchanged or lower. Otherwise the proposal goes to "Needs re-approval". No order reaches the broker out of hours.
2. **Portfolio fit and catalysts: partly.** Post-trade exposure (position %, sector %, open tactical risk vs 2%) is a MUST on the approval card. Discover shows headroom under the 10% and 30% caps. The catalyst calendar covers earnings dates only, for holdings and the watchlist, in CET. No macro events.
3. **Soft warnings vs one tap: split by state.** A clean pass is one tap. A rule within 20% of its limit (Advocate's threshold) needs one checkbox per warning (Risk's requirement), never typed confirmation. A hard fail disables Approve. No approving from notifications.
4. **3 vs 6 agents: six functions, three LLM agents.** The risk officer is deterministic Python under the non-negotiables, and portfolio exposure maths is Python too. News folds into research. That leaves an orchestrator plus research, technical and a risk explainer. Strategy waits for the v2 backtester.
