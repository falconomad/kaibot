# Kaibot v1 PRD: final decisions from the product team
Team: Product Manager · Investor/User Advocate · Risk & Compliance Officer. Two rounds (positions, then rebuttals), resolved by the facilitator.

## Job-to-be-done
"When I have cash to deploy or a position moves, help me make one sound, rules-checked decision in under 10 minutes, and remember why I made it."
Most frequent decisions: (1) approve or reject a tactical proposal (daily); (2) add, trim or hold a core holding (weekly); (3) is this thesis still intact? (on earnings or material news).
Moments of use: a 3-minute check at 07:40 CET; a lunch opportunity before the US opens at 15:30 CET; an evening review at 21:00.

## Global (every screen)
- G1 Persistent safety header: PAPER badge with account ID · kill switch (state + one-click engage) · AI spend vs $150 alert / $190 hard stop · last Alpaca reconciliation time and status · daily loss vs 1.5% · open tactical risk vs 2% · drawdown vs 8%. Turns red on breach.
- G2 € is the primary currency, with the USD value and FX effect shown separately. The FX rate is timestamped.
- G3 Every number shows its as-of time and provenance (Python calculation / Alpaca / source). Stale values grey out.
- G4 AI output is labelled "AI analysis" and kept visually separate from calculated values. Every number it quotes links to its source or calculation.

## 1. Command Centre: MUST
- "Needs you" queue as the primary element: pending approvals, triggered alerts and thesis changes, sorted by urgency. Shows "Nothing needs you today" when empty.
- Briefing scoped to holdings and watchlist: overnight changes (one line each, linked to evidence) and today's catalysts in CET (earnings plus company announcements). No market-wide news feed.
- Portfolio value and day/total P&L in €, with core vs satellite split against the 70/30 target.
- 3 pre-checked candidates, read-only and labelled "Candidate, not a recommendation", linked to Discover.
- AI panel one click away: answers commit to a call (Add / Hold / Trim / Pass) with a kill condition, the strongest counter-argument, the count of independent sources and a confidence band in words (no percentages). It can open a Trade approval draft but can never size or submit orders.
- SHOULD: latest 3 research notes; key macro dates (central-bank meetings, CPI).
- WON'T: heatmaps, index tickers for indices not held, customisable widgets, AI confidence gauges.

## 2. Discover & Research: MUST
- Screener with 6 fixed filters (sector, market cap, P/E, revenue growth, % off 52-week high, average volume). Results are labelled "candidate".
- Portfolio fit on every idea: sector and theme overlap; headroom under the 10% position and 30% sector caps; correlation with the top 5 holdings (Python, 1-year daily returns, one number, warning above 0.7).
- Company report: 5 years of key financials; bull/base/bear cases, each with the assumption that would flip it; valuation range (one Python DCF) showing inputs, model version and data date.
- Evidence: every claim has a source and timestamp. Items that repeat one wire story collapse into one entry with an "n outlets" count.
- Staleness by sleeve: core research is valid for 30 days or until the next earnings report; tactical needs news and technicals less than 24 hours old. Stale items show a "Refresh and continue" button.
- "Save as thesis", and an archive searchable by ticker and date.
- WON'T (v1): free-form AI discovery, a semantic-search UI, a custom screener builder, multiple valuation models, a correlation matrix.

## 3. My Portfolio: MUST
- Holdings synced from Alpaca: qty, cost, value, P&L, weight, sleeve. Reconciliation mismatches show as a banner that is never hidden.
- Allocation by stock, sector and theme (manual tags), with the 10% and 30% limit lines and the headroom left.
- Open tactical risk shown as one bar against the 2% budget. Drawdown against the 8% alert.
- One thesis per core holding: 4-state status (Intact / Under pressure / Invalidated / Needs review), a one-line reason, the date last reviewed, and who changed it and why. Agents can only suggest a status change; the user confirms it. A price drop alone never sets a thesis to Invalidated.
- Transactions, each linked to the decision record behind it.
- SHOULD: performance against one benchmark, core and satellite separately.
- WON'T (v1): correlation-cluster and currency exposure views, factor exposure, tax lots, attribution.

## 4. Trade approval: MUST
- Setup card: symbol, side, limit order with stop and target bracket, qty, entry, stop, target, max loss (€, $ and % of equity), R:R. Each value is tagged "calculated by Python" with its data timestamp. Size comes from the stop distance (0.5% equity rule).
- Gate results: the Python verdict exactly as returned, one row per rule showing the actual value against the limit (stale price, duplicate, short, market hours, position/sector/aggregate/daily-loss limits, kill switch, reconciliation OK). Claude's commentary sits in a separate, labelled panel and cannot change the verdict.
- Exposure after the trade: position %, sector %, open tactical risk, daily-loss headroom.
- Approval states:
  - Clean pass: one tap to approve.
  - Warning (a rule within 20% of its limit, a soft warning, or a thesis that is not Intact): one combined acknowledgement that lists every warning.
  - Hard fail: Approve is disabled, with the reason shown. No override.
- Adjust and recheck: qty and stop steppers re-run the gate. No free-text numbers anywhere in the order payload.
- Reject requires a one-tap reason.
- Expiry: the server re-checks the computed-at time at submit (MUST). A visible countdown is a SHOULD.
- Staged (out-of-hours) approvals are conditional: the user approves limits (max entry = proposed ±0.5%, max qty, max loss). At open + 5 minutes, Python re-prices with a quote under 60 seconds old and re-runs every gate. It submits a limit order only if every limit holds and never resizes upward. Otherwise it cancels, notifies the user and marks the proposal "Needs re-approval". The approval lapses 30 minutes after the open. No order reaches Alpaca out of hours.
- Active paper orders with live status and cancel; reconciliation result after each execution.
- An audit record for every decision: input snapshot, gate version, user action, timestamp.
- The AI rationale is shown but never gates approval, so a research outage cannot block a trade that passed every gate.
- WON'T (v1): approving from notifications or the Android app (a push can only deep-link to this screen), batch approval, other order types.

## Agents in v1
Six logical functions, three LLM agents: an orchestrator plus research (news folded in), technical and a risk explainer. Portfolio maths and the risk officer are deterministic Python. The strategy agent waits for the v2 backtester. Cost appears on one budget page plus the header meter.

## Success metrics
1. Median time from proposal to decision under 10 minutes; a Nebius-style question answered in under 90 seconds.
2. 100% of orders have an approval record and a logged gate pass; 0 order values with AI provenance.
3. 100% of executions reconciled within 60 seconds; zero unexplained position drift.
4. At least 90% of core theses reviewed within the last 30 days.
5. Claude spend at or below $120 a month in normal use, with cost per decision tracked.

## Facilitator rulings on remaining disputes
- Correlation: one Python number per idea in v1 (Advocate) instead of none (PM). Daily bars are needed for technicals anyway, so it costs little.
- The staged approval lapses 30 minutes after the open (Risk), not at the close (Advocate). A tighter window means less gap risk.
- The visible countdown is a SHOULD, but the design team should include it (Risk + Advocate).
