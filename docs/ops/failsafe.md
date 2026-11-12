# Fail-safes (M1: stories S11, S15, S16, S18)

What stops Kaibot from adding risk when something breaks, from the inside out.

| Layer | Runs where | Protects against | What it does |
|---|---|---|---|
| Broker-held brackets, **GTC** legs | Alpaca | engine or laptop dead at any time | stop (and 2R target) stay at the broker until filled or the engine closes the position; nothing sits overnight unprotected |
| Engine halts | engine | bad state | no new entries for the session (exits keep being managed) |
| Engine lease | Supabase row + engine | a second engine on another machine | start-up refused while another engine's lease is live; renewal failing: no new entries; lease taken by another engine: this one stops ([run_engine.md](run_engine.md#engine-lease-one-engine-whatever-the-machine)) |
| Watchdog | separate process, same machine | engine crashed or hung | heartbeat > 90 s: cancel unfilled `kb-` entry orders; still stale at close minus 5 min (15:55 ET, 12:55 on early closes): close every position, retried until close minus 4 min |
| Dead-man | Supabase (pg_cron + pg_net) | laptop asleep, off or offline | close minus 4 min (15:56 ET; 12:56 on early closes once 0017 is applied), heartbeat > 3 min old: `DELETE /v2/positions?cancel_orders=true` on the **paper** account |

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
