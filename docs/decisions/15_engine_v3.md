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
- **Updated 19 Nov 2026 (section 12):** since 18 Nov 2026 daily rules send near-close DAY market orders on Alpaca
  paper, not closing-auction orders (#333, PR #337). The closing-auction text below is kept as history and marked.

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
| `ExecutionPolicy` + `OrderManager` | target changes -> orders; closing-auction, limit and marketable-limit orders for daily rules (since 18 Nov 2026 daily rules send near-close market orders: section 12), brackets for intraday rules, standalone protective stops; intent written first; state machine with an `unknown` state; partial fills, rejects, replace, cancel; reconciliation from account activities | `Engine._open`, `_sync_orders`, `_adopt_orphans` | brackets as the unit of position |
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
| Execution for daily rules | **Current, since 18 Nov 2026 (#333, PR #337; section 12):** near-close DAY market orders, whole shares, sent from 15:50 to 15:57 ET in the next session; no opening-auction orders. **Replaced on 18 Nov 2026, kept as history:** ~~base: next session's closing auction (`cls`, whole shares, submitted before 15:50 ET; may be queued from 19:00 ET the evening before); alternative: marketable limit after 09:45 ET~~ | the close is the cheapest venue, the open the most expensive. Replaced because Alpaca paper runs no closing auction: on 18 Nov 2026 it filled 7 of 30 `cls` buys (4 in full, 3 in part) |
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
This fill model is not changed by the near-close orders of 18 Nov 2026 (section 12).

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

## 10. Gate v2 clarifications (Product, 12 Nov 2026, settled before any rule was judged)

Raised by QA-Ops while building #126 (PR #140). The conservative reading is chosen each time.

| Question | Decision |
|---|---|
| Gate C tracking tolerance | Mean absolute difference between live daily returns and the `SimBroker` replay of the same days ≤ 10 bp per session, over a rolling 20 sessions |
| "Outside tolerance for 10 sessions" | 10 sessions in a row |
| Part-years in "6 of 10" | A partial calendar year counts only if it has at least 6 months of out-of-sample data. The threshold is then 60% of the counted years, rounded up |
| Mark 3 for SPY-only rules (no stock universe) | Random-timing control: the same number of SPY round trips at random decision times, with the rule's holding-duration distribution and exposure, ≥ 500 seeded runs; the rule must be at or above the 90th percentile |
| "Same universe" for the random control | Names are drawn from the stocks that pass the price and volume filter on that date. The rule's own selection filters (trend, rank) are not applied to them. The rule's exposure path, which includes its market filter, is copied |
| Bootstrap settings | Stationary bootstrap, mean block length 20 sessions, 2,000 resamples, fixed seed |
| Cost and fill re-runs of the same configuration | Not new trials (`n_trials = 0`). Every new configuration counts 1 |

## 11. Stakeholder decision 13 Nov 2026: paper experiment of a rule that failed Gate B

Issues #133 (S93, first rule on paper) and #132 (S92, settings for multi-day holding, closed).

### The result

The ten-year point-in-time test (`core_pit`, run on 13 Nov 2026 at code `7c0123e`; engine with `SimBroker`,
point-in-time universe, cost model v2) **failed Gate B** for both momentum rules, `trend_momentum` (rule 1) and
`mom_12_1_band` (rule 2), on marks 2, 3, 4 and 5 of section 7.

| `trend_momentum`, point-in-time | CAGR | Max drawdown | Sharpe |
|---|---|---|---|
| Base (100% exposure cap) | 8.24% | 18.27% | 0.59 |
| 60% exposure cap | | 14.18% | |
| 30% exposure cap | | 8.57% | |

### The decision

The stakeholder decided on 13 Nov 2026 to trade `trend_momentum` **on paper anyway, as a clearly labelled
experiment** ("failed the ten-year test"):

- **Paper only. Real money is barred for this rule**: Gate D can never be opened for it, whatever the paper run
  shows. Only a rule that passes Gate B can ever reach Gate D.
- **Gate B is unchanged.** No mark is loosened, re-read or re-run to let this rule pass. The verdict stays `fail`.
- **This paper run is not Gate C.** Gate C is the incubation of a rule that has passed Gate B. This run measures how
  the live engine, the broker and the ledger behave with a multi-day book (tracking, fills, stops, fail-safes) and
  what this rule does live. Its result can never count as Gate C of this rule or any other.
- **Level exposure caps** (the stakeholder's level limits of section 6, for this rule): Careful (`conservative`)
  **30%**, Balanced (`balanced`) **60%**, Hunter (`aggressive`) **100%** of equity, gross and overnight. They are
  tighten-only settings inside `BOOK_HARD_CEILINGS` (`config/profiles.yaml` `multi_day.rules.trend_momentum`).
- `mom_12_1_band` (and its no-filter version) stays **off**. `trend_momentum` is the only multi-day rule switched
  on when `KAIBOT_MULTIDAY=on`.
- `KAIBOT_MULTIDAY` stays **off** by default and `all_levels.allow_overnight` stays **false** in the repository. The
  stakeholder flips them himself on his Mac (`docs/ops/paper_start.md`). *Superseded for `allow_overnight` on
  13 Nov 2026 evening (Product, delegated; below): it is true in the repository; `KAIBOT_MULTIDAY` stays his switch.*
- The engine journal (`session_start.experiments`) and the heartbeat (`detail.experiments`) carry the label
  (`evidence: failed_gate_b`, `experiment: true`, `real_money: false`) so the dashboard shows it on every screen
  that shows the rule (`strategies/registry.EVIDENCE`).

### Retirement trigger (Product proposes; the stakeholder confirms with the start)

Stop the experiment, sell the rule's positions and switch `KAIBOT_MULTIDAY` off when **the rule's run drawdown
(peak to trough of its mark-to-market equity since the start) exceeds 1.5 x the ten-year test's maximum drawdown at
that level's cap**:

| Level | Cap | Ten-year max drawdown | Retire at |
|---|---|---|---|
| Careful | 30% | 8.57% | **13%** |
| Balanced | 60% | 14.18% | **21%** |
| Hunter | 100% | 18.27% | **27%** |

Why 1.5 x: the ten-year test includes the 2018 sell-off, the 2020 crash and the 2022 bear market, and the rule's
worst drawdown in all of it is the number above. A drawdown half as large again on paper means the live rule is not
the rule we tested (or the market is outside anything the test saw), and an experiment that has already failed Gate B
gets no benefit of the doubt. A tighter trigger (1.0 x to 1.25 x) would very likely stop it inside its normal range,
in which case we learn nothing about live behaviour. A looser one would let a known-failed rule lose more than its own
history justifies. The figures are rounded to whole points. Two more stops, as in Gate C: the live daily returns track
the `SimBroker` replay outside 10 bp (rolling 20 sessions) for 10 sessions in a row (the engine does not trade what
we tested), or the ledger and the broker differ and the difference is not resolved the same day.

The trigger is in `config/profiles.yaml` (`retire_drawdown_pct`) so the dashboard can show it. Since the stakeholder's
decision below (13 Nov 2026, 12:39 CET) **the engine enforces it** as the multi-day book's own run end.

**Interaction with the level's run-drawdown end (unchanged, a hard ceiling).** Each level's `run_drawdown_end_pct`
(Careful 4%, Balanced 8%, Hunter 12%; `HARD_CEILINGS` caps it at 12%) still ends the *whole run* and sells everything,
multi-day sleeves included (section 6). It is measured on the whole run, and it is lower than the triggers above, so at
every level the run ends first. At Careful, 4% is well below the test's 8.57% drawdown at the 30% cap, so a Careful
run is likely to end in a normal drawdown. That rule is not loosened here. If the stakeholder wants the experiment to
run through the drawdowns the test saw, the choice is his: run it at Balanced or Hunter, or start a new run when one
ends. The run-drawdown end is never raised above `HARD_CEILINGS`.

### Stakeholder decision 13 Nov 2026, 12:39 CET: "option A, as tested" (issue #206, PR #207)

The stakeholder's words: **"option A, as tested"**. What that decides:

1. **The multi-day book has its own run-drawdown end**, separate from the intraday one: Careful **13%**, Balanced
   **21%**, Hunter **27%** (1.5 x the ten-year test's maximum drawdown at each cap, the retirement trigger above).
   This needs a new book-level hard ceiling, which **the stakeholder approved explicitly: `BOOK_HARD_CEILINGS.
   run_drawdown_end_pct = 27`** (`risk/gate.py`). `RiskPolicy`, `HARD_CEILINGS` and the intraday run-drawdown ends
   (4 / 8 / 12%, ceiling 12%) are **unchanged**.
   - The book's equity is the run's pot plus the book's P&L since the run began (realised, dividends, open sleeves at
     the last price). Its peak is journalled (`md_book_peak`), so a restart measures from the same peak.
   - **Confirmed breach (#211):** the end counts only when the breach holds on two checks at least a minute apart
     (two engine ticks, two minute bars); a breach seen once is journalled (`md_book_breach`) and dropped if the next
     check is not a breach (`md_book_breach_clear`), so one bad print can never retire the rule. The first
     breach is read back from the journal after a restart, so a restart (or a crash loop) never delays the end
     (QA-Ops second key on #213).
   - **Confirmed prices and peak (M9 ticket 1.1, #426, engine audit R1):** for this check a holding's minute bar
     counts by itself only when it ended at most 5 minutes ago and is within 10% of the holding's last close mark;
     an older or further bar counts only when the broker's own price for the position, read at the same tick, is
     within 2% of it; otherwise the holding is measured at its last close mark (the heartbeat's `book_line` names it
     under `unconfirmed`). A holding without minute bars is measured at the broker's price. The peak is raised at
     once only at the close marks; during the session only by two readings above it at least a minute apart (the
     lower of the two). A held symbol whose shares differ between the ledger and the broker, or that has a
     corporate action today (an unbooked split reads as a fall), is measured at its last close mark
     (`md_book_check_skipped`, `book_line.unchecked`, event `multi_day_book_unchecked`); the check goes on for
     every other holding. A fall that the bars and the broker both show still ends the book at the second check.
   - **The other direction (QA-Ops second key on #527):** the check is never switched off for the whole book. A
     bar more than 15 minutes old that the broker's price does not agree with is no price any more, and the
     broker's price of that tick counts alone (market data down while the broker shows a fall: the book ends at
     most 15 minutes and two checks later). A holding measured at its last close mark for 5 minutes on end raises
     the event `multi_day_book_unconfirmed`, once per day and holding.
   - At the end (`risk/gate.book_run_ended`; `engine/core.Engine._book_end`), exactly this happens, once: the end is
     journalled (`md_book_end`); a `book_drawdown` halt row and a `multi_day_book_ended` event are written; the daily
     path's working orders are cancelled; every multi-day sleeve is sold **at market** (retried each tick until none
     is left); from then on the daily path decides and sends nothing **for the rest of the run**. The intraday rules
     and the run go on.
   - **Retired across runs** (Product call on #207, 13 Nov 2026): the rule is then **retired**, durably: a journal
     entry `md_retired` and a `multi_day_retired` halt row in Supabase (the engine reads both at start-up; either one
     keeps it retired, so neither a restart, a new journal nor a new run brings it back). The heartbeat says so
     (`detail.multi_day.note`: "trend_momentum retired: book fell 21.3% from its peak on <date>"). The only way back
     is an explicit stakeholder action: `KAIBOT_REENABLE=<rule>:<retirement id>` in the engine's `.env`, read once
     at start-up and journalled (`md_reenabled`, a `multi_day_reenabled` halt row and event). Each retirement has its
     own id (its date plus a random part, e.g. `2026-11-20.a1b2c3`, shown on the heartbeat as `multi_day.rules.<rule>.
     reenable`), so the value is **single-use** (#211): any other value is refused (`multi_day_reenable_refused`
     event), and a value left in the `.env` never re-enables a later retirement, not even one on the same date.
   - With the live daily path on, the intraday run end is measured on the run's equity **without** the multi-day
     book's P&L (`risk/gate.intraday_equity`), with the same formula as before (`run_drawdown_reached`). Without the
     daily path (flag off) the book's P&L is 0 and the check is exactly what it was. When the intraday end trips it
     still ends the whole run and sells everything, the multi-day book included (unchanged).
   - **The daily stop counts the book's daily move** (#211; S80, unchanged and tighter, never looser): the daily stop
     and the gate's `daily_loss_pct` measure the day's change in the **whole** run's equity, the multi-day book's
     move of the day and its overnight gap included. A bad day for the book therefore halts new **intraday** entries
     (and multi-day buys) for the rest of that session; it sells nothing of the book, which keeps its own end.
   - **Wash trades (#207, #211):** Alpaca refuses an order while an opposite-side order it could trade against is
     open in the symbol: a market or stop order always, a limit order against a new market or stop order (and a
     limit at a crossing price). The intraday rule therefore never enters a stock the multi-day book holds or has an
     order in (`multi_day_symbol`), and the daily path holds back a `cls` order while an opposite market, stop or
     limit order (an intraday bracket's target leg included) is open in its symbol. `SimBroker` models the rule
     (`wash_check`, opt-in, so replays are unchanged). *Since 18 Nov 2026 the daily path's orders are near-close
     DAY market orders, not `cls` orders (section 12); the same hold-back applies to them.*
2. **Book limits exactly as pre-registered**, now **confirmed** (no longer provisional; `config/profiles.yaml`
   `multi_day.provisional: false`): k = 0.1%, the level's `max_position_pct` per name (10 / 15 / 20%), at most
   **30 names**, a **40% sector cap**, the 3% cash reserve of the portfolio builder (Amendment 3) on the 1% hard
   floor, and the gap-scenario limit at the hard ceiling, **15%**. The live book is therefore
   `engine/daily.momentum_limits` at the rule's exposure cap, the sizing the ten-year test ran with.

### Product decisions 13 Nov 2026, evening: go-live on paper for Monday 16 Nov (the stakeholder delegated them)

The stakeholder asked for everything ready for Monday 16 Nov 2026 and delegated the remaining decisions to Product
("just decide and push"). Product decided:

- **`all_levels.allow_overnight: true`** in `config/profiles.yaml`. It acts only on multi-day books, which exist only
  with `KAIBOT_MULTIDAY=on`. With the flag off, no multi-day rule, no daily path and nothing that reads
  `allow_overnight` on the intraday path load, so the engine is unchanged (tested). **`KAIBOT_MULTIDAY` stays the
  stakeholder's single switch**, set in his `.env` (by `./scripts/go_live_paper.sh`, `docs/ops/paper_start.md`).
- **Migration 0019 is applied in production** as of 13 Nov 2026, about 18:55 CET: one pg_cron job `kaibot_deadman`,
  its checks passed (`docs/ops/migrations_pending.md`).
- **One go-live command**, `./scripts/go_live_paper.sh`: latest code, shorting off at the paper account,
  `KAIBOT_MULTIDAY=on`, no second engine, `--check`, the always-on service (the daily-bar refresh also at login and
  boot, so a Mac switched off at the weekend catches up on Monday), and a plain summary.

### What the code does and does not do yet (13 Nov 2026, PR for #133)

Done: the caps, the label, `trend_momentum` as the only rule on behind the flag, and the defaults off. **Not on `main`
yet: a live daily path.** The live engine (`engine/core.py`) asks strategies only at minute bars (`Event.BAR_CLOSE`).
The multi-day rules decide `AFTER_CLOSE`, and their targets are executed only by the research runner
(`engine/daily.run_daily` with `SimBroker`). So with the flag on, the engine loads and labels the rule but places no
`trend_momentum` orders. Wiring the daily path to the live engine (decide after the close, the portfolio builder at
the rule's cap via `risk/profiles.rule_exposure_pct`, closing-auction orders through the order manager,
protective stops) is a separate two-key story (Backend-quant and QA-Ops). The experiment starts trading only once it
is merged.

*History, as written on 13 Nov 2026. The live daily path was wired later (issue #206, `engine/live_daily.py`). Since
18 Nov 2026 it sends near-close market orders, not closing-auction orders (section 12).*

## 12. Update 19 Nov 2026: daily rules send near-close market orders, not closing-auction orders

Product, issue #358. This section records what the code on `main` does today. It changes no rule, no parameter and
no number.

### What replaced what

- **Before (decided 12 Nov 2026, live for one session, 18 Nov 2026), now history:** daily rules bought and sold in
  the next session's closing auction (`cls` orders, whole shares, submitted before 15:50 ET). The row "Execution for
  daily rules" in section 3 said so.
- **Since the evening of 18 Nov 2026 (issue #333, PR #337):** on the live paper path, daily rules send **DAY market
  orders, whole shares, from 15:50 to 15:57 ET** in the session after the decision
  (`ExecutionPolicy("near_close")`, `engine/live_daily.py`).
- **Why.** Alpaca's paper venue runs no closing auction. It works a `cls` order as a market order in the last
  seconds before 16:00 and lets the rest expire. On 18 Nov 2026, the rule's first live session, 4 of 30 `cls` buys
  filled in full, 3 in part and 23 not at all. About 16% of the book was invested against a plan of about 90%. A
  market order sent from 15:50 gives the paper venue minutes to fill.

### Current behaviour

| Step | What the engine does |
|---|---|
| Decision | Unchanged: after the close, on complete daily bars. The target list is journalled (`md_decision`). |
| Send window | 15:50 to 15:57 ET in the next session. On an early-close day: the close minus 10 minutes to the close minus 3 minutes (12:50 to 12:57 for a 13:00 close). The last three minutes are left for the venue to fill. |
| Orders | DAY market orders, whole shares. The targets are journalled before any order (`md_send`). Each order is the difference between the target and the shares held and working. Sales go out even when entries are blocked. Buys need no entry block and a clean reconciliation. |
| Outside the window | The `near_close` style refuses an order before the close minus 10 minutes and from the close on. The engine starts no send after 15:57. A buy still blocked then is dropped for that session. |
| Fills | Booked once each, from the broker's account activities. An order that reads filled before its fill is booked is not bought a second time (QA-Ops on #337). |
| Unfilled remainder | A DAY order expires at the close. The shares it had left are **not sent again** that session. The remainder is journalled once (`md_unfilled`) and reported as the event **`multi_day_unfilled`** (symbol, side, quantity, filled, left). `kb_health` returns it (migration 0029, PR #351). |
| An unfilled buy | It waits for the rule's next decision. Nothing carries it to the next session. Carrying unfilled buys to the next sessions is proposed in PR #366, which is open and not merged. |
| An unfilled full sale | A sale of a whole position (target zero) that did not fill is sent again in the next session's window. |
| Fail-safes | Just before the first orders go out, the account gets a row for each planned buy. So the watchdog and the dead-man keep a near-close fill that lands after the engine died (#341). A planned buy that never filled does not make them keep an unrelated position (#344, PR #352, migration 0030). |
| Intraday rules | Unchanged: brackets, and the flatten at the close minus 5 minutes. |

Not changed by #337: the decision time, the sizing, the plan order (sales, then resizes, then buys best first), the
3% cash reserve, whole shares, no opening-auction orders, no protective stop for the two momentum rules.

The `close` style (`cls`) is still in `engine/execution.py`. The live daily path does not use it. The research
runner can still send `cls` orders (`run_daily(execution="close")`) for comparison.

Also corrected after #337: `docs/ops` (PR #346) and the comments of the agents' daily path (PR #347).

### Research runs and the fill model

The research runner (`engine/daily.run_daily`) sends the same near-close orders at 15:50. `SimBroker` fills them at
the session's closing print, with the same cost model and the same cash check as it filled the `cls` orders. A test
asserts that fills and daily equity are identical with `cls` orders and with near-close orders. So the fill model of
section 5 stands, and no report, trial row or Gate B mark changed. The pre-registrations record this: Amendment 10
of `docs/prereg/trend_momentum.md` and Amendment 8 of `docs/prereg/mom_12_1_band.md`.

### Do Gate B's cost assumptions still describe this execution?

**For the research numbers: yes, nothing moved.** Gate B is judged on fills at the close of T+1. That is the same
before and after #337.

**For the orders now sent: no, not exactly. This is an open question for Backend-quant.** No number is changed
here.

- The tested cost is the cost of a fill at the official close. Cost model v1 charges half a spread plus impact at
  the closing print. Cost model v2 (#111; the model of the Gate B run in #202) has a venue `close`: one full
  half-spread and half the continuous impact.
- A market order sent at 15:50 is not a closing-auction fill. On a real exchange it trades in the continuous
  session: in cost model v2 that is venue `continuous`, with the full impact, at the price of that minute and not
  at the close.
- On Alpaca paper it fills at the quote of that moment (IEX-fed), with no impact and no queue. The price move from
  15:50 to the close is not modelled. The amendments call this a known deviation, live against research, and say
  Gate C counts it as one.

The questions for Backend-quant, to answer before a daily rule is judged at Gate C or proposed for real money
(posted on #202):

1. If near-close market orders stay, should the ten-year test price daily fills as `continuous` orders at the
   15:50 price instead of `close` fills at the closing print? That would be a re-run of the same configuration
   (section 10). It is not done here.
2. Is the Gate C tracking tolerance of section 10 (10 bp per session) still right with the 15:50-to-close move
   inside it?
3. The amendments say the execution is reviewed again before real money, because a live account's `cls` order does
   reach the closing auction. Which of the two is then the tested execution?

Until these are answered, read every Gate B number of a daily rule as "priced at the close", and every live paper
fill as "taken near the close".

### Other records checked for the same wording (19 Nov 2026)

| File | Result |
|---|---|
| `docs/decisions/14_multi_day_holding.md` | Checked, nothing to change. It names no order type and no send time. |
| `docs/prereg/trend_momentum.md` | Not edited (pre-registrations are append-only). Amendment 10 (18 Nov 2026) covers the change. The older text that says "closing auction" (Amendments 2, 3, 6 and 7) stands as written at its date. |
| `docs/prereg/mom_12_1_band.md` | Not edited. Amendment 8 (18 Nov 2026) covers the change. The older text (Amendments 2, 3 and 6) stands as written at its date. |
| `docs/prereg/spy_trend_filter.md` | Not edited. Its base fill is "the close of T+1 at the closing-auction price". That is a research fill model and the rule is not on paper, so nothing is wrong today. It has no amendment for near-close orders. If the rule goes on paper it needs one first, by Backend-quant. |
| The other pre-registrations | Checked, nothing to change. They are intraday rules, or research fill models at the close of T+1 or at the last regular-hours minute. None names an order type and none is on paper. |

Links: issue #333, PR #337, #341, #344 (PR #352), PR #346, PR #347, PR #351, issue #202, PR #366 (open), issue #358.
