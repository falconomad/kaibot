# Fail-safes (M1: stories S11, S15, S16, S18; overnight-safe since M7 S80, #120)

What stops Kaibot from adding risk when something breaks, from the inside out. Since S80 (decision 15 sections 4
and 6) none of them sells a **multi-day** sleeve: they cancel entries, keep (or re-create) its protective stop and
raise the alarm. Intraday sleeves are flattened at the close as before. Only run drawdown, a new run and "Flatten
and stop" sell everything.

| Layer | Runs where | Protects against | What it does |
|---|---|---|---|
| Broker-held **GTC** stops | Alpaca | engine or laptop dead at any time | intraday: bracket legs (stop and 2R target); multi-day: one standalone GTC stop per sleeve, whole shares (S78, `engine/protect.py`). They stay at the broker until filled or replaced |
| Engine halts | engine | bad state | no new entries for the session (exits keep being managed); `Engine.entry_block()` says why, for the multi-day target path too |
| Daily stop | engine | a bad day, overnight gaps included | the day's change in mark-to-market equity (start = yesterday's close mark) at the level's stop: entries blocked for the day, intraday sleeves flattened, multi-day sleeves keep their stops |
| Engine lease | Supabase row + engine | a second engine on another machine | start-up refused while another engine's lease is live; renewal failing: no new entries; lease taken by another engine: this one stops ([run_engine.md](run_engine.md#engine-lease-one-engine-whatever-the-machine)) |
| Watchdog | separate process, same machine | engine crashed or hung | heartbeat > 90 s: cancel open Kaibot buys (`kb...`) not yet completely filled, halt row + event; once a minute: every multi-day sleeve has its stop at the broker, a missing one is re-created; still stale at close minus 5 min (15:55 ET, 12:55 on early closes): sell the **intraday** shares only, retried until close minus 4 min |
| Dead-man | Supabase (pg_cron + pg_net) | laptop asleep, off or offline | with 0019: heartbeat > 3 min old while the market is open: the same plan as the watchdog (cancel entries, verify and re-create stops, alarm); from close minus 4 min to the close: sell the intraday shares only. Before 0019 (0006/0017): `DELETE /v2/positions?cancel_orders=true` at close minus 4 min, i.e. the whole account |

Restart and recovery (crash, 10-minute outage, API outage, half-written order, two engines): see
[outage_drill.md](outage_drill.md) and `scripts/outage_drill.sh`.

## Watchdog and dead-man on the last minutes of a session (S74c #147, S74d #154)
- **The close** is the earlier of the broker clock's `next_close` and the broker calendar's close for the day,
  so on early-close days (13:00 ET) the watchdog flattens at 12:55. The calendar is read once a day, only when
  the engine is already stale, and never inside the flatten window (from the clock's close minus 5 minutes the
  clock's `next_close`, already calendar-aware, decides alone); the happy path makes no extra calls.
- **The first flatten pass** of the day always tries every position, however long it takes.
- **Retries** run until close minus 4 minutes, the dead-man time. If the dead-man will act (the engine heartbeat
  will be older than 3 minutes at the dead-man time, `DEADMAN_STALE_S`), a retry starts no close within
  `CLOSE_BUDGET_S` (25 s) of the dead-man time; a symbol it does not start counts as failed, so the day is not
  marked flattened. 25 s is a slow close, not a worst case: one close can overrun it (several order-path calls,
  each with a 20 s deadline), but only that one close. If the dead-man will not act (the engine stopped less
  than 3 minutes before it), retries are not cut. A watchdog that first sees the stale engine after the dead-man
  time still makes one pass, as a backstop.
- **A failed open-orders read** (entry cancels) is logged and returned as `entry_read_failed`; the halt and the
  flatten of that poll still happen.
- **Order-path budget:** a trading call that gives up because its rate-limit slot comes after its deadline no
  longer books that slot, so it does not push other callers back.
- **Dead-man on early closes:** migration `0017_deadman_early_close.sql` (**not applied yet; the stakeholder
  applies it**) adds `kaibot.market_calendar_exception` (holidays and 13:00 closes, NYSE calendar 2026-2028) and
  two more pg_cron jobs at 12:56 ET. `deadman_flatten()` then acts at the day's close minus 4 minutes and never on
  a holiday. Before 0017, early-close days have no off-box dead-man (the GTC stops still protect positions).
  **Every autumn** add the next year's holidays and early closes to that table, next to `macro_calendar.yaml`.
  Check after applying: `select jobname, schedule from cron.job where jobname like 'kaibot_deadman%';` shows four
  jobs, and `select kaibot.deadman_flatten();` outside the due minutes returns `not 15:56 ET` (or `not 12:56 ET`).

## Engine halts (fail closed)
Each writes a `halt` row and an event; all clear at the next session start, except `three_stops` (the run is paused until you press Resume).

| Kind | Trigger |
|---|---|
| `daily_stop` | day P&L at the level's daily stop: flatten and halt |
| `run_drawdown` | drawdown from the run's peak at the level's cap: flatten and end the run |
| `drift` | engine positions differ from the broker's on two consecutive minutes |
| `engine_errors` | 3 loop iterations in a row raised |
| `calendar_stale` | today is after `covers_until` in `config/macro_calendar.yaml` |
| `three_stops` | 3 daily stops within 14 calendar days (~10 sessions): run paused |
| lease unverified (no halt row) | the engine lease could not be renewed: entries blocked until a renewal succeeds; shown as `lease: unverified` in the heartbeat |
| account not clean | positions or open orders on symbols the engine doesn't hold (entries blocked until clean) |

Also enforced per entry by `risk/gate.py`: quote older than 5 s, missing bars, buying power (the smaller of the pot
and the paper account's own figure), the ±30 min event blackout (FOMC 14:00; CPI 08:30 is pre-open), and VIX > 35.

## Setup
- The watchdog starts automatically with `./scripts/run_engine.sh` (Docker: container `kaibot-watchdog`;
  Python: `logs/watchdog.log`, pid in `data/live/watchdog.pid`). It keeps running when the engine stops: that is
  its job. After updating the code, restart it: `kill $(cat data/live/watchdog.pid)` then start the engine again.
- The dead-man (migration `0006_failsafe.sql`) is inert until you store the **paper** keys in Supabase Vault, in
  the SQL editor (never in chat or the repo):
  ```sql
  select vault.create_secret('<paper key id>', 'kaibot_alpaca_paper_key_id');
  select vault.create_secret('<paper secret>', 'kaibot_alpaca_paper_secret');
  ```
  Test it outside market hours: `select kaibot.deadman_flatten(force => true);` with the engine stopped closes all
  paper positions; with the engine running it returns `engine alive`.
- Every autumn, extend `config/macro_calendar.yaml` (Fed tentative schedule ~September, BLS CPI schedule) and
  bump `covers_until`, or entries stop on 1 January.

## Overnight-safe fail-safes (S80, #120)

One plan, two implementations: `src/kaibot/engine/failsafe.py` (the watchdog) and
`migrations/0019_deadman_overnight.sql` (the dead-man). `tests/test_failsafe_drills.py` runs both on the same
accounts and checks that they agree.

- **Entries:** every open buy not yet completely filled (a partly filled one has its rest cancelled; the filled
  shares keep their legs or get their stop) whose client id starts with `kb` (intraday `kb-<run>-<signal>-<n>`, order
  manager `kb<run>-<strategy>-<symbol>-<yyyymmdd>-<purpose>-<n>`) is cancelled. Sells (stops, sales) are never touched.
- **Which shares are multi-day**, per symbol: the larger of what the book says and what the multi-day stops at the
  broker cover (their client ids name the strategy), never more than the broker holds. The book is, for the watchdog,
  the engine's local journal (`$KAIBOT_DATA_DIR/journal.sqlite`, opened read-only), else the ledger rows in Supabase
  (`ledger_position`, the last close); for the dead-man, the ledger rows. Multi-day strategies:
  `strategies/registry.MULTI_DAY` (in SQL: `kaibot.strategy_holding`, kept equal by a test).
- **Stops:** a multi-day sleeve whose shares no resting stop of its own covers gets one: GTC stop sell, whole shares
  (a fraction is never covered), at its last known stop (the order manager's journal, or `ledger_position.stop_px`);
  if none is known, or it is not below the last price, 10% below the last price (`risk/gate.GAP_SCENARIO_PCT`). Never
  for more shares than are free (other sell orders hold shares); what cannot be covered is an alarm (`unprotected`).
  Client ids: `kbwd-<strategy>-<symbol>-<time>-wdstop` (watchdog), `kbdm-...-dmstop` (dead-man). The engine treats
  them as orders on a symbol it holds; the order manager adopting them as the sleeve's own stop is #188, which must
  land before `KAIBOT_MULTIDAY=on`.
- **Never a stop on a flat position (#179's lesson):** the fail-safes only ADD a stop where none covers the shares;
  they never cancel and re-arm one. The watchdog reads the position again right before each new stop and sends
  none if the shares are gone (a stop filled in between). The dead-man acts only on an account read made in the last 3 minutes (older reads expire, and every open read
  expires when the engine beats again; drilled), normally about a minute old,
  so a stop that filled in that minute could be followed by its new stop on a flat position; Alpaca then rejects it
  for want of shares only if shorting is off on the paper account. **Turn shorting off on the paper account**
  before applying 0019 (Alpaca dashboard, paper account, Account Configurations, Shorting off; API:
  `PATCH /v2/account/configurations {"no_shorting": true}`; check `GET /v2/account` shows `shorting_enabled: false`), and keep #179 (the order manager's re-arm) fixed
  before protective stops go live.
- **Fail closed:** a position that neither the book nor a multi-day stop explains counts as intraday and is sold at
  the close minus 5 minutes. So with Supabase down AND the journal unreadable, a multi-day position keeps its place
  only through its own resting stop; one with no stop at all is sold rather than carried unprotected.
- **Alarm:** watchdog: `halt` row `watchdog` and events `watchdog_protect`, `watchdog_flatten` (with `kept`). Dead-man:
  one `halt` row `deadman` per session ("engine silent ...") and a `kaibot.deadman_pass` row per pass with everything
  it did (`result`). The alarm channel of S81 reads these.
- **Engine side:** the close-minus-5 flatten and the daily stop take intraday sleeves only; only intraday positions
  are "carried" (sold at the next open); the account guard and the drift check count the ledger's multi-day sleeves as
  the engine's own; equity counts them from their last close mark (the overnight gap is in the day's change); run
  drawdown, a new run and "Flatten and stop" also sell them (`_flatten_md`), and the sale is booked when it fills.
- **Turning multi-day rules on:** `KAIBOT_MULTIDAY=on` in the engine's environment (the `.env`); default off, and the
  engine refuses to load a multi-day rule without it. It is the explicit switch for the first paper start (#133,
  after Gate B) and needs #188 first. It does not loosen anything else: `config/profiles.yaml` `all_levels.allow_overnight` (the level
  limits, the stakeholder's) still caps overnight exposure at 0 while false, and migration 0019 must be applied first.

### Drills (`tests/test_failsafe_drills.py`, every one with 40 shares of a multi-day sleeve held from the day before)

| Drill | Who acts | What must happen |
|---|---|---|
| Engine killed at 15:50 | watchdog | the closing-auction buy is cancelled; the resting stop is kept (a missing one re-created, whole shares, at the journal's stop price); 15:55 sells the intraday position only; the restarted engine knows the sleeve |
| Engine killed at 15:50, laptop gone | dead-man (SQL) | the same from Supabase: entry cancelled, stop re-created (`kbdm-`), intraday legs cancelled then the position closed in the 15:56-16:00 window, the multi-day position kept; one `deadman` halt |
| Network cut overnight | dead-man, then the engine | the engine and the watchdog cannot reach Alpaca (loop errors, kill switch); the dead-man reads the account at 09:35, finds the stop resting and sells nothing; when the network is back the engine counts the overnight gap, the daily stop blocks entries and the stop stays |
| Supabase down at the open | watchdog | the heartbeat is unreadable (stale): entries cancelled, the missing stop re-created from the local journal; at 15:55 nothing multi-day is sold. With the journal unreadable too: kept through its own stop, or sold if it has none (fail closed) |

The dead-man drills run the migration in a throwaway local Postgres (`tests/pg_harness.py`: stand-ins for pg_net,
Vault and pg_cron; never Supabase). They are skipped where no PostgreSQL binaries are installed.

### Applying 0019 (the stakeholder)

Apply `migrations/0019_deadman_overnight.sql` after 0017 and 0018, before the first multi-day position is held
overnight. It replaces the four `kaibot_deadman_*` jobs by one (`kaibot_deadman`, every minute 13:00-21:59 UTC on
weekdays; the function decides what is due). Check: `select jobname, schedule from cron.job where jobname like
'kaibot_deadman%';` gives one row, and `select kaibot.deadman_check();` outside market hours gives `market closed`.
A test outside market hours with the engine stopped: `select kaibot.deadman_check(force => true);` twice, a minute
apart (the first reads the account, the second acts); `select result from kaibot.deadman_pass order by id desc
limit 1;` shows what it did. It never sells a multi-day position.

### Audit B1: the twenty places that assumed "flat every night"

| # | Assumption (audit `docs/research/engine_v3/code_audit.md` B1) | Status | Where and why |
|---|---|---|---|
| 1 | Flatten at close minus 5; no entries after | changed | S78 (#118): `flatten_all("time")` takes intraday sleeves only (`execution.flatten_at_close`); drilled here. "No entries after 15:45" stays for intraday entries; the multi-day path trades in the closing auction and asks `Engine.entry_block()` |
| 2 | "Carried" positions sold at the next open | changed | `_finalize_day` and `_start_day` mark only intraday positions as carried; a multi-day sleeve is meant to be held |
| 3 | Daily stop sells everything | changed | `flatten_all("daily_stop")` takes intraday sleeves only; entries blocked for the day (`Day.halted`, `entry_block`); multi-day sleeves keep their stops; the halt reason names them |
| 4 | Equity = start + realised today + unrealised since the fill | changed | S77 counted held `Pos` from the last close mark; now the ledger's multi-day sleeves too, at the minute's price (bars, else the broker's `current_price`); a multi-day sale is realised against cost less the gain to the last mark (`_md_realized`). Overnight gaps are in the day's change |
| 5 | Start equity = pot + realised daily results | changed in S77 (#117) | the ledger's last mark-to-market close; unchanged here |
| 6 | Open risk must fit in the room left before the daily stop | unchanged | `risk/gate.py` is two-key code and its open-risk budget is about intraday proposals; multi-day buys are sized by the portfolio builder (#119) under book limits (gap scenario, overnight cap). What changed: the cash a multi-day book holds is no longer counted as intraday buying power (`_decide`) |
| 7 | Watchdog closes every position at 15:55 | changed | `engine/watchdog.py` + `engine/failsafe.py`: cancel entries, verify and re-create stops, alarm, sell intraday shares only |
| 8 | Database dead-man deletes all positions at 15:56 | changed (file only) | `migrations/0019_deadman_overnight.sql`, **not applied**: the same plan in SQL |
| 9 | One position per symbol, exits tied to the entry bracket | changed in S77/S78 | positions keyed by (strategy, symbol); one standalone protective stop per sleeve (`engine/protect.py`); unchanged here |
| 10 | Foreign-position guard | changed | `check_account` and the drift check count the ledger's multi-day sleeves (and orders on their symbols) as known; anything else still blocks entries, including positions left by an ended run (it should have been flat) |
| 11 | New run and run drawdown flatten everything | changed so they still do | decision 15 section 6 keeps them: `flatten_all` for `run_drawdown` and `manual` now also sells multi-day sleeves (`_flatten_md`), the fill is booked to the ledger, and a new run waits until they are sold. "Flatten and stop" likewise |
| 12 | Per-day state object (entries, pause, halts reset daily) | unchanged | these are per session by definition (the daily stop is the day's change; the pause after three losses is an intraday rule). What must cross sessions already does: the run's peak (`run.peak_equity`), three daily stops in 14 days (halt rows), the book (ledger and journal) |
| 13 | Daily result is realised P&L only | changed in S77 | `ledger_day` / `ledger_position` hold the mark-to-market close; multi-day sales now enter the day's result too (item 4) |
| 14 | Gate report books P&L on the exit day | unchanged here | multi-day rules are judged by the mark-to-market Gate B report (`engine/daily_report.py`, #161) and evidence gate v2 (#126); the intraday gate report stays for the dip soak test |
| 15 | Random baseline is intraday | unchanged here | the exposure-matched random control for multi-day rules is #126 / #143 |
| 16 | Walk-forward simulates each month from flat | unchanged here | multi-day rules run as one continuous account (`engine/daily.run_daily`, #161); `walkforward.py` stays for intraday research |
| 17 | Minute-bar arrays; live keeps 70 sessions | unchanged here | multi-day rules read the daily store (`docs/ops/daily_bars.md`, `DailyData`), not the live minute feed |
| 18 | Shadow and room counterfactuals end at the flatten | unchanged | they belong to intraday shadow rules; the room as a pre-open review of the candidate list is #131 |
| 19 | Owner config: flatten and "no overnight" locked | unchanged here | tighten-only multi-day settings are #132; until then the switches are `KAIBOT_MULTIDAY` (this story) and `all_levels.allow_overnight` (the stakeholder's level limits) |
| 20 | Dashboard functions by entry day | changed in S77 | 0018 added `kb_book`, `kb_equity_series` and the mark-to-market start equity of `kb_report`; the multi-day dashboard is #130 |
