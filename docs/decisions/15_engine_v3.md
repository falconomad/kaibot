# Decision 15: Engine v3 (multi-day core, one code path, honest evidence)

- **Status:** decided by Product on 12 Nov 2026 under the stakeholder's mandate of the same day: "take a look at our
  engine ... do some research how the major algo traders ... think deep and make our engine really, really good, and do
  whatever is necessary and make any code changes that you need ... make those architectural decisions yourself and
  rebuild your architecture. Come with a plan, create extra milestones and stories and tasks, and start implementing."
- **Goal confirmed by the stakeholder on 12 Nov 2026** (section 1): "market-like return with smaller drawdowns, proven
  honestly ... it's enough."
- **Milestone:** M7 Engine v3 (GitHub milestone, stories S71-S93).
- **Evidence behind it:** `docs/research/engine_v3/report.md` (124 sources), `docs/research/engine_v3/code_audit.md`
  (file-level findings), `docs/m0/walk_forward_10y.md`, `docs/m0/experiments_001.md`.
- **Supersedes:** the "engine changes are not approved yet" line of decision 14. Decision 14's risk rules stay, with one
  amendment (earnings, section 6). Decision 11 (local-first engine) stays.

## 1. What we promise, and what we do not

No engine can "always profit". SPY rose +209% over the test window while the dip rule lost 59%, mostly because the rule
sat in cash every night and paid costs every day. What Engine v3 can honestly aim for:

1. **Be invested in rising stocks** (capture the market's return instead of missing it).
2. **Lose much less than the market when it falls** (trend filter, risk-sized positions, exposure cap).
3. **Add a small edge only where decades of published evidence support one**, proven on ten years of our own data.
4. **Never fail operationally** (same code in test and live, reconciled books, alarms, kill switches).

Research estimate for the best long-only, unlevered portfolio: within about two percentage points a year of SPY, with
shallower drawdowns. "Nothing beats holding SPY" is a legitimate finding and will be reported as such.

A rule trades on paper only after it passes Gate B (section 7). Real money stays the stakeholder's decision.

## 2. Diagnosis in one table

| Cause | Evidence | Fix in v3 |
|---|---|---|
| Liquidity-taking on a liquidity-provision signal | gross P&L -$4,139 on 10,124 trades; costs -$83,439 | slow rules; auction and limit orders |
| Horizon too short for the cost | one round trip a day per slot costs about 0.5 Sharpe a year; single rules earn 0.2-0.4 | holds of weeks; costs about 0.15-0.25% of the book a year |
| Tight stop on a mean-reversion entry | 45% of trades stopped at -1.08R; median stop 63 bp | exits by rule; a far broker stop as insurance only |
| Flat every night | stock reversal and the equity premium are earned overnight | multi-day holding (decision 14) |
| One rule, one tuned setting, 17 months of design data | 8,286 configurations tried | pre-registration, frozen published settings, ten years |
| Six simulators, 14 live/replay divergences | audit A2-A4 | one decision core, `SimBroker` |
| No portfolio layer | five dip trades = one bet | portfolio builder with risk sizing and exposure caps |
| Evidence holes | IEX vs SIP, survivorship, uncalibrated costs, wrong trial count | point-in-time data, cost model v2, gate v2 |
| Manual operations | manual start, no alerts, `git pull` at start | always-on, alarms, pinned releases |

## 3. Architecture

One pure decision core; only the edges are swapped between the ten-year test and live paper trading.

```
Clock event -> MarketData (as of now) -> each Strategy due -> Signals / targets
            -> PortfolioBuilder -> target positions per (strategy, symbol)
            -> risk gate (every order) -> ExecutionPolicy + OrderManager -> Broker (AlpacaPaper | SimBroker)
            -> fills -> Ledger -> local journal -> (async) Supabase -> dashboard, evidence gate
```

| Part | Responsibility | Built from | Replaces |
|---|---|---|---|
| `Clock` + runner | one event loop: pre-open, session open, bar close, pre-close, session close, after-close daily job; `LiveClock`, `SimClock` | `engine/__main__.py`, `Engine.tick`, `tests/fakes.Clock` | the minute loop in `replay/sim.py`, per-script drivers |
| `MarketData` | point-in-time reads: bars (1m, 1d) as of a time, quotes, calendar, corporate actions, earnings dates; historical and live implementations | `replay/data.py`, `engine/market.py` | four bar representations |
| `Strategy` | `name`, `schedule`, `holding` (intraday or multi-day), `warmup`, `on_event(ctx)`; returns signals (intraday) or target lists (multi-day); owns its exit rules; no order code | shadow strategy interface | `Engine._entries`, `_spy_momentum`, `_trail_stops` |
| `PortfolioBuilder` | signals + book + limits -> target positions per sleeve: volatility sizing, per-name, gross, overnight and sector caps, index trend filter as an exposure dial, cash buffer, whole-share rounding | sizing arithmetic of `risk/gate.size_position` | "max positions" as the only portfolio rule |
| risk gate | unchanged role: pure, last check before any order; `Snapshot` gains gross, overnight and sector exposure and the gap-scenario loss | `risk/gate.py` | - |
| `ExecutionPolicy` + `OrderManager` | target changes -> orders; closing-auction, limit and marketable-limit orders for daily rules, brackets for intraday rules, standalone protective stops; intent written first; state machine with an `unknown` state; partial fills, rejects, replace, cancel; reconciliation from account activities | `Engine._open`, `_sync_orders`, `_adopt_orphans` | brackets as the unit of position |
| `Broker` | `submit`, `cancel`, `replace`, `open_orders`, `positions`, `account`, `activities`, `clock`, `calendar`; `AlpacaPaper`, `SimBroker` (enforces Alpaca's real order rules and the cost model) | `engine/alpaca.py`, `tests/fakes.FakeBroker`, `broker/shadow_costs.py` | `broker/base.py`, six simulators |
| `Ledger` | positions per (strategy, symbol), cash, daily mark-to-market equity, dividends and splits; state is a fold over a local append-only journal (SQLite), replicated to Supabase | `Pos`, `Day`, `store.ledger` | equity as the sum of `daily_result` rows |

Names may change in implementation; responsibilities may not. Two loops share the core:

- **Multi-day rules: target reconciliation.** Once per decision time compute target shares per symbol, subtract
  broker-confirmed positions and working orders, send only the difference. Self-healing after a missed fill or restart.
- **Intraday rules: signal-triggered brackets**, with explicit state recovery.

### Architecture decisions

| Decision | Choice | Why |
|---|---|---|
| Build or adopt | keep the custom engine, restructure as ports and adapters | NautilusTrader has no Alpaca adapter and is mid-rewrite; LEAN is C# with paid tooling and a fragile Alpaca path; copy their patterns, not their code |
| Decision time for daily rules | after the close on complete daily bars (free, SIP older than 15 minutes) | removes the IEX-vs-SIP divergence without paid data |
| Execution for daily rules | base: next session's closing auction (`cls`, whole shares, submitted before 15:50 ET; may be queued from 19:00 ET the evening before); alternative: marketable limit after 09:45 ET; no opening-auction orders | the close is the cheapest venue, the open the most expensive |
| Sizing | volatility: shares = equity x 0.001 / ATR(20), under per-name and gross caps; no leverage, no shorts | equal risk per position; gap risk absorbed by size |
| Stops | exits by rule; a wide protective stop rests at the broker (GTC, whole shares, re-armed before day 90) | tight stops subtract value; the broker stop protects when the engine is off |
| Source of truth | local journal first, Supabase asynchronously | a database outage must not interrupt stop management |
| Research | two tiers: fast vectorised screen ranks ideas; go/no-go numbers come only from the engine with `SimBroker` | a seventh simulator is forbidden |
| Hosting | the stakeholder's Mac, auto-started; daily rules need one short run a day (for example 08:00 Berlin); cloud stays the stakeholder's call (decision 11) | - |
| Share size | whole shares in v3.0; names whose single share exceeds the position size are skipped and logged; fractional mode later if the real pot is small | fractional orders are day-only at Alpaca (no `cls`, no resting GTC stop) |

## 4. What is kept, changed, deleted

- **Kept:** `risk/gate.py` and `HARD_CEILINGS`, tighten-only owner config, engine lease, fail-closed halts, idempotent
  order ids and orphan adoption, trial discipline (walk-forward, digests, deflated Sharpe), cost split, "report the
  worse", point-in-time features, request budgets, the paper-only guard, room isolation.
- **Changed for overnight holding (all in the release that enables the first overnight position):** flatten at 15:55
  only for intraday sleeves; daily stop on the day's change in mark-to-market equity (gaps count; a breach blocks
  entries and keeps stops); watchdog and database dead-man cancel entry orders and verify protective stops instead of
  liquidating multi-day sleeves; account guard by sleeve; start equity from the ledger.
- **Deleted when replaced:** `replay/sim.py` (`run_portfolio`, `random_entry_runs`), `replay/spy_momentum.py`,
  `replay/pipeline.py`, the counterfactual code in `engine/shadow.py` and `engine/roomflow.py`, `engine/run.py`,
  `docker/`, `apps/api`, `src/kaibot/store/`, `strategies/dip_reversal.py`, `broker/base.py`, unused packages and
  profile keys.

## 5. Candidates, in test order

Every candidate has a pre-registration (`docs/prereg/<rule>.md`: hypothesis, who is on the other side, frozen settings,
at most five neighbours for the plateau check, fill model, pass marks) committed **before** its first run. Expectations
are research estimates, net of costs, long-only, unlevered.

| # | Rule | Frozen construction | Expectation |
|---|---|---|---|
| 0 | First look (#113) | `trend_highs`, `rel_strength`, `winners_run`, `overnight_spy` as built | a first look on today's stock list and cost model v1; not a verdict |
| 1 | `trend_momentum` (core) | weekly; rank = annualised slope of a 90-day exponential regression x R-squared; exclude below the 100-day average or with a >15% one-day move in the window; ATR sizing at 10 bp of equity; buy only while SPY is above its 200-day average; sell when out of the top 20%, below the 100-day average or after a >15% gap; resize every second week | SPY -2 to +1 points a year; clearly shallower drawdown |
| 2 | `mom_12_1_band` | monthly; rank on months t-12..t-2; enter in the top 10%, exit below the top 20%; 10-20 names at equal risk; blend of 6, 9 and 12-month lookbacks; with and without the index filter | SPY -1 to +2 points a year; multi-year lags possible |
| 3 | `spy_noise_band` (long only, 1x) | published parameters: 14-day lookback, checks on the half hour from 10:00, exit at the close or below max(band, VWAP); fill at the next bar's open | 3-5% a year before SPY-specific costs; needs fills under about 0.5 bp a side |
| 4 | `spy_noise_band_carry` | hold an open long to the next open | adds about 2 points a year with gap risk |
| 5 | One-shots (#112) | opening-range breakout keep-or-delete; news continuation; one-week post-earnings window; pullback in an uptrend (RSI-2 type); all-time-high breakout (distribution over random signal subsets) | about zero; anything that passes is a bonus |

Retired: the intraday dip rule in every variant (it keeps running on paper only as a plumbing soak test until a
replacement passes). Not pursued: leverage, shorting, options, paid data.

Fill model for daily rules in tests: decide after the close of T; **base** fill at the close of T+1 at half a spread per
side; **stress** fill at the open of T+1 at twice the midday half-spread; a stop gapped through fills at the open.

## 6. Risk rules for multi-day holding (decision 14, confirmed and amended)

| Risk | Rule |
|---|---|
| Overnight gaps | volatility sizing (a 1-ATR day costs about 0.1% of equity per position); per-name cap from the level |
| Too much invested | gross and overnight exposure capped per level; never above 100% |
| Market downtrend | no new buys while SPY is below its 200-day average (per rule, pre-registered) |
| Engine off | protective GTC stop at the broker for every position; missed daily run raises an alarm |
| Earnings | **amended:** per rule and decided by the test (run "hold through" and "exit before" as the two pre-declared versions). A blanket "never hold through earnings" would force a sale every quarter for rules that hold for months |
| Daily loss stop | counts overnight gaps; blocks entries; open positions keep their stops |
| Pause, run drawdown end, "Flatten and stop" | unchanged; flatten sells everything |
| Owner settings (M5) | tighten-only: lower exposure, fewer positions, multi-day holding off |
| Corporate actions | protective orders cancelled and re-created around splits; dividends booked |
| Runaway orders | hard caps on orders per minute and open notional outside strategy code; a breach halts |

Level limits for multi-day books (exposure cap and run-drawdown end per level) are proposed by Product with the first
ten-year results of rule 1 and confirmed by the stakeholder together with the first paper start. Until then tests run at
Balanced with a 100% exposure cap and report 30%, 60% and 100%.

## 7. Evidence gate v2

The M4 bar (900 trades, per-trade t above 3, intraday random baseline) cannot judge slow rules: a weekly rule would need
4 to 14 years of paper trading. It stays for intraday rules. Multi-day rules pass four gates.

| Gate | Pass marks (pre-declared here; `config/gate_v2.yaml` is hashed into every report) |
|---|---|
| A. Reason | pre-registration committed before the first run; published evidence for the family; frozen settings; <= 5 neighbours |
| B. Ten-year test (engine + `SimBroker`, out of sample 2017-05 onward) | all of: (1) net return positive overall and in at least 6 of 10 calendar years; (2) **either** CAGR >= SPY total-return CAGR - 2 points with max drawdown <= 0.75 x SPY's, **or** CAGR >= SPY + 1 point with max drawdown <= 1.1 x SPY's; (3) net Sharpe at or above the 90th percentile of matched random entries (same universe, liquidity filter, exposure and holding-period distribution, >= 500 runs); (4) marks 1-2 still hold at 2x costs (3x and break-even reported); (5) holds on the point-in-time universe, not only on today's list; (6) every pre-declared neighbour passes mark 1 |
| C. Paper incubation | >= 3 months and >= 60 sessions; ledger equals broker every day; live daily returns track the `SimBroker` replay of the same days; live drawdown inside the 95% block-bootstrap band of the test; retirement trigger (99% band, or tracking outside tolerance for 10 sessions) written before the start |
| D. Real money | the stakeholder's decision; starts small |

Statistics are computed on **daily mark-to-market returns** (not per trade): Sharpe with Newey-West standard errors,
alpha against SPY total return, deflated Sharpe against one trial registry (configurations, not rows), a 50% haircut
quoted beside every backtested Sharpe. A backtested Sharpe above about 1.0 for a long-only stock rule is treated as a
warning sign. Until S71 (cost model v2), S83-S85 (data repairs) and S75 (`SimBroker`) land, results from the research
harness of #113 are labelled **provisional** and cannot pass Gate B.

## 8. Order of work (the paper engine keeps running after every step)

| Wave | Stories | Outcome |
|---|---|---|
| 1 | #113 first look; S74 quick fixes; S75 broker protocol + `SimBroker` + `SimClock`; S83 point-in-time universe; S84 raw daily bars, corporate actions, total return; S86 gate v2; S89 dashboard design | first multi-day numbers; honest data; the spine starts |
| 2 | S87 momentum core tests; S88 SPY noise band; S71 cost model v2; S85 earnings dates; S76 strategy interface; S82 hygiene; S81 always-on and alerts | Gate B verdicts on rules 1-4; rules become plug-ins |
| 3 | S77 ledger and journal; S78 order manager and execution; S79 portfolio builder; S80 overnight-safe fail-safes; S72 one-shot tests; S90 dashboard build | an engine that can hold positions for weeks |
| 4 | S91 agent room pre-open review; S92 settings for multi-day holding; S93 first rule on paper (incubation plan) | paper incubation starts |

## 9. Who decides what during M7

- **Product decides and merges** (two keys Backend-quant + QA-Ops on risk, sizing and broker code; green CI; outside
  09:30-16:00 ET): everything in this decision, including engine, broker and risk-gate changes on **paper**, and the
  paper start of a rule that has passed Gate B. Each such merge is reported in the daily status.
- **Only the stakeholder:** production database migrations (written as files, applied on "apply"), paid services, the
  agent room on, the cloud move, real money, the level limits of section 6.
- Unchanged everywhere: paper only; no LLM output sets price, size, stop or exit; profiles and owner settings only
  tighten `HARD_CEILINGS`; free data only; one engine at a time.
