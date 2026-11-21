# Outage drill (story S40)

Goal: after the engine dies, a restart is healthy again in **under 5 minutes**, without adding risk.
"Healthy" means a fresh engine heartbeat (at most 30 s old, written after the restart) with `account_clean: true`.
Paper only. Fail-safe layers: [failsafe.md](failsafe.md). Running the engine: [run_engine.md](run_engine.md).

## What happens in each outage

| Scenario | While it lasts | On restart / recovery | Test |
|---|---|---|---|
| **a. Engine crashes** (SIGKILL, laptop sleeps) and starts again | Brackets are held at Alpaca (GTC stop + 2R target). Nothing new is opened. | `startup()` rebuilds positions from open `trade` rows (stop leg id and price included), reconciles with the broker (`account_clean`), and rebuilds today's state from the store: entries placed, realized P&L, loss streak/pause and a **daily stop already hit**. The first tick syncs orders and manages the position (trailing stop, 15:55 flatten). Takes about 1 minute (next tick at :07). | `test_a_*` |
| **b. Engine dead for 10 min** | After 90 s without a heartbeat the watchdog cancels unfilled `kb-` entry orders and writes a `watchdog` halt. Broker-held stops and targets are left alone. If a stop fills meanwhile, the broker closes the position. | Same as (a). A cancelled entry is closed as `not_filled`, and a stop that filled while the engine was down is booked once. The watchdog logs `watchdog_recovered`. | `test_b_*` |
| **c. Data or broker API down** | Every loop iteration raises (since M9 1.5, #430, not for the minute's market-data read alone: that is one `engine_error` event, step `market_update`; the tick goes on without new bars, no intraday entry is scanned that minute and the multi-day send still runs). Heartbeats keep coming with `state: error`. No orders are sent and the stops stay at the broker. **3 failed iterations in a row (about 30 s) trip the `engine_errors` kill switch: no new entries for the rest of the session.** | Healthy on the first tick after the API is back (missed bars are backfilled). Exits are still managed, including the 15:55 flatten. Entries come back at the next session, or after a restart. | `test_c_*` |
| **d. Crash between the broker accepting an order and the engine writing its row** | The order (and maybe the fill) exists only at Alpaca. | The engine adopts it only if its `client_order_id` is exactly `kb-<run>-<signal>-1` for an **approved** signal of this run that has no trade row. It writes the missing order/trade rows, logs `order_adopted`, and manages the position from there. Any other unknown order or position still sets `account_clean: false` and blocks entries. Client ids are deterministic, so a re-submission would be refused by Alpaca (422), and the adapter returns the existing order. | `test_d_*` |
| **e. Two engines at once** | See [Two engines](#two-engines). | `run_engine.sh` refuses to start a second engine on the same machine. On another machine the engine lease refuses it (`tests/test_engine_lease.py`). | `test_e_*` |

All of these run on fakes in `tests/test_outage_drill.py`. Its `Sim` mirrors the engine loop in `kaibot.engine.__main__` (10 s iterations, a tick at second ≥ 7, a heartbeat each iteration) and the watchdog's 15 s poll. The fake venue keeps filling legs while the engine is down.

## What the Engine health page shows

| Moment | Header pill | Processes card | Events / halts |
|---|---|---|---|
| Engine killed, < 90 s | Engine live (heartbeat ageing). With `--graceful`, offline at once (`state: stopped`) | Engine: last heartbeat counting up | — |
| > 90 s | **Engine offline · last seen …** | Engine dot grey (stale). Watchdog: "sees engine N s" | In session only: halt "Engine went quiet: watchdog cancelled unfilled entries" |
| Restart, history loading | Engine live | Engine: `starting` / `preparing` | "Engine started", "Session started" |
| Recovered | Engine live · host · N s ago | Engine green, `account_clean` not flagged | "Engine heartbeat back" (watchdog_recovered). `session_restored` / `order_adopted` if they applied |
| Account not clean | — | Engine: **account NOT clean** | "Paper account not clean" |

Since S40 the heartbeat carries `account_clean` outside the session too (`market_closed` / `idle`), so an off-hours drill can see the reconciliation. Before that it was missing whenever the market was closed.

## Running the drill on the Mac

```
./scripts/outage_drill.sh --dry-run          # checks + plan, stops nothing (read-only heartbeat select)
./scripts/outage_drill.sh                    # the drill (refused 09:30-16:00 ET on weekdays)
./scripts/outage_drill.sh --during-session   # in session, loud warning, refused after 15:50 ET
```
Options: `--graceful` (SIGTERM instead of a crash), `--no-wait` (restart at once), `--timeout S` (default 600).

What it does:
1. Refuses unless `ALPACA_BASE_URL` (environment, then `.env`, default paper) is `https://paper-api.alpaca.markets`.
2. Refuses during the session unless `--during-session` is given.
3. Finds the one running engine with `scripts/engine_guard.sh` (Docker `kaibot-engine`, a compose `engine` service, or a Python `-m kaibot.engine`). It refuses if there are none or more than one, and refuses if the heartbeat is not already healthy.
4. **T0**: stops it (`docker kill`, `docker compose kill engine` or `kill -KILL <pid>`) and waits until no engine is left.
5. In session, waits for the watchdog's `engine heartbeat … s old` log line (about 105 s). Outside the session the watchdog idles, so it waits for the heartbeat to be older than 90 s instead (the dashboard then shows "Engine offline").
6. **T1**: restarts with `nohup ./scripts/run_engine.sh --docker|--python >> logs/engine.log` (compose: `docker compose start engine`). `run_engine.sh` runs `git pull --ff-only` first, so do the drill on the code you mean to run.
7. Polls `kaibot.heartbeat` every 5 s with a read-only PostgREST select, using `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` from the `.env`. The key goes to curl on stdin and is never printed or put on a command line.
8. Prints the downtime (T0→T1), the recovery time (T1→healthy), and **PASS** (recovery < 300 s) or **FAIL**. Exit codes: 0 pass, 1 fail, 2 refused.

After the drill the engine keeps running in the background. Follow it with `tail -f logs/engine.log` (Docker: `docker logs -f kaibot-engine`) and stop it as usual.

The first drill after deploying S40 needs the engine to have been restarted once on the new code. Older engines don't send `account_clean` outside the session, so the pre-check refuses.

## Pass criteria
- Recovery (T1 → fresh heartbeat with `account_clean: true`) **< 300 s**. Expected: 10–70 s, plus the history load if the cache is cold.
- No new `kb-` order appears at Alpaca because of the restart. Check Alpaca's paper dashboard: one entry per signal.
- Open positions still have their stop leg at the same price.
- In session: a `watchdog` halt and then "Engine heartbeat back" on the Health page.

## On FAIL
1. **No heartbeat at all**: look at `logs/engine.log` (or `docker logs kaibot-engine`). Run `./scripts/run_engine.sh --check`, which reports keys, Alpaca, Supabase and FRED one by one.
2. **Heartbeat but `account_clean: false`**: the paper account has positions or orders the engine doesn't know (event `account_not_clean` lists them). Close or cancel them in the Alpaca paper dashboard, then restart. Never adopt by hand during the session.
3. **Stuck in `preparing`**: a cold history cache (first run, or `data/live/history` deleted) can take a few minutes. Run the drill again once it's warm. If it is slow every time, file an issue.
4. **`state: error`**: an API is down (Alpaca, Supabase, FRED). Entries are already halted (`engine_errors`). Wait for the API, or stop the engine if positions need attention. Brackets stay at the broker.
5. **The watchdog never logged** (in session): it isn't running. Python: `logs/watchdog.log` and `data/live/watchdog.pid`. Docker: `docker ps -f name=kaibot-watchdog`. `run_engine.sh` starts it.
6. Record the result (date, mode, down/recovery seconds, PASS/FAIL, what you did) in issue #40 or a follow-up.

## Two engines
How it is enforced today:
- **Same machine, Docker via `run_engine.sh`**: the container name `kaibot-engine` is fixed, so a second `docker run --name kaibot-engine` fails.
- **Same machine, anything else**: since S40, `run_engine.sh` calls `scripts/engine_guard.sh` before starting (not for `--check`, which never trades) and refuses if an engine already runs there: Docker, compose or Python. Before S40 there was no guard in Python mode or between compose and `run_engine.sh`.
- **Different machines** (Mac and a cloud VM): the **engine lease** (issue #81, migration 0013). One row in Supabase is taken at start-up, renewed with every heartbeat and released on a clean stop. A second engine is refused while the lease is live and can take over 90 s after the holder dies. A restart on the same machine gets its own lease back at once, so the drill above is not slowed down. Messages and how to release a stuck lease: [run_engine.md](run_engine.md#engine-lease-one-engine-whatever-the-machine).

## Three sessions down with multi-day positions

What happens when the engine is off for three whole trading sessions while `trend_momentum` holds positions overnight.
It is drilled in CI by `tests/test_outage_drill_multiday.py` on the simulated venue (SimBroker with cost model v1 and
a cash book). The test runs the real engine with `KAIBOT_MULTIDAY=on`, the real watchdog, and the dead-man's SQL
(migrations 0017, 0019 and 0022) in a throwaway local Postgres, never Supabase. It runs twice: with the laptop up
(watchdog and dead-man) and with the laptop off (dead-man alone).

The story: on Monday the rule holds B and C, with no stop, as registered. It decides after the close to buy D on
Tuesday, and the engine dies that evening. Tuesday to Thursday the engine is down. On Wednesday C opens 15% lower.
B climbs every day. On Friday at 09:00 the engine starts again.

### Each session while the engine is down

| | Watchdog (laptop, every 15 s) | Dead-man (Supabase, every minute) |
|---|---|---|
| When it acts | at the first poll after 09:30 (the heartbeat is older than 90 s) | once the heartbeat is over 3 minutes old and the market is open: it reads the account, then acts on the next run |
| Protective stops | the first day: one GTC stop per position, whole shares, **10% below the last price** (the rule has no stop of its own), client id `kbwd-...` | the same (`kbdm-...`) if no stop rests yet. With the laptop up the watchdog's stop is already there, so it adds none. It never adds a second one |
| Later days | the stop is still resting (GTC), so nothing new is sent | the same |
| Buying | never. Kaibot buy orders still open are cancelled. The D buy decided on Monday is never sent, because sending is the engine's job | never, the same |
| Close minus 5 / 4 minutes | sells intraday shares only. Multi-day shares are kept (`kept` in `watchdog_flatten`) | sells intraday shares only. Nothing multi-day is closed or sold |
| Alarm | one `watchdog` halt row each session ("multi-day positions are kept"), plus `watchdog_protect` / `watchdog_flatten` events | one `deadman` halt row each session ("engine silent ..."), plus a `kaibot.deadman_pass` row per pass |

### When the engine starts again
- The account check is clean, there is no drift halt, and the reconciliation shows no difference before the send.
- C's stop, which filled on Wednesday, is booked **once** to C's sleeve. B's resting fail-safe stop is taken over as
  B's own (`stop_adopted`).
- The rule decides from Thursday's close, the last one before the restart. The evenings it missed are not decided
  after the fact, and nothing from Monday's decision is sent.
- From 15:50 it cancels B's fail-safe stop (the rule has no stop), then sends each order once: C bought back, D
  bought. A second crash at 15:53 and another restart send nothing again.
- After the close the ledger equals the broker, shares and cash to the cent.

### What is protected
- Every multi-day position gets a broker-held stop in the first minute of the first session (about two
  minutes with the laptop off: one dead-man run reads the account, the next acts). The stop then sits at Alpaca, so it keeps working even if the watchdog and Supabase both go down later.
- A fall of more than 10% from that first price is sold by the stop, once, and booked once on restart.
- Nothing is bought, and nothing multi-day is sold by the fail-safes themselves. Someone is told every session.

### What is not protected
- **Gaps.** A stop is not a price guarantee. When a stock opens below its stop, the stop fills at the open. In the
  drill C fills about 15% down, not 10%.
- **The stop never moves.** It stays 10% below the price on the first morning. A stock that rises for three days and
  then falls gives back the whole rise plus 10% before the stop fills.
- **The rule does not run.** No decisions, no sales the rule would have made, no rebalancing and no buys, for as long
  as the engine is down. A decision evening missed while the engine is down is skipped, not caught up. Falls
  smaller than 10% are simply held.
- **Before the first stop.** If neither the watchdog nor the dead-man can reach Alpaca on the first morning (laptop off
  and Supabase or its keys down), no stop is ever placed. `trend_momentum` has no stop of its own, so its positions
  are then fully unprotected until one of them is back.
- **After the restart** the fail-safe stops are cancelled at 15:50 by design. While the engine runs, the rule has no
  stop (see [paper_start.md](paper_start.md)).
- Known gap #344: on a day the engine dies right after a 15:50 buy that never fills, a position of exactly that many
  shares that nothing else explains can be kept overnight (with an emergency stop) instead of sold.
