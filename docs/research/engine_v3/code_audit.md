# Kaibot engine audit (read-only)

- **Repo:** github.com/falconomad/kaibot, branch `main` at `2d50869` (12 Nov 2026), fresh clone, nothing changed or run.
- **Scope:** `CLAUDE.md`, `docs/decisions`, `docs/m0`, `docs/ops`, `config`, `src/kaibot/*`, the research and engine scripts, Docker files, migrations, tests.
- **Method:** reading, `wc`, `grep`, and one throw-away AST script for complexity and type coverage. No engine, replay or database query was run; the 12 GB history is not on this machine, so every claim about magnitudes in data is marked "to check".
- **Age of the code:** 111 commits between 10 and 12 Nov 2026. This is a three-day-old codebase. Most of what follows is the cost of that speed.

**Answer to the owner's question in one paragraph.** The safety shell (risk gate, tighten-only config, lease, watchdog, dead-man, idempotent orders) is good and should be kept. The trading core is not an engine yet: it is one intraday rule written directly into a 1,051-line class, with a second, separate implementation of the same rule for backtests, six different fill simulators (five in the product, one in the tests), no position or portfolio model beyond "one bracket per symbol, flat by 15:55", market orders only, live decisions on a different data feed from the one the tests use, an evaluation gate that under-counts trials and cannot judge multi-day trades, and an operating model that depends on a person starting a laptop each afternoon. Multi-day holding is blocked in at least twenty places, three of which (watchdog, database dead-man, daily stop) would actively liquidate a swing book.

---

## 1. What exists

### Modules and size

| Area | Lines | Main files (lines) |
|---|---|---|
| `src/kaibot/engine` | 3,516 | `core.py` 1,244 · `shadow.py` 400 · `alpaca.py` 281 · `userconfig.py` 252 · `__main__.py` 233 · `market.py` 207 · `roomflow.py` 197 · `lease.py` 164 · `store.py` 154 · `watchdog.py` 145 · `m3glue.py` 111 |
| `src/kaibot/replay` | 1,255 | `walkforward.py` 377 · `sim.py` 287 · `features.py` 112 · `quality.py` 100 · `data.py` 94 |
| `src/kaibot/evaluation` | 688 | `gate_report.py` 312 · `ablation.py` 218 · `metrics.py` 157 |
| `src/kaibot/strategies` | 440 | `orb.py` 118 · `news_continuation.py` 117 · `dip_reversal.py` 87 (unused) · `spy_momentum.py` 50 |
| `src/kaibot/risk` | 211 | `gate.py` 171 · `profiles.py` 39 |
| `src/kaibot/broker` | 85 | `base.py` 43 (unused protocol) · `shadow_costs.py` 41 |
| `ingest` / `agents` / `news` / `features` / `common` | 1,613 / 1,097 / 601 / 434 / 400 | data downloaders, the LLM veto room, news desk, pump-risk and alt-data features, HTTP and rate limits |
| `scripts` | 2,961 | `walk_forward.py` 341 · `run_replay.py` 203 · three more replay scripts |
| `tests` | 8,520 | 480 test functions |
| `apps/web` (TS) / `migrations` (SQL) | 4,679 / 1,590 | dashboard; 13 migrations, 21 `kb_*` functions |

`Engine` in `core.py` is one class of 1,051 lines and 52 methods.

### How a trading day flows

1. `scripts/run_engine.sh` (started by hand): `git pull`, refuse a second local engine, start the watchdog, then `docker run --rm` or `python -m kaibot.engine`.
2. `engine/__main__.py:131-229`: settings from `.env`, `build()` wires broker, data, store, calendar, room; take the database lease; `Engine.startup()` restores open trades from Supabase, adopts orphan orders, checks the account is clean. Then a loop every ~10 s: renew lease, apply dashboard commands, and once per minute at second >= 7 call `Engine.tick()`.
3. `Engine.tick()` (`core.py:516-563`), market open:
   `_start_day` (calendar, level, start equity, owner config, rebuild today's state, load 70 sessions of SIP history, mark carried positions) -> `market.update` (IEX 1-minute bars, all symbols) -> `_sync_orders` -> `_check_drift` -> close carried positions -> `_risk_halts` (daily stop, run drawdown) -> `_trail_stops` -> flatten at close minus 5 minutes -> room results -> every 5-minute bar `_entries` (regime, candidates, news veto, owner veto, pump veto, room or `_decide` -> `risk.gate.evaluate` -> `_open` bracket order) -> 15:30 `_spy_momentum` -> 15:45 `_moc_exits` -> `_ledger` -> `shadow.on_minute`.
4. After the close: `_sync_orders`, `_finalize_day` (`core.py:1138-1176`): daily result, shadow and room counterfactuals, random-entry baseline, gate report.

### State

- **Supabase Postgres, schema `kaibot`, over PostgREST with the service key** (`engine/store.py`): `run`, `segment`, `signal`, `gate_run`, `order`, `fill`, `trade`, `ledger` (one row a minute), `daily_result`, `halt`, `command`, `heartbeat`, `event`, `config`, `room_session`, `gate_random`, `gate_report`, `engine_lease`, `trial`.
- **Position state** lives in memory (`Engine.pos`, a dict keyed by symbol; `Day`) and is persisted as a JSON blob inside `trade.features` (`core.py:104-105, 261-269`).
- **Local disk:** a Parquet cache of 70 sessions per symbol (`data/live/history`), the lease holder id; on the research machine the 12 GB history, `data/trials.jsonl` and reports.

### Backtest versus live

They share four things: `replay/features.py` (`candidates`, `regime`, `five_minute`), `risk/gate.evaluate`, `broker/shadow_costs`, and the profile loader. Everything else exists twice: the live engine manages real orders minute by minute (`core.py`), the replay is a separate vectorised simulator (`replay/sim.py:120-243`). The live engine imports from the `replay` package (`core.py:39-40`, `market.py:20-21`), not the other way round. One test compares them, on one day, by symbol set only, and it is skipped in CI (`tests/test_engine.py:231-267`).

### How strategies are plugged in

They are not. The dip rule is the body of `Engine._entries` and `Engine._trail_stops`; SPY momentum is `Engine._spy_momentum` and `_moc_exits`; the schedule is two `if` statements in `tick` (`core.py:553-560`). A level's strategy list in `config/profiles.yaml` only switches those branches on. The two shadow strategies do have an interface (`prior_stats()` plus `decide(panel, stats, m) -> [ShadowCandidate]`, driven by `shadow.step`), but they never trade.

### Tests (480 functions)

| Area | Tests | Notes |
|---|---|---|
| Engine loop, safety, lease, watchdog, outage drill, owner config | 148 | on fakes (`tests/fakes.py`), synthetic bars |
| Ingest and data scripts | 107 | downloaders, parsers |
| Agent room, gateway, news | 60 | |
| Replay, walk-forward, experiments, determinism | 51 | |
| Evaluation (metrics, gate report, ablation) | 38 | |
| Strategies | 19 | 5 of them test the unused `dip_reversal.py` |
| Features (pump, alt, bloodbath parity) | 15 | |
| Rate limiter | 14 | |
| Risk gate and profiles | 13 | includes Hypothesis property tests, loaded with `importorskip`: they skip silently where Hypothesis is not installed |
| Config SQL and presets | 10 | SQL parity needs a scratch Postgres |
| Cost model | 3 | |
| API stub | 2 | |
| Web (vitest) | 127 | |

Thin spots: cost model (3), live-versus-replay parity (1, skipped), partial fills, rejects, corporate actions and overnight behaviour (0).

---

## 2. What is sound and must be kept

| Asset | Why it is good | Where |
|---|---|---|
| Pure risk gate | 171 lines, no I/O, hard ceilings validated at load, every check stored with the signal | `risk/gate.py:40-51, 129-171`; `core.py:858-872`; `tests/unit/test_gate.py:36-40, 92` |
| Tighten-only owner config | rejects instead of clamping, validated against level and ceilings, applied only at a session open, invalid config refuses the session | `engine/userconfig.py:86-195`; `core.py:463-497` |
| Engine lease | atomic compare-and-set in SQL, age measured on two clocks, entries blocked while unverified, stop when lost | `engine/lease.py:81-152`; `migrations/0013` |
| Fail-closed halts and the account guard | unknown orders or positions, two-minute persistent drift, error streak, stale calendar all stop entries and never add risk | `core.py:197-259` |
| Layered dead-man idea | broker-held GTC stops, separate watchdog process, off-box `pg_cron` | `alpaca.py:108-131`; `watchdog.py`; `migrations/0006` |
| Idempotent orders and orphan adoption | deterministic `kb-<run>-<signal>-1` id, adopt on duplicate, adopt after a crash only if provably ours | `core.py:271-325, 898`; `alpaca.py:124-131` |
| Trial discipline | walk-forward chooses before it simulates the test month, input and event digests, deflated Sharpe, CSCV overfitting probability | `replay/walkforward.py:355-366`; `replay/events.py`; `evaluation/metrics.py:44-113` |
| Cost split | every replay trade carries gross, spread, impact, stop slippage and fees | `replay/sim.py:39-45, 70-73, 227-231` |
| "Report the worse" | live P&L is the minimum of venue and modelled fills | `core.py:1041`; `migrations/0001_init.sql:108` |
| Point-in-time discipline in features | every daily statistic is shifted one session, intraday values use bars 0..m | `replay/features.py:21-43`; `strategies/shadow_types.py:44-57` |
| Pre-registered bar | `gate.yaml` hashed into every report; reports are append-only | `evaluation/gate_report.py:38-46`; `migrations/0010` |
| Request budgets and bounded HTTP | one shared budget per source, 429 backs off every caller | `common/ratelimit.py`; `common/http.py` |
| Paper-only guard, no vendor SDK | live URL refused at start | `engine/config.py:53-55` |
| Room isolation | veto or halve only, runs in worker threads, never blocks the loop, budget enforced in SQL | `engine/roomflow.py`; `agents/gateway.py` |
| Injectable clock and fake broker | the seed of a real simulator | `core.py:150`; `tests/fakes.py:83-143` |

Keep the concepts. Several need their semantics changed for overnight holding (section 3, group B).

---

## 3. Findings

Severity: **critical** = blocks a multi-rule, multi-day engine or can produce wrong evidence or unmanaged risk; **major** = material risk or rework; **minor** = cleanup.

### A. Strategy logic and duplication

**A1. No strategy interface; the rule is the engine (critical)**
- Evidence: schedule hard-coded in the loop (`core.py:553-560`); dip entry, stop and target inline (`core.py:742-801`, constants at `46-50`); trailing only for one strategy name (`core.py:1068`); target logic keyed on the name string (`core.py:983`); exit reason inferred from leg type (`core.py:974-975`); the owner config knows two strategy names (`userconfig.py:119`); the gate baseline knows one (`config/gate.yaml:38`, `gate_report.py:198-219`). `strategies/dip_reversal.py` is imported only by its own test and computes a different target (1.5R capped at VWAP, `:83-85`) from the live rule (2R, `core.py:770`).
- Why it matters: every new rule means editing `tick`, `_decide`, `_on_entry_fill`, `_trail_stops`, the config validator, the SQL validator and the gate report. A daily rule has no place to run at all.
- Fix: one `Strategy` protocol (section 4). The shadow interface (`orb.py:96`, `news_continuation.py:97`, `shadow.py:129-149`) is the right starting shape.

**A2. Six fill simulators, none behind the broker interface (critical)**

| # | Simulator | Used for | Where |
|---|---|---|---|
| 1 | `run_portfolio` | dip replay, walk-forward | `replay/sim.py:120-243` |
| 2 | `random_entry_runs` | random baseline | `replay/sim.py:246-287` |
| 3 | `spy_momentum.run` | SPY replay | `replay/spy_momentum.py:20-60` |
| 4 | `ShadowBook.counterfactual` | shadow strategies, live and replay | `engine/shadow.py:107-126` |
| 5 | `RoomFlow._counterfactual` | room A/B | `engine/roomflow.py:154-172` |
| 6 | `FakeBroker` | engine tests | `tests/fakes.py:83-143` |

Each has its own fill rules. None is behind the broker interface. The approved tech design asked for exactly the opposite: "the same engine, with an injected clock and `SimBroker`" (`docs/decisions/08_tech_design_v2.md` section 6). `broker/base.py` defines an async `BrokerAdapter` that nothing implements; `AlpacaPaper` is synchronous with different methods (`alpaca.py:69-182`), so the `CLAUDE.md` rule "never bypass `BrokerAdapter`" protects nothing.

**A3. Functions that exist twice (critical)**

| Logic | Live | Replay | Same? |
|---|---|---|---|
| Stop and target | `core.py:769-770` | `sim.py:189-192, 220` | constants in one, parameters in the other |
| Trailing stop | `core.py:1066-1084` | `sim.py:98-117` | different mechanics (broker order replaced after each minute vs bar scan) |
| Daily stop | `core.py:651-661` | `sim.py:159-177` | different equity definition |
| Loss-streak pause | `core.py:1043-1046, 343-347` | `sim.py:152-154` | three copies |
| VIX size halving | `core.py:839` (floored) | `sim.py:209` (not floored: fractional shares) | no |
| FOMC blackout | `engine/events.py:17, 37-43` (three event types, owner-widened) | `sim.py:22, 182` (14:00 only) | no |
| News veto | `core.py:767, 774` (API count, limit 50) | `features.py:91-93`, `sim.py:185` (archive) | different code |
| Entry window | `core.py:685-688` (session length aware) | `features.py:62`, `sim.py:180` (fixed 375) | no on half-days |
| Time exit | `core.py:546-548` (market sell after 15:55) | `sim.py:21, 110-111` (15:55 bar close) | no |
| VIX series | `__main__.py:45-63` | `scripts/run_replay.py:45-57`, `replay/config_replay.py:37-41` | three builders |
| Session arrays | `replay/data.py:51-84` | `engine/shadow.py:367-392` (`fast_days`), `strategies/shadow_types.py:16-27` (`Panel`) | duplicate by design |
| Daily bars | `engine/m3glue.py:28-52` | none | a fourth bar representation |
| SPY momentum | `core.py:698-739` | `replay/spy_momentum.py:20-60` | see A4 |
| Owner-config validation | `userconfig.py:86-195` | PL/pgSQL `config_check` (`migrations/0011:106`, `0012:3`) and TypeScript | three languages |

**A4. Places where live and replay can decide differently (critical)**

1. Feed: replay on SIP, live on IEX bars (`market.py:101`). The dip is the return from the session's first print (`features.py:67`), which on IEX is not the opening auction price. A symbol with no IEX print in the 09:30 minute gets a NaN open and is silently excluded for the whole day (`data.py:72-80`); how often that happens is to check.
2. Volume: live scales IEX volume by one daily ratio (`market.py:80-93, 118`). The auctions are in SIP volume and not in IEX volume, so the intraday profile differs: scaled turnover reads low just after the open and somewhat high later, and the `turnover <= 1.5` filter does not select the same minutes as in replay.
3. Timing: replay fills at the next minute's open (`sim.py:212-214`). Live ticks between :07 and :17 past the minute (`__main__.py:192, 213-216`), then fetches bars and rebuilds every symbol's arrays from DataFrames (`market.py:114-155`). The parity test needs "about 7 minutes" for one day (`tests/test_engine.py:233`), roughly 5 s per scan, so orders go 10 to 25 s after the bar. The room adds up to 10 minutes and re-anchors the price (`roomflow.py:110-115`).
4. Quote freshness: the gate rejects IEX quotes older than 5 s (`gate.py:148`, `core.py:815`). Quotes are read once per scan and each candidate then waits for a news call (`core.py:760-767`), so later candidates in one scan are older. Replay passes age 0 (`sim.py:198`). To check: `gate_run.checks->'quote_age_s'` fail rate in the paper run.
5. Bar freshness: live accepts a decision when the latest bar is up to 2 minutes old (`core.py:830`), on forward-filled closes.
6. Vetoes that exist only live: pump risk, owner price, spread and VIX limits, the room (`core.py:775-791`).
7. Buying power: live uses the smaller of pot and broker (`core.py:835`); replay passes none.
8. Size: the paper pot is $5,000 with whole shares; the backtest uses $100,000 (average position $13,907). Expensive names size to zero live.
9. Universe: live trades 59 names (`__main__.py:67`, `config/universe_seed.yaml`); the ten-year test used 288 (`scripts/walk_forward.py:216`).
10. Exits: live stop and target rest at Alpaca's paper matcher; replay uses "stop wins ties, target needs a one-cent trade-through" (`sim.py:76-117`).
11. Half-days: live flattens at close minus 5 (`core.py:132-134`); replay always at minute 385 on forward-filled bars.
12. SPY momentum: live applies the regime gate, the VIX halving and the shared book (`core.py:713-722, 839`); the replay has none of the three (`replay/spy_momentum.py:33-53`).
13. Run drawdown: live ends the run (`core.py:663-674`); the walk-forward resets equity and peak every month (`walkforward.py:5-9`).
14. VIX timing (unsure): live reads FRED about 45 minutes before the open; if FRED has not yet published yesterday's close, live uses the close of two days ago while replay uses yesterday's. Check: log the last observation date returned at `prepare_day`.

### B. "Positions never survive the close"

**B1. The assumption is in at least twenty places (critical)**

| # | Assumption | Where | Effect on a held position |
|---|---|---|---|
| 1 | Flatten at close minus 5; no entries after | `core.py:132-134, 546-548, 682` | sold |
| 2 | "Carried" positions are sold at the next open's first tick | `core.py:101, 540-542, 614-615, 1145-1148` | sold |
| 3 | Daily stop sells everything | `core.py:651-661` | sold |
| 4 | Equity = start + realized today + unrealized since the fill | `core.py:642-649` | a multi-day gain or loss is counted again every day against the daily stop |
| 5 | Start equity = pot + sum of realized daily results | `core.py:600`; `store.py:144-146` | ignores open P&L at yesterday's close |
| 6 | Open risk must fit inside the room left before the daily stop | `gate.py:106-110`; `profiles.py:23` | Balanced allows 1.5% total open risk: three full-size positions, although `max_positions` is 5 |
| 7 | Watchdog closes every position at 15:55 if the heartbeat is stale | `watchdog.py:84-95` | liquidates the swing book when the engine is off |
| 8 | Database dead-man (when armed) deletes all positions at 15:56 ET if the heartbeat is stale | `migrations/0006_failsafe.sql:33-56` | same, from the database, on any day the engine is not running |
| 9 | One position per symbol, exits tied to the entry bracket | `core.py:174, 765, 972-977` | two rules cannot hold one stock; a stop cannot be resized or lifted around earnings |
| 10 | Foreign-position guard | `core.py:197-212` | positions of an ended run block all entries |
| 11 | New run and run-drawdown flatten everything | `core.py:365-367, 663-674` | sold |
| 12 | Per-day state object | `core.py:108-130, 602, 616-618`; `_restore_session` `327-354` | entries, pause and halts reset daily |
| 13 | Daily result is realized P&L only | `core.py:1151-1163`; `migrations/0001_init.sql:103-113` | no mark-to-market equity series exists |
| 14 | Gate report books P&L on the exit day | `gate_report.py:121-127` | Sharpe and drawdown hide open losses |
| 15 | Random baseline is intraday | `sim.py:246-287`; `gate_report.py:198-219` | cannot be matched to a multi-day rule |
| 16 | Walk-forward simulates each month from flat | `walkforward.py:5-9, 206-225` | positions cannot cross a month |
| 17 | Bars are (days x 390) regular-hours arrays; live keeps 70 sessions | `replay/data.py:11-24`; `market.py:23` | no daily series, no 200-day history |
| 18 | Shadow and room counterfactuals end at the flatten | `shadow.py:77-79, 107-126`; `roomflow.py:139-172` | |
| 19 | Owner config: flatten and "no overnight" are locked; times are session minutes | `userconfig.py:11-12, 23-29` | |
| 20 | Dashboard functions | `migrations/0007`: `kb_overview` "today" by entry time (`:20-22`), `kb_report` start equity and trades by entry day (`:78-83`), `kb_attribution` by entry hour (`:116`), `kb_evidence` t-stat on R per trade (`:43-47`) | no open-position, exposure or holding-period view |

Two corrections to common belief: bracket legs are already good-till-cancelled (`alpaca.py:111-118`), which is right; only the unused `OrderRequest` still says `day` (`broker/base.py:23`). And `allow_overnight: false` and `flatten_at_et` in `config/profiles.yaml:40-42` are read by nothing, so changing them changes nothing.

- Fix: a holding policy per strategy (intraday rules flatten, multi-day rules do not), mark-to-market equity, a daily stop defined on the day's change in equity, and fail-safes that cancel entries and keep stops instead of liquidating (section 4, step 5). Items 7 and 8 must change in the same release as the first overnight position.

### C. No portfolio layer

**C1. Sizing is per trade; nothing looks at the book (critical for the target design)**
- Evidence: `size_position` takes the minimum of risk per trade, position cap, open-risk headroom and buying power (`gate.py:113-126`). `symbol_exposure` is passed as `0.0` everywhere (`core.py:824`, `sim.py:198`, `shadow.py:94`). There is no target position, gross or overnight exposure, sector, beta or correlation limit, no volatility target, no cash rule, no budget per strategy. The only market-wide control is "halve above VIX 25" (`core.py:839`).
- Why it matters: on a market-wide dip, five dip trades are one bet (75% of the pot at Balanced). With several rules they will pile into the same names. Decision 14 already asks for gap-scenario sizing and an overnight exposure cap that no code can express.
- Fix: a `PortfolioConstructor` that turns signals into target positions under book-level limits; the gate stays as the final check with a richer `Snapshot`.

### D. Orders

**D1. Market orders only (major).** Entries are market brackets (`alpaca.py:113-123, 135`), exits are market sells (`alpaca.py:172-182`). The approved design specified a marketable limit at ask + 0.05% cancelled after 2 minutes (`08_tech_design_v2.md` section 3). No limit entries, no slippage cap, no participation cap (`shadow_costs.max_fill_qty` is called only by a test).

**D2. No auction orders (major).** One market-on-close sell exists, for SPY (`alpaca.py:146-157`). There is no market-on-open or limit-on-open/close path. Daily rules want exactly these: decide after the close on complete data, trade in the next auction, pay no spread.

**D3. Partial fills are not handled (critical once limits exist).** An entry counts only when `status == "filled"` and is booked at the requested quantity (`core.py:962-967, 979-981, 999`). A partly filled entry stays "pending"; at the flatten it is cancelled and closed as `not_filled` (`core.py:1087-1090`), leaving shares at the broker that the engine has just forgotten.

**D4. Rejects abort the scan (major).** `_open` calls the broker with no error handling (`core.py:905-907`). A 403 or 422 on one candidate (stop above the market by the time the order arrives, buying power, untradable symbol) raises through `_decide`, `_entries` and `tick`: the remaining candidates are dropped, the signal is already stored as approved (`core.py:867`), and it counts as a loop error.

**D5. The order row is written after the broker call (major).** Sequence: submit, then insert `order`, then insert `trade` (`core.py:905-953`). If Supabase fails after Alpaca accepted, the position exists only at the broker. The engine then reports drift and halts entries (`core.py:252-259`), but the position is not in `self.pos`, so it gets no trailing stop and no 15:55 flatten (`core.py:1100-1102` iterates `self.pos`). Adoption runs only at start-up (`core.py:182-189`).
- Fix: write the intent first (local journal), then submit, then confirm.

**D6. A tick is not atomic and its retry hides errors (major).** `d.last_m = m` is set before any work (`core.py:535-537`). If order sync, the daily-stop check or an entry raises, the retry 10 s later returns immediately as "already done" and `on_tick_ok()` resets the error streak (`__main__.py:192-195`). So a failure that recurs every minute after line 537 never reaches the three-error kill switch, and that minute's stop, trailing and entry steps are skipped each time.

**D7. Flatten is not isolated per position (major).** `flatten_all` loops with no `try` (`core.py:1100-1102`). One failed close stops the loop; positions later in the dict are not attempted until the first succeeds. `close_position` cancels orders and immediately deletes the position (`alpaca.py:172-182`); if the cancel is still pending the shares are held and the delete can be refused (unsure how often on paper: search the logs for 403 on `DELETE /v2/positions`).

**D8. Start-up reconciliation is by symbol name only (major).** `check_account` compares sets of symbols (`core.py:204-207`); quantity is compared only in session (`core.py:241-259`); cash and equity are never compared with the broker; fills are discovered by polling one order per position per minute (`core.py:957-977`) instead of reading account activities. A restart after the close does not sync or finalize (`core.py:519-524` needs `self.day`), so that day's result is never written and start equity is wrong for the rest of the run, because equity is the sum of `daily_result` rows (`core.py:600`). This is a known follow-up; it is an accounting-design flaw, not a detail.

**D9. Stops after a gap (critical for multi-day).** The protective order is a stop-market (`alpaca.py:122, 136`). Size is risk divided by stop distance (`gate.py:119`). After an overnight gap the loss is the gap, not 1R. The replay models gap-through only inside a session (`sim.py:87, 107`).

**D10. Corporate actions (critical for multi-day).** No code mentions splits or dividends. The live history cache is split-adjusted when downloaded and only appended to afterwards (`market.py:66-76`), so after a split old bars are on the old scale for up to 70 sessions. A held position's share count changes at the broker and the engine reports drift. Unsure: whether Alpaca cancels good-till-cancelled stops on a split; check their corporate-actions documentation before holding overnight.

**D11. Trailing-stop leg tracking (to verify, possibly major).** After `replace_stop` the engine stores the new order id (`core.py:1080`) but detects exits only from the parent order's nested legs (`core.py:972-977`). If a replaced leg is not listed under the parent, a trailing-stop fill would surface only as drift. This is what open issue #11 should confirm on paper.

**D12. Hash-chained event log is not a chain (minor).** Each process caches the previous hash (`store.py:108-124`); the engine and the watchdog both append, nothing verifies the chain, and it holds operational events only. "P&L is a fold over it" (tech design section 4) is not true.

### E. Data

**E1. Live decisions on IEX, evidence on SIP (critical for intraday rules).** See A4 items 1 to 5. For daily rules this problem disappears without paying for data: decide after the close on SIP bars older than 15 minutes (free), trade the next auction. That is the cheapest fix and it fits the owner's free-data rule in `CLAUDE.md`.

**E2. Polling and latency (major for intraday rules).** REST polling of all symbols every minute, no stream; features rebuilt from DataFrames each scan (A4 item 3). The design budget was 7 s from bar close to order.

**E3. Universe (critical for research).**
- Survivorship: today's 288 large caps applied back to 2016 (`config/universe_history.yaml:3-5`). The point-in-time universe promised for M1 was never built; the table `kaibot.universe_daily` has no reader or writer.
- Live trades 59 names, the test covered 288 (A4 item 9).
- Hidden hole: features need the sector ETF on the same dates (`features.py:16-18, 28`). XLC began trading in June 2018, so the 18 communication names (GOOGL, META, NFLX, DIS and others) cannot produce a candidate before about mid-July 2018 (20 common sessions are needed). Roughly the first 14 months of the out-of-sample window silently exclude them. Check: trades by sector and year in the walk-forward CSV.
- The sector map is today's, fixed.

**E4. Split-adjusted prices, no dividends (major).**
- History and live bars use `adjustment=split` as of the download date (`ingest/alpaca_history.py:81`, `alpaca.py:201`).
- The cost model has absolute floors: half-spread >= $0.005 (`sim.py:57`) and spread >= $0.01 (`shadow_costs.py:24`). On back-adjusted prices these are far too large for names that split heavily later. NVDA at an adjusted $2.50 to $5 in 2017 is charged 30 to 60 bp per round trip instead of about 5 (section F4, table B). Costs in early years are overstated for NVDA, CMG, TSLA, AVGO, ANET, LRCX and similar. Check: `cost_spread / (qty * entry)` by symbol and year.
- Dividends are in neither the strategy P&L nor the SPY benchmark (`scripts/walk_forward.py:136-147`; `market.py:178-183`). For intraday trades this is harmless; for multi-day holds and for "beat SPY" it is roughly 1.2% a year on the benchmark.
- `spy_return()` divides by the last cached bar, which can be a post-market print (`market.py:59, 178-183`).
- Fix: store raw bars and a corporate-actions table; adjust at read time as of the decision date; compute costs on raw prices; credit dividends.

**E5. Point-in-time correctness (minor today).** Features are clean (F3). The `available_ts` column written by the downloader (`alpaca_history.py:100`) is read by no replay code: the `CLAUDE.md` rule is kept by array indexing, not by an accessor. For daily and fundamental data that will not be enough.

**E6. Earnings and news (major for multi-day).** The only earnings protection is "any Alpaca headline in 24 h" (`core.py:767`, `alpaca.py:235-237`). There is no earnings calendar. Decision 14 requires "never hold through earnings".

### F. Research platform

**F1. Speed and why (major).** One ten-year walk-forward takes 707 s; five experiments about an hour. Causes: `run_portfolio` is a Python loop over 386 minutes for every day and every configuration, events or not (`sim.py:145`), about 28 million iterations for 27 configurations; exits are scanned bar by bar in Python (`sim.py:105`); features are recomputed from Parquet on every run (`walkforward.py:338-342`), nothing is cached. The live engine in simulation would be far slower (7 minutes a day). A daily rule on daily bars should take seconds; this code cannot run one.

**F2. Experiments are code, not data (major).** `Params` has dip-specific fields (`walkforward.py:41-51`); experiments are a hard-coded dict (`:103-114`); `--variant` accepts only `A` (`scripts/walk_forward.py:219`); the trial row hard-codes `"strategy": "dip_reversal"` (`:322`). SPY momentum and the shadow rules each got their own script and simulator. A new rule today means a new simulator.

**F3. Look-ahead audit of `features.py` and `sim.py`.** I found none in the decision path:
- daily volatility, dollar volume, beta, residual volatility and the volume profile are all shifted one session (`features.py:21-43`);
- dip, turnover, VWAP, ATR and the higher-low test use bars up to m (`:66-84`);
- the regime uses SPY to minute m and the previous VIX close (`:102-112`);
- entries fill at the next minute's open; the trailing stop on bar m uses closes to m-1 (`sim.py:98-117`);
- the walk-forward picks parameters before simulating the test month, and a test perturbs test prices to prove it.

Where optimism or distortion can still hide:
1. universe survivorship and the XLC hole (E3);
2. back-adjusted price levels in the cost floors (E4);
3. the news archive was downloaded in 2026; its coverage of 2016-2018 is unknown, and thin coverage weakens the "no news" filter in early years;
4. minutes with no trades are forward-filled and can be "filled" at a stale price (`data.py:72-80`), penalised only through the impact term;
5. no latency, queue or participation model;
6. half-days are simulated as full days on flat bars;
7. the caveat "FOMC blackout only 2025-2026" printed in every report is a hard-coded string (`scripts/walk_forward.py:287`) and is out of date: the calendar has FOMC dates for 2016-2027. The replay still ignores the unscheduled-statement and CPI event types.
The look-ahead test itself is narrow: it perturbs one day's future with thresholds switched off (`tests/unit/test_replay.py:40-54`).

**F4. Cost model (major: uncalibrated, not wrong in total).**

Formula (`broker/shadow_costs.py:27-33`, replay inputs `sim.py:56-67`):

```
half_spread = max(1 bp x price, $0.005)            # replay only; live uses the IEX quote
spread      = max(ask - bid, 1 bp x mid, $0.01)
impact      = 0.25 x spread + 0.5 x sigma_1m x mid x sqrt(qty / vol_1m)
buy  = mid + spread/2 + impact        sell = mid - spread/2 - impact
sigma_1m = 20-day daily volatility / sqrt(390)      (features.py:97)
vol_1m   = volume of the fill minute, floor 1 share (sim.py:214)
fees     = 0.278 bp of sale value + $0.000166 a share (placeholders, shadow_costs.py:11-12)
```

Per round trip with a market exit: spread 2 bp, plus 1 bp of fixed "impact" (the quarter-spread, twice), plus twice the square-root term. A target exit is a limit fill and pays nothing on the way out.

A) $13,907 order at $100 (139 shares), daily volatility 2%:

| Volume in the fill minute | Per side (bp) | Round trip (bp) | Of which "impact" |
|---|---|---|---|
| 0 (stale minute) | 61.2 | 122.4 | 120.4 |
| 500 | 4.2 | 8.3 | 6.3 |
| 2,900 | 2.6 | 5.2 | 3.2 |
| 20,000 | 1.9 | 3.8 | 1.8 |

B) Same order on back-adjusted prices: $2.50 -> 62 bp, $5 -> 32 bp, $10 -> 17 bp, $25 -> 8 bp, $50 and above -> 5.2 bp.

Reading:
- The reported 3.14 bp impact is consistent with the formula: about 1 bp fixed quarter-spread plus about 2 bp square-root term, i.e. the order is about 5% of the fill minute's volume on average.
- With an average minute the square-root term equals `0.5 x daily volatility x sqrt(order / daily volume)`: 0.4 bp a side at $1bn a day, 1.7 bp at $50M. That law was fitted to institutional orders worked over hours. A 139-share marketable order in a large cap normally fills inside the displayed size and pays about half the quoted spread and nothing more. So the impact term is probably 2 to 3 bp per round trip too high.
- Against that, the 1 bp half-spread floor is too low for the less liquid half of 288 names, in the first half hour, and during sell-offs, which is when this rule trades; and stop-market exits in a falling stock slip more than modelled.
- My judgement: about 6 bp per round trip for market-in, market-out is plausible (range 3 to 7); the split between "spread" and "impact" is not reliable; nothing is fitted to a quote or a fill. The impact term is unbounded when the fill minute is thin and the floors are distorted by price adjustment.
- It does not change the verdict on variant A (gross is zero). It will matter when rules with different order types are compared.
- Fix: measure spreads by liquidity bucket and time of day from quotes (the unspent Databento credit, or Alpaca quote history), cap participation, add auction and limit-order cost models, compute on raw prices, and reconcile the model against paper fills monthly.

**F5. The trial log is a file on one laptop, and the two consumers count it differently (major).**
- The log is `data/trials.jsonl`, git-ignored, appended after a run (`scripts/walk_forward.py:309-332`). The design said "the runner refuses to run without inserting a `trial` row"; only config replays write the `kaibot.trial` table (`scripts/run_config_replays.py:71-76`).
- The walk-forward sums `n_trials` over rows (8,286) (`scripts/walk_forward.py:75-79, 128`). The live gate counts rows (`gate_report.py:136-137, 250-274`), one per run, so a dozen or two. The live deflated Sharpe is therefore barely deflated.
- In Docker the gate looks for `/app/data/trials.jsonl` (`gate_report.py:239-240`), which is not in the image (`.dockerignore`, `Dockerfile:7-8`). The paper engine in Docker sees no research trials at all.

### G. Evaluation

**G1. The go-live gate cannot judge multi-day rules unchanged (critical).**

| Criterion | Today | Problem for multi-day | Change |
|---|---|---|---|
| >= 900 trades in >= 3 months | `gate.yaml` | derived for 10 bp mean and 100 bp s.d. per intraday trade (`metrics.py:122-124`); a book of 5-8 positions held 10-20 days makes 5 to 17 trades a month: 4 to 14 years | evidence in months and effective independent bets, set per rule from its own mean and variance |
| t > 3 on per-trade P&L | `gate_report.py:133`, `metrics.py:19-23`, assumes independent trades | overlapping holds share the market move; even today same-day dips cluster | t on daily mark-to-market returns with Newey-West errors, or a block bootstrap; cluster by day at minimum |
| Deflated Sharpe | daily returns, trial count wrong (F5) | daily series is realized-on-exit (B1 item 14) | mark-to-market series; one trial registry |
| >= 95th percentile of random entries | intraday random minute, intraday exits, no gate constraints (`sim.py:246-287`); exists only for the dip rule (`gate.yaml:38`), so SPY momentum can never pass | no multi-day notion | random entry dates with the same holding-period distribution, exposure, universe and liquidity filter |
| Sharpe above SPY's | price-only SPY (E4) | a long-only swing book is mostly market exposure | regression alpha and its t-stat against SPY total return, plus an exposure-matched SPY line |
| Drawdown within cap | realized equity | hides open losses | mark-to-market |
| Frozen code | one fingerprint for the whole engine (`gate.yaml:26-34`) | a change to rule B resets rule A's clock | fingerprint per strategy plus shared core |

**G2. The fingerprint misses trading code (major).** The pattern list has `engine`, `risk`, `replay`, `features`, `agents`, `news` but not `strategies/` (SPY momentum's thresholds), `broker/` (the cost model that sets reported P&L) or `evaluation/`. Those can change without resetting the evidence clock.

**G3. The random-entry test has already passed a rule with no edge (major).** Variant A scored at the 100th and 95th percentile of random entries on its two 17-month windows (`docs/m0/replay_002.md`), and ten years later has zero gross edge. Either the windows were lucky or the baseline is mismatched (uniform minute and symbol, no liquidity or regime filter, no gate limits). The walk-forward does not compute the baseline at all (`pipeline.py:28-29`). Its power as a criterion is unproven; match it properly and report it in every walk-forward.

**G4. There is no portfolio-level gate (major).** Rules are judged one by one (`gate_report.py:93-96`). A multi-rule engine also needs the combined book and the correlation between rules.

### H. Operations

**H1. Manual daily start on a laptop (critical for always-on and for overnight holding).** A person runs `./scripts/run_engine.sh` before 15:30 Berlin and must leave it running past 22:00 so the day is finalized (`docs/ops/run_engine.md`; `core.py:519-522`). The Docker path uses `docker run --rm` with no restart policy (`scripts/run_engine.sh:36-37`); a crash stays down, and the container's logs go with it. A closed lid sleeps the Mac. With overnight positions, "engine off" becomes a risk state, not a missed day.

**H2. A free hosted database sits on the order path (major).** Every tick makes synchronous PostgREST calls with up to four tries of 20 s each (`store.py:33-59`), about 87 s worst case, inside the loop that manages stops. Known symptom: a 30 s outage ends entries for the session. Equity itself is derived from rows in that database (D8).
- Fix: a local write-ahead journal (SQLite) as the source of truth, replicated to Supabase asynchronously.

**H3. No alerting (major).** No push, e-mail or health-check ping exists; `HEALTHCHECKS_PING_URL` is in `.env.example` and read by nothing. A halt, drift, lease loss or dead engine is visible only if someone opens the dashboard.

**H4. Deployment safety (major).** Every start runs `git pull` (`scripts/run_engine.sh:19`): whatever is on `main` trades that day, and a changed fingerprint restarts the evidence clock without anyone deciding to. "Deploy outside market hours" is a convention. The image uses a floating base tag and unpinned packages (`Dockerfile:3-6`). Migrations are applied by hand (0014 is partly applied). There is no staging account.

**H5. Dependencies (major).** Three unpinned lists and no lock file. CI installs `pyproject.toml`; production installs `requirements-engine.txt`; research adds `requirements-research.txt`. Of 20 packages in `pyproject.toml`, 13 are never imported (uvicorn, pydantic-settings, httpx, websockets, sqlalchemy, psycopg, alembic, apscheduler, duckdb, datasketch, structlog, tenacity, feedparser). `scipy`, which `evaluation/metrics.py:16` needs, is not listed; CI gets it only because the unused `datasketch` depends on it. `mypy` and `import-linter` are dev dependencies that nothing runs or configures.

**H6. Configuration sprawl (major).** The same number lives in several places: level limits in `config/profiles.yaml`, the SQL table `kaibot.level_limit` (`migrations/0011:7-19`), `HARD_CEILINGS` (`gate.py:40-42`), floors in `userconfig.py:32-39` and again in SQL (`0011:111-112`), `presets.yaml` and `presets.json`. Rule parameters are module constants: `core.py:46-52`, `userconfig.py:23-29`, `events.py:15-17`, `sim.py:21-22`, `spy_momentum.py:15-20`; `FIRST_ENTRY_M` is defined three times. Eight keys in `profiles.yaml` (`flatten_at_et`, `entry_window_et`, `allow_overnight`, `allow_leverage`, `min_quality_score`, both `halts` entries, `shadow_strategies`) are read by nothing; the rest of its `universe` block is read only by a research screen script (`ingest/test_group.py:76`). The live universe file path is hard-coded twice (`__main__.py:67`, `core.py:503`).

**H7. Time and holidays (minor, mostly sound).** Session times come from the broker's clock and calendar each tick, with early closes handled. Gaps: the database dead-man does not cover early-close days (`0006:10-11`); the SQL `next_open_after` skips weekends only (`0011:221-227`); the macro calendar must be extended by hand every autumn or entries stop on 1 January.

**H8. Secrets (minor).** A plain `.env` on the laptop holds the Supabase service-role key (full database access) and is mounted into containers; the design's encrypted file was not done. No secret is committed. One `sb_secret_`-shaped constant in `tests/test_outage_drill.py:442` is used against `example.invalid` and looks synthetic; confirm it is not real.

**H9. Logging (minor).** Plain `logging.basicConfig` to stdout (`__main__.py:135`); no structured logs, no rotation, no shipping.

### I. Code health

**I1. Module size and hot spots (major).**

| Function | Complexity | Lines | Where |
|---|---|---|---|
| `run_portfolio` | 45 | 124 | `replay/sim.py:120` |
| `_one` (gate report) | 43 | 82 | `evaluation/gate_report.py:100` |
| `tighten` | 40 | 110 | `engine/userconfig.py:86` |
| `pump_risk` | 36 | 79 | `features/pump.py:67` |
| `Engine.tick` | 29 | 48 | `engine/core.py:516` |
| `main` | 28 | 99 | `engine/__main__.py:131` |
| `Engine._entries` | 27 | 60 | `engine/core.py:742` |
| `Engine._decide` | 18 | 76 | `engine/core.py:803` |

(Branch count from a simple AST pass; `agents/evalset/stub.py:47` is 52 but is test tooling.)

**I2. Dead or orphan code (minor, but it misleads).**
- `engine/run.py`: a stub that exits. `docker/compose.yml:13` still runs it; the whole `docker/` folder is superseded by the root `Dockerfile` and `compose.yaml`.
- `apps/api`: 36-line stub, replaced by decision 11, still packaged (`pyproject.toml:39`).
- `src/kaibot/store/`: empty package.
- `strategies/dip_reversal.py` and its 5 tests: not the live rule.
- `broker/base.py`: unused async protocol. `shadow_costs.max_fill_qty`: unused.
- `features/bloodbath.py` (147 lines) and its parity test: no caller in the trading path.
- `AlpacaData.movers`, `parse_movers`, `core.new_idempotency_key`: unused.
- Tables with no code reader or writer: `kaibot.universe_daily`, `kaibot.macro_event`; column `daily_result.cash_ret`. Eleven old `public` tables from bloodbath are locked, not dropped (`migrations/0003`).
- Fourteen keys in `.env.example` that nothing reads (among them `HEALTHCHECKS_PING_URL`, `DATABASE_URL`, `ALLOWED_EMAILS`, the `LLM_*` budgets and the `CLAUDE_*_MODEL` names).

**I3. Layering and names (minor).** The live engine depends on the `replay` package, and `replay/config_replay.py:18` depends on `engine.userconfig`: a cycle between "live" and "research". Module names record milestones, not purpose (`m3glue.py`, `roomflow.py`). "Shadow" means both the cost model and the non-trading strategies. The import rules in the tech design are not enforced.

**I4. Types (minor).** 76% of 550 functions are fully annotated (engine 73%, replay 74%, agents 64%). `Engine.__init__` takes `broker` and `store` untyped (`core.py:142-157`); rows are plain dicts end to end; no type checker runs in CI.

**I5. Threads (minor).** Room workers read `market.history` and `today_bars` (`roomflow.py:79-81`, `m3glue.py:28-52`) while the main thread replaces them. Only the news desk is locked.

---

## 4. Target architecture, grounded in this code

### The smallest set of new abstractions

| Abstraction | Responsibility | Built from | Replaces |
|---|---|---|---|
| `Clock` + `Runner` | one event loop: session open, bar close, pre-close, session close, daily jobs; `LiveClock` (wall time + broker calendar) and `SimClock` (stored bars) | `__main__.py:185-216`, `Engine.tick`, `tests/fakes.Clock` | the minute loop in `sim.py:145`, per-script drivers |
| `MarketData` | point-in-time reads: `bars(symbol, frame, n, as_of)`, quotes, news, calendar, corporate actions; `HistoricalData` and `LiveData` | `replay/data.py`, `engine/market.py`, `Panel`, `m3glue.daily_bars` | four bar representations; per-scan DataFrame rebuild |
| `Strategy` | `name`, `schedule`, `holding` (intraday or multi-day), `warmup`, `on_event(ctx) -> [Signal]`; a `Signal` carries symbol, direction, conviction or risk budget, entry style, stop rule, exit rule, maximum holding, reasons, features | `ShadowCandidate`, `prior_stats`/`decide`, `features.candidates`, `spy_momentum.py` | `Engine._entries`, `_spy_momentum`, `_trail_stops`, `strategies/dip_reversal.py` |
| `PortfolioConstructor` | signals + current book + limits -> target positions per strategy sleeve; budgets per strategy, gross and overnight exposure, per-name, sector, beta, gap-scenario sizing, volatility target, cash buffer | new; sizing arithmetic from `gate.size_position` | `0.5 if vix > 25` and the implicit "max positions" portfolio |
| `ExecutionPolicy` + order manager | target changes -> orders: open and close auction orders for daily rules, marketable limits with a timeout for intraday, standalone protective stops; order state machine with intent written first; partial fills, rejects, replace, cancel; reconciliation from account activities | `Engine._open`, `_sync_orders`, `_on_*_fill`, `_exit`, `_adopt_orphans` | brackets as the unit of position; D3-D8 |
| `Broker` protocol | `submit`, `cancel`, `replace`, `open_orders`, `positions`, `account`, `activities`, `clock`, `calendar`; `AlpacaPaper` and `SimBroker` | `engine/alpaca.py` (move to `broker/`), `tests/fakes.FakeBroker`, fill rules of `sim.py`, `shadow_costs.py` | `broker/base.py`; simulators 1-6 in A2 |
| `Ledger` | positions per (strategy, symbol), cash, daily mark-to-market equity, realized and unrealized P&L, dividends and splits; state is a fold over a local journal | `Pos`, `Day`, `sim.Book`, `store.ledger` | equity from `daily_result`; `trade.features` as state store |

`risk/gate.evaluate` stays as the last check before any order, pure, with new `Snapshot` fields (gross and overnight exposure, sector exposure, gap-scenario loss). `userconfig.tighten`, the lease, the halts, the watchdog and the dead-man stay with new semantics.

Target flow, identical in backtest and live:

```
Clock event -> MarketData (as of now) -> each Strategy due -> Signals
            -> PortfolioConstructor -> target positions
            -> risk gate (per order) -> ExecutionPolicy -> Broker (Alpaca | Sim)
            -> fills -> Ledger -> journal -> (async) Supabase -> dashboard, gate report
```

Daily schedule for the always-on service: 08:30 ET data refresh, corporate actions, reconcile with broker, compute daily signals and place open-auction orders; 09:30-16:00 intraday loop for intraday rules and stop management; 15:40 close-auction orders; 16:10 mark-to-market, daily result, gate report; night: history update, universe rebuild, backups.

### What gets deleted

`replay/sim.py` (`run_portfolio`, `random_entry_runs`), `replay/spy_momentum.py`, `replay/pipeline.py`, the counterfactual code in `shadow.py` and `roomflow.py`, `shadow.fast_days`, `Panel`, `engine/run.py`, `docker/`, `apps/api`, `src/kaibot/store/`, `strategies/dip_reversal.py`, `broker/base.py`, `features/bloodbath.py`, the three per-rule replay scripts (one research command instead), the unused dependencies and profile keys. `core.py` shrinks to the runner plus halts.

### Migration order (the paper engine runs after every step)

1. **Safety net.** Lock dependencies. Commit a small bar fixture (5 symbols, 3 days) and a golden test of today's replay events and of the live engine on fakes. Apply the quick fixes D4, D6, D7, G2, F5.
2. **Broker protocol.** Extract it from `AlpacaPaper` and `FakeBroker`; type `Engine` against it. No behaviour change.
3. **Fast market data.** Replace `LiveMarket.symbol_days` with incremental arrays behind `MarketData`. Same features, same decisions, engine-in-simulation becomes fast.
4. **SimBroker and SimClock.** Promote `FakeBroker` with the replay's fill rules and costs. Run the unchanged `Engine` over history; assert it reproduces `run_portfolio` on the fixture. This is the first true "same code" proof.
5. **Strategy interface.** Move dip reversal and SPY momentum out of `Engine` behind `Strategy`; make ORB and news continuation ordinary strategies in shadow mode. Golden tests must not change.
6. **Ledger and holding policy.** Sleeve-keyed positions, mark-to-market equity from a local journal, per-strategy flatten. Change the carried-position rule, daily stop, watchdog, dead-man, account guard and dashboard functions together. Still no rule holds overnight.
7. **Order manager and execution policy.** Standalone stops, auction and limit orders, partial fills, rejects, start-up reconciliation.
8. **Portfolio constructor** and the new gate fields.
9. **Research on the same loop.** Daily bar store, raw prices plus corporate actions, point-in-time universe; walk-forward with continuous equity; evaluation v2; trial registry in the database. Test multi-day rules here (decision 14, step 1).
10. **First multi-day rule in paper**, then delete the old simulators.
11. **Always-on host**, scheduler, alerts, backups (can start in parallel from step 1).

Decision 14 says "test first, engine later". The trap is to write a seventh simulator for the multi-day tests. Steps 2 to 5 are small enough to do first, so that the rule which passes is already the code that trades.

---

## 5. Top 15 actions

| # | Action | Team | Size | Unblocks |
|---|---|---|---|---|
| 1 | Engine spine: `Broker` protocol, `SimBroker`, `SimClock`, one runner; prove the current engine reproduces the replay on a committed fixture in CI | backend-quant | L | same decision code in backtest and live; ends six simulators; honest tests of order types |
| 2 | `Strategy` interface and registry; port dip, SPY momentum, ORB, news continuation; schedule and holding policy per strategy | backend-quant | L | several rules, daily rules, issue #112 |
| 3 | Data foundation: raw bars plus corporate actions, daily bar store, earnings calendar, point-in-time universe with delisted names, fix the XLC hole | data | L | honest ten-year tests of multi-day rules (#113); holding through splits and dividends |
| 4 | Ledger: sleeve-keyed positions, mark-to-market equity from a local journal, daily stop on the day's equity change, per-strategy flatten | backend-quant | L | any overnight position; correct Sharpe and drawdown |
| 5 | Evaluation gate v2: daily mark-to-market returns, autocorrelation-robust t, matched-holding random baseline, alpha against SPY total return, per-strategy fingerprints, evidence in time not trade count, portfolio-level view | qa-ops (with backend-quant) | M | judging multi-day rules; decision 14 step 3 |
| 6 | Order manager and execution policy: intent-first journal, partial fills, rejects, standalone stops, open and close auction orders, marketable limits, reconciliation from account activities | backend-quant | L | cheaper execution; crash safety; D3-D8 |
| 7 | Re-scope fail-safes for overnight: watchdog and dead-man cancel entries and keep stops instead of liquidating; account guard by sleeve; gap-scenario sizing and overnight exposure cap in the gate | qa-ops | M | must ship with the first overnight position |
| 8 | Portfolio constructor: strategy budgets, gross, per-name, sector and beta limits, volatility target, cash buffer | backend-quant | M | several rules sharing one pot |
| 9 | Research platform: trial registry in the database (append-only, runner refuses without it), experiments as data, feature cache, walk-forward with continuous equity | backend-quant | M | fast, comparable tests; one trial count everywhere |
| 10 | Always-on operation: VM with restart policy, scheduler for pre-open and post-close jobs, releases pinned by tag, alerts (push or e-mail, health-check ping), log shipping, backups, a second paper account as staging | qa-ops | M | no manual start; supervised overnight risk |
| 11 | Quick fixes in today's engine: tick resume and error streak (D6), per-position isolation in flatten (D7), reject handling (D4), fingerprint globs (G2), trial count and Docker path (F5) | backend-quant | S | a soak run that can be trusted meanwhile |
| 12 | Cost model: measure spreads from quotes by liquidity and time of day, cap participation, auction and limit models, raw prices, monthly check against paper fills | data | M | fair comparison of rules and order types |
| 13 | Hygiene: one locked dependency set, delete dead code and unused tables, single source for limits and rule parameters, mypy and import rules in CI, split `core.py` | qa-ops | M | lower change risk for every item above |
| 14 | Dashboard for multi-day: open positions and exposure, mark-to-market pot line, per-strategy sleeves, holding periods; rewrite `kb_overview`, `kb_report`, `kb_attribution`, `kb_evidence` | frontend | M | the owner can see a swing book |
| 15 | Agent room as a plug-in veto on `Signal`s; for daily rules a pre-open review of the candidate list (no latency cost); counterfactuals through `SimBroker` | agents-ai | S-M | keeps the room measurable in the new engine |

Items 3, 10, 11 and 13 can start today in parallel with 1.

---

## 6. Empirical results on record

### Ten-year walk-forward, variant A (`docs/m0/walk_forward_10y.md`)

Code `6510fad`, run 12 Nov 2026, runtime 707 s. 288 stocks (300 symbols with ETFs and SPY). 115 monthly folds, train 12 months, test 1; 27 configurations (dip z -1.5 / -1.75 / -2.0; stop 1.0 / 1.5 / 2.0 ATR; target 1.5 / 2 / 3 R); selection by train Sharpe after costs with at least 100 train trades. Out of sample 1 May 2017 to 6 Nov 2026, 2,395 sessions.

| | Tuned monthly | Default settings |
|---|---|---|
| Trades | 10,124 | 17,298 |
| Trades per month | 88.0 | 150.4 |
| Net return | -58.99% | -75.69% |
| Mean per trade (bp of $100k equity) | -0.87 | -0.80 |
| t-stat per trade | -8.09 | -11.17 |
| Hit rate | 44.7% | 42.4% |
| Daily Sharpe, annualised | -1.81 | -2.47 |
| Max drawdown | 59.69% | 75.84% |
| Deflated Sharpe (3,105 trials) | 0.0 | 0.0 |

SPY over the same sessions: +208.84%, Sharpe 0.73 (price only). Overfitting probability across the grid 0.009. Tuned beat default in 67.8% of folds. The strictest dip (z = -2.0) was chosen in 85 of 115 folds.

### Experiments 001 (`docs/m0/experiments_001.md`)

Same window and folds; deflated Sharpe counted against 8,286 logged configurations.

| Experiment (tuned monthly) | Trades | Net return | Gross bp | Net bp | t | Hit rate | Sharpe | Max drawdown | Months positive |
|---|---|---|---|---|---|---|---|---|---|
| control (variant A) | 10,124 | -58.99% | -0.041 | -0.87 | -8.09 | 44.7% | -1.81 | 59.7% | 30 of 115 |
| mornings (no entry after ~10:30) | 6,438 | -40.03% | 0.050 | -0.78 | -5.03 | 46.1% | -1.19 | 40.5% | 40 |
| wide stop (2.5-4 ATR, same $ risk) | 11,085 | -29.14% | -0.006 | -0.31 | -5.45 | 48.8% | -1.16 | 29.8% | 43 |
| high VIX (above 18-26) | 3,841 | -27.36% | -0.164 | -0.82 | -5.67 | 42.7% | -1.17 | 27.5% | 22 |
| mornings + wide stop | 6,568 | -19.25% | 0.026 | -0.32 | -3.52 | 48.7% | -0.80 | 20.0% | 44 |

Basis points are of equity per trade. Best fixed setting: VIX > 22, -14.89%, gross +0.19 bp, net -0.44 bp, t = -2.92, trading in 59 of 115 months.

Cost split of the control (average position $13,907):

| | $ | bp of the position per trade |
|---|---|---|
| Gross P&L | -4,139 | -0.29 |
| Spread | -31,374 | 2.23 |
| Price impact | -44,193 | 3.14 |
| Slippage past stops | -3,611 | 0.26 |
| Fees | -4,260 | 0.30 |
| Net | -87,578 | about -6.2 |

Gross by exit: stops -376,696; targets +140,868; 15:55 exits +130,491; trailing stops +101,199.

Conclusions on record: no edge before costs (gross between -0.16 and +0.19 bp in every experiment); the whole loss is costs of about 6 bp per round trip; variant A is retired as a gate candidate.

### Earlier 17-month replays, 59 stocks (`docs/m0/replay_001.md` to `replay_003.md`)

| Variant | Window | Trades | Net | t | Versus random entries |
|---|---|---|---|---|---|
| v1 (target capped at VWAP) | Feb 2025 - Nov 2026 | 642 | -4.0% (gross -0.7%) | -2.38 | 97th percentile |
| A, design | Feb 2025 - Jun 2026 | 750 | +0.08% | 0.03 | 100th |
| A, hold-out | Jul - Nov 2026 | 187 | +0.70% | 0.53 | 95th |
| B (quality >= 70, dip <= -2 sigma), design | Feb 2025 - Jun 2026 | 25 | -0.78% | -2.07 | 5.5th |

### Caveats that apply to all of the above (from this audit)

- Survivorship: today's large caps; and no communication-sector names before autumn 2018 (E3).
- Costs are overstated for heavily split names in early years (E4) and uncalibrated overall (F4).
- Half-days simulated as full days; the "FOMC 2025-2026 only" caveat in the reports is out of date (F3).
- Each month starts from flat equity (walk-forward design).
- None of these can turn a zero gross result into an edge. They matter for the next rules.

---

## Things I could not verify, and how to check each

| Question | How to check |
|---|---|
| Does FRED publish yesterday's VIX close before 08:45 ET? | log the last observation date at `prepare_day` for a week |
| How often does a symbol lack an IEX bar in the 09:30 minute (excluded all day)? | count NaN first-minute opens per day in the live bars |
| How often does the 5 s quote rule reject live candidates? | count `gate_run.checks->'quote_age_s'->>'state'` in the paper run |
| Size of the split-adjusted cost bias | `cost_spread / (qty * entry)` by symbol and year from the walk-forward trades CSV |
| Size of the XLC hole | trades by sector and year from the same CSV |
| Does a replaced stop leg appear under the parent order? | issue #11 on paper: trail a stop, let it fill, read the parent with `nested=true` |
| Does Alpaca cancel good-till-cancelled stops on a split? | Alpaca corporate-actions documentation; then a paper test |
| Cancel-then-close refusals | search engine logs for 403 on `DELETE /v2/positions` |
| Coverage of the news archive in 2016-2018 | headlines per symbol per year in `data/news` |
| Is the `sb_secret_` constant in the outage-drill test synthetic? | compare with the real key's length and prefix; rotate if in doubt |
