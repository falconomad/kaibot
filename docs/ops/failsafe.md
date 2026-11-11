# Fail-safes (M1: stories S11, S15, S16, S18)

What stops Kaibot from adding risk when something breaks, from the inside out.

| Layer | Runs where | Protects against | What it does |
|---|---|---|---|
| Broker-held brackets, **GTC** legs | Alpaca | engine or laptop dead at any time | stop (and 2R target) stay at the broker until filled or the engine closes the position; nothing sits overnight unprotected |
| Engine halts | engine | bad state | no new entries for the session (exits keep being managed) |
| Watchdog | separate process, same machine | engine crashed or hung | heartbeat > 90 s: cancel unfilled `kb-` entry orders; still stale at 15:55 ET: close every position |
| Dead-man | Supabase (pg_cron + pg_net) | laptop asleep, off or offline | 15:56 ET, heartbeat > 3 min old: `DELETE /v2/positions?cancel_orders=true` on the **paper** account |

Restart and recovery (crash, 10-minute outage, API outage, half-written order, two engines): see
[outage_drill.md](outage_drill.md) and `scripts/outage_drill.sh`.

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
