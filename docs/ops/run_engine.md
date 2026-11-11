# Running the Kaibot engine

The engine is one Python process (or one Docker container). It only makes **outgoing** connections:
Alpaca paper (orders), Alpaca market data, FRED (VIX) and Supabase (results, heartbeat, commands).
Nothing connects in, so it needs no open ports, domain or web server, and it runs the same on a laptop or any cloud VM.

```
Mac / VM ──► Alpaca paper + data, FRED
   │
   └──► Supabase (kaibot schema, service_role key)  ◄── Dashboard on Vercel (owner-only kb_* functions)
```

## On the Mac (now)

One-time:
1. `git clone https://github.com/falconomad/kaibot ~/Projects/kaibot`
2. Add to `~/Projects/kaibot-secrets/.env`:
   `SUPABASE_SERVICE_KEY=...` (Supabase → Project Settings → API keys → **Secret key** `sb_secret_…`; the legacy `service_role` key also works). It never leaves this file.
3. Either Docker Desktop running, or Python 3.12+ (`brew install python@3.13`).

Every trading day, before 15:30 Berlin (09:30 New York):
```
cd ~/Projects/kaibot
./scripts/run_engine.sh --check   # keys, account, Supabase: prints a short report
./scripts/run_engine.sh           # runs until Ctrl-C; keeps the Mac awake while it runs
```
- Start it at least 30 minutes before the open: it loads ~70 sessions of history first (a few minutes the
  first time, seconds afterwards).
- `caffeinate` keeps the Mac awake on power. Closing the lid still sleeps a MacBook unless an external screen is attached.
- If the Mac sleeps or the engine stops: open trades keep their stop-loss and target at Alpaca. The 15:55 close-out
  does not happen; the engine closes any position left from an earlier day at the first tick of the next open.
  The dashboard shows "Engine offline" after 90 seconds without a heartbeat.

## Moving to a cloud VM later (Google, Oracle, anything with Docker)

```
git clone https://github.com/falconomad/kaibot && cd kaibot
scp mac:~/Projects/kaibot-secrets/.env ./secrets.env          # or create it on the VM
KAIBOT_ENV_FILE=$PWD/secrets.env docker compose up -d --build   # restarts automatically
docker compose logs -f engine
```
Stop the engine on the Mac first: two engines on one paper account would trade twice. The engine lease (next
section) now refuses the second one, but stopping the first cleanly is still the right order: it hands the lease over at once.
A 1 GB VM is enough (pandas + ~70 symbols × 70 sessions of minute bars).

## Engine lease (one engine, whatever the machine)

`scripts/engine_guard.sh` stops a second engine on the **same** machine. The lease stops one on **another**
machine (Mac and a cloud VM). It is one row in Supabase, `kaibot.engine_lease` (migration `0013_engine_lease.sql`):
who holds it (`holder`, `host`), since when (`started_at`) and until when (`expires_at`).

| Moment | What the engine does |
|---|---|
| Start-up, before it touches the broker | Takes the lease with one atomic database call. Free, expired or already its own: it starts. |
| Every heartbeat (about every 10 s) | Renews the lease for another 90 s. The heartbeat shows `lease: ok`. |
| Ctrl-C / SIGTERM | Releases the lease, so another machine can start at once. |
| Crash, power loss, network gone | Nothing: the lease runs out 90 s after the last renewal and the next engine to start takes it over (event `engine_lease_takeover`). |
| Restart on the same machine | Gets its own lease back at once (the holder id is kept in `data/live/engine_holder_id`), so a crash does not lock the machine out. |

`./scripts/run_engine.sh --check` prints the current lease (`engine_lease`: host and times, or `free`) without taking it.

### Messages and what to do

| Log line (exit code 3 unless noted) | Meaning | Do this |
|---|---|---|
| `Another Kaibot engine holds the lease: host '…', started …` | An engine on that host is running, or died less than 90 s ago. This one did **not** start and touched nothing. | Stop the other engine (Ctrl-C, `docker stop kaibot-engine`, `docker compose stop engine`), then start this one. If the other machine is already dead, wait 90 s and start again. |
| `The engine lease could not be checked (…)` | Supabase is unreachable, or migration 0013 is not applied. Without the lease the engine cannot know it is the only one, so it does not start. | Run `./scripts/run_engine.sh --check`. `supabase: FAILED` is a network or key problem. Only `engine_lease: FAILED` means the migration is missing. |
| `engine lease renewal failed (…): no new entries until it works again` (keeps running) | A renewal call failed. The heartbeat shows `lease: unverified`. **No new entries**; open positions are still managed (stops, 15:55 flatten). | Usually a Supabase or network blip: it clears itself on the next good renewal (`engine lease renewed again`). If it lasts, check the network. |
| `The engine lease is no longer ours` then `engine stopped: lease lost` | This engine went quiet for more than 90 s (laptop slept, network dropped) and another engine took over, or someone released the lease by hand. This one stops; event `engine_lease_lost`. | Nothing to fix: the other engine is in charge. Open positions keep their broker-held stops and the other engine manages them. Decide which machine should run, and stop the other. |

With `docker compose` (`restart: unless-stopped`) a refused engine exits and Docker starts it again, with a growing
pause between tries. It keeps being refused while the other engine lives and takes over by itself within a few
minutes of the other one dying. That is a standby, not a second engine. If you do not want a standby, `docker compose stop engine`.

### Releasing a stuck lease safely

A lease cannot stay stuck longer than 90 s by itself: nothing but a running engine renews it. So if the message
keeps coming, **an engine is alive somewhere**. Find it before touching the row:

1. In the Supabase SQL editor: `select host, started_at, renewed_at, expires_at, now() from kaibot.engine_lease;`
   If `renewed_at` moves every ~10 s, the engine on `host` is running. Stop it there. Do not delete the row.
2. On each machine that may run Kaibot: `./scripts/engine_guard.sh --list` (prints nothing when no engine runs).
3. After a clean stop the row is gone. After a kill, wait until `expires_at` has passed (90 s), then start.
4. Only if `renewed_at` no longer moves, `expires_at` is in the past and a start is still refused (it should not
   happen): outside 09:30-16:00 ET, with every engine stopped, run `delete from kaibot.engine_lease;` and start again.

Never delete the row or shorten `expires_at` while `renewed_at` is moving. The running engine would notice at
its next heartbeat and stop (`lease lost`), but in the seconds before that both engines could act on the account.
Do not copy `data/live/engine_holder_id` to another machine: two machines with the same id would both be let in.

### What the lease does not do
- It does not replace `engine_guard.sh`: two engines on the same machine share one holder id.
- It does not cover the watchdog or `--check`: neither trades, neither takes the lease.
- While the lease cannot be renewed the engine still manages exits. It only stops completely when the database
  says the lease belongs to someone else.

## What it does each minute
0. Renews the engine lease with each heartbeat (see above). Not renewable: no new entries.
1. Applies dashboard commands (new run, pause/resume, flatten and stop, level from next session).
2. Pulls the latest IEX 1-minute bars; syncs order and fill status from Alpaca.
3. Marks to market; daily stop (flatten + halt) and run-drawdown end are checked.
4. Trails stops once a trade is +1R (stop to breakeven, then 1.5 ATR below the highest close).
5. 15:55 ET (5 minutes before an early close): sells everything.
6. Every 5-minute bar from 09:35 to 15:45: Variant A candidates (same code as the replay), news check,
   risk gate, then a bracket order (market buy + broker-held stop + 2R target).
7. Writes signal, gate, order, fill, trade, ledger, daily result and a heartbeat to Supabase.
8. Shadow strategies (S14, `engine/shadow.py`, never traded): ORB on Stocks in Play (`strategies/orb.py`) and news
   continuation (`strategies/news_continuation.py`) write `signal` rows with `mode = 'shadow'`, the would-be qty from
   the risk gate in `features`, and at day end `features.counterfactual` (fill, exit, $ P&L, R after shadow costs)
   plus a `shadow_session_end` event (totals and the hook's cost). Any shadow error is logged as `shadow_error`
   (once per kind per day) and never affects live trading. Replay: `scripts/run_shadow_replay.py --strategy orb`.

## Request budgets
Every outgoing call (Alpaca data, Alpaca paper trading, FRED, SEC, FINRA, GDELT) waits for its source's budget in
`config/sources.yaml` (`kaibot.common.ratelimit`): sliding windows (`budget_per_s/_min/_hour`) plus an optional
minimum gap. All clients of one source in a process share one budget; a 429 makes every caller of that source back
off together, and 429/5xx are retried with backoff (1, 2, 4, 8, 16 s). Budgets are per process, so the engine and a
script such as `fetch_history.py` running at the same time each get the full budget: keep the sum under the
provider limit (Alpaca: 150 + `--per-min` < 200/min). Nightly alt-data collection: see `alt_data.md`.

## Known differences from the replay
- Free data: today's bars are IEX, not SIP. Prices match closely; volume is rescaled by each symbol's
  SIP/IEX ratio over the prior 20 days, and VWAP is IEX-only.
- Real fills (Alpaca paper) replace the replay's next-bar-open model; the shadow cost model still re-prices every
  fill and the dashboard reports the worse of the two.
- Whole shares only (bracket orders don't take fractions): with a small pot, expensive stocks can size to 0 and are skipped.
- The agent room is off in M1 (rules only).
