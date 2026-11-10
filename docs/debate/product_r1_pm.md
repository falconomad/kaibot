# Kaibot: PM position memo, round 1

## 1. Job-to-be-done
"When I have cash to deploy or a position moves, help me make one sound, rules-checked decision in under 10 minutes, and remember why I made it."

Most frequent decisions:
1. **Approve or reject a proposed tactical trade** (daily).
2. **Add, trim or hold a core holding**, e.g. "another €2,000 to Nebius?" (weekly).
3. **Is this thesis still intact?** (on earnings or material news).

## 2. Screens

**Command Centre**
- MUST: equity and day/total P&L (USD, with € at a daily FX rate); pending-approvals count as the primary CTA; active alerts (gate breaches, thesis "Needs review", drawdown, $150 budget); kill switch always visible; AI chat panel grounded in actual holdings (the Nebius flow).
- SHOULD: core/satellite split vs 70/30 target; latest 3 research notes.
- WON'T: market-summary news feed, "top opportunities" widget (it lives in Discover), customisable widgets.

**Discover & Research**
- MUST: screener with ~6 fixed filters (sector, market cap, P/E, revenue growth, % off 52w high, avg volume); company page with 5y key financials and bull/base/bear, every claim sourced, timestamped and wire-deduplicated; "Save as thesis"; archive searchable by ticker.
- SHOULD: one Python valuation model (simple DCF); weekly scheduled refresh for the watchlist.
- WON'T: free-form AI discovery ("find me the next Nvidia"), semantic-search UI, custom screener builder, multiple valuation models, charting beyond a price line.

**My Portfolio**
- MUST: holdings synced from Alpaca (qty, cost, value, P&L, weight, sleeve); transactions; allocation by stock and sector with 10%/30% limit lines; one thesis per core holding with 4-state status and last-reviewed date; drawdown vs 8% alert.
- SHOULD: manual theme tags; performance vs one benchmark.
- WON'T: correlation matrix, factor exposure, tax lots, full multi-currency accounting, performance attribution.

**Trade approval**
- MUST: setup card (ticker, entry, stop, target, qty, max loss in $ and % equity, R:R), every number Python-derived with a provenance tag; each risk gate as a pass/fail row showing the rule and actual value; Approve/Reject (reason required on reject); nothing submits without approval; active paper orders with cancel; post-execution reconciliation status.
- SHOULD: Claude rationale panel (explains, cannot alter numbers); proposal expiry that forces re-pricing when stale.
- WON'T: in-app order editing (reject and re-propose), order types beyond stop + target bracket, batch approve, mobile approval (Umbrella push comes later).

## 3. v1 success metrics
1. **Decision speed:** median proposal-to-decision < 10 min; Nebius-style question answered in < 90 s.
2. **Gate integrity:** 100% of submitted orders have an approval record and a logged gate pass; 0 order values with AI provenance.
3. **Reconciliation:** 100% of executions reconciled with Alpaca within 60 s; zero unexplained position drift.
4. **Thesis hygiene:** ≥ 90% of core holdings have a thesis reviewed in the last 30 days.
5. **Cost:** Claude spend ≤ $120/month at normal use, with cost per decision tracked.

## 4. Hills I'll fight on
1. **Approval queue first, dashboards last.** Month 1 strips bloodbath's auto-submit and LLM-percentage sizer, replacing them with the deterministic stop-distance gate and approval screen. Everything else is decoration. *Reason: it's the one non-negotiable the current code violates, and every other screen depends on it.*
2. **Orchestrator plus 3 specialists in v1, not 6.** Ship research, technical and risk-explainer only; news folds into research, and strategy/portfolio-manager wait for v2. *Reason: each agent is a prompt, an eval set and a cost line that a 3-hour-a-day developer must maintain, all on $200/month.*
3. **No friction beyond the deterministic gate.** If gates pass, approval is one click: no cooling-off timers, mandatory checklists or typed confirmations. *Reason: this is paper money, and friction the user learns to click through is worse than none. The gate already enforces discipline, so the UX should not have to.*
