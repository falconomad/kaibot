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
Stop the engine on the Mac first: two engines on one paper account would trade twice.
A 1 GB VM is enough (pandas + ~70 symbols × 70 sessions of minute bars).

## What it does each minute
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
