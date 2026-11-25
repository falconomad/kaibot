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

## Always on (no daily start by hand)
`./scripts/install_service.sh` once makes the Mac start the engine by itself (launchd), restart it after a crash,
ping free Healthchecks.io alarms and back up local files nightly. Releases can be pinned with `./scripts/release.sh`
(the engine then trades a tag, not `main`). Everything, including stop and recovery: `docs/ops/always_on.md`.
Starting by hand as above keeps working unchanged.

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

## Switches (KAIBOT_* variables)

Every `KAIBOT_*` variable is declared once in `src/kaibot/common/switches.py` (M10 1.23, #448): its values, its
default, the team that owns it and a review date. At start (`Settings.from_env`, so also `--check`) every engine
switch is parsed together; a value the switch does not have stops the start with one line naming every bad
variable, for example `invalid settings (nothing was started): KAIBOT_MULTIDAY='1': use on or off (...)`. Before
#448 such a value silently meant the default. Values that worked keep their meaning: flags take
`1/on/true/yes` and `0/off/false/no` (any case), `KAIBOT_MULTIDAY` and `KAIBOT_MARKET_ALARM` take `on`/`off`,
`KAIBOT_PREOPEN` and `KAIBOT_FILING_FLAG` take `off`/`shadow`; empty means unset, except `KAIBOT_DATA_DIR` and
`KAIBOT_ENGINE_NAME`, which are refused when set but empty. `KAIBOT_ROOM` (the stakeholder's switch) refuses a value with spaces around it
(`" on"`): the room never read that as on. A bad behaviour switch stops the engine but not the watchdog, which
logs it and keeps protecting the positions (it reads only `KAIBOT_DATA_DIR` and `KAIBOT_ENGINE_NAME`, strictly). Check your `.env` with `./scripts/run_engine.sh --check`
before you deploy this release.

The switches are read **once**, at start, into one typed object (`switches.start`, called by `Settings.from_env`
after the `.env` file is read); every reader in the engine, the agents and the scripts asks that object
(`switches.get` / `switches.text`), and a test fails on any other `os.environ` read of a `KAIBOT_*` variable. So a
change to a switch takes effect at the next restart, never in a running engine. A `KAIBOT_*` variable that is not
declared (a misspelt switch, such as `KAIBOT_MULTI_DAY`) is named in a warning at start and is otherwise ignored.

## When a start is refused (exit codes)

The engine exits with a code that says why it did not start. In every case below it sent nothing to the broker, and
the broker-held stops, the watchdog and the dead-man keep protecting open positions.

| Exit code | Why | What the always-on service does | Do this |
|---|---|---|---|
| 1 | A setting or a key is missing or invalid (`invalid settings (nothing was started): …`), or `--check` found a FAILED line. | Tries again every minute. | Fix the line it names. |
| 3 | The engine lease: another engine runs, or the lease could not be checked. | Tries again every minute (a standby). | See [Engine lease](#engine-lease-one-engine-whatever-the-machine) below. |
| 4 | The journal was refused (not durable, stale, damaged, or another journal ran meanwhile). | Tries again every minute and names the journal each time. | [ledger.md](ledger.md). |
| 5 | `refusing to start: run N holds multi-day sleeves (…) and this engine has no daily path; …` (M10 1.20 step 2, #445). The active run's ledger holds multi-day shares, and this engine was started with `KAIBOT_MULTIDAY` off (or unset), or with it on but no multi-day rule enabled in `config/profiles.yaml` (`multi_day.rules`). Without the daily path nothing could sell, sync or book those sleeves, so the engine does not start. The lease is given back. | Waits 5 minutes before it exits (`KAIBOT_REFUSED_BACKOFF_S`, 300 s), so one line `engine refused to start (exit code 5): …` and one alarm come about every 6 minutes, not every minute. A stop during the wait ends it at once. | Set `KAIBOT_MULTIDAY=on` in the engine's `.env` (or enable the rule that holds the sleeves, as the line says). The next try starts by itself; `./scripts/service.sh restart` starts it at once. `./scripts/run_engine.sh --check` reports it beforehand: `daily_path: FAILED: a start would be refused (exit code 5): …`. |

`./scripts/run_engine.sh --check` asks the exit-code-5 question read-only (`daily_path`): with the daily path on,
or no multi-day sleeve in the active run's ledger, it prints `daily_path: on|off`, the sleeves it found and
`start: allowed`. It reads the journal's ledger only; it takes no lease and writes nothing.

## Engine lease (one engine, whatever the machine)

`scripts/engine_guard.sh` stops a second engine on the **same** machine. The lease stops one on **another**
machine (Mac and a cloud VM). It is one row in Supabase, `kaibot.engine_lease` (migration `0013_engine_lease.sql`):
who holds it (`holder`, `host`, `machine`), since when (`started_at`) and until when (`expires_at`).

Since migration `0041_engine_lease_machine.sql` (#562) the lease belongs to the holder id **and the machine**: a key the
engine computes once at start from the host name and the operating system's machine id (macOS `IOPlatformUUID`,
Linux `/etc/machine-id`), hashed. It is never stored in the data folder, so a data folder copied to another machine
(holder file included) does not make that machine the same engine: its start is refused while the lease is live, it
can never renew or release the first engine's lease, and it takes over only after that lease expired (the first
engine then stops, `lease lost`). A machine whose name changed waits out its own old lease once (90 s) unless it
stopped cleanly. A container recreated by Docker gets a new host name and waits the same way; `docker restart` keeps it.

Since migration `0042_engine_lease_epoch.sql` (M13 4.3, #477) every start is a new **tenure** with a rising number
(`epoch`, the fencing token). Renewals and the release name it, and **every order the engine sends is preceded by a
renewal with it** (`engine/fence.py`: submit, replace, cancel and close all go through the fence). An older process of
the same install (a second start on the same machine, or a laptop that wakes after a restart) is therefore refused at
its next order or heartbeat and stops (`lease lost`); a paused engine whose lease another engine took sends nothing.
An engine of an older release (no epoch) keeps its lease only until an engine of this release starts on the install.
If the renewal cannot reach Supabase, the order is refused when it is a buy, and sent when it only reduces (a sale, a
stop, a cancel, a protective stop's resize), as for any renewal failure below. Log line of a refused order: `order not sent (...): ...`.

| Moment | What the engine does |
|---|---|
| Start-up, before it touches the broker | Takes the lease with one atomic database call. Free, expired or already its own: it starts. |
| Every heartbeat (about every 10 s) | Renews the lease for another 90 s. The heartbeat shows `lease: ok`. |
| Ctrl-C / SIGTERM | Releases the lease, so another machine can start at once. |
| Crash, power loss, network gone | Nothing: the lease runs out 90 s after the last renewal and the next engine to start takes it over (event `engine_lease_takeover`). |
| Restart on the same machine | Gets its own lease back at once (the holder id is kept in `data/live/engine_holder_id`, and the machine key is the same), so a crash does not lock the machine out. |

`./scripts/run_engine.sh --check` prints the current lease (`engine_lease`: host and times, or `free`) without taking it.

### Messages and what to do

| Log line (exit code 3 unless noted) | Meaning | Do this |
|---|---|---|
| `Another Kaibot engine holds the lease: host '…', started …` | An engine on that host is running, or died less than 90 s ago. This one did **not** start and touched nothing. | Stop the other engine (Ctrl-C, `docker stop kaibot-engine`, `docker compose stop engine`), then start this one. If the other machine is already dead, wait 90 s and start again. |
| `The engine lease is held by THIS install's holder id on another machine or an older release (host '…' …)` | The data folder (its `engine_holder_id`) was copied from that host, or this machine's name changed, or an engine of a release before #562 still runs here. This one did **not** start. | If another machine runs the engine, stop one of them. On a machine that got a copied data folder, delete `data/live/engine_holder_id` there (a new one is made at the next start). Same machine after a rename or an upgrade: wait 90 s and start again. |
| `The engine lease could not be checked (…) No degraded start: …` | Supabase is unreachable, or migration 0013 is not applied, and the engine may not start reducing only (the reason follows: no lease record of its own in the journal, one written on another host or machine (#562), another engine seen, or the broker unreadable). Without the lease the engine cannot know it is the only one, so it does not start. | Run `./scripts/run_engine.sh --check`. `supabase: FAILED` is a network or key problem. Only `engine_lease: FAILED` means the migration is missing. |
| `The engine lease could not be checked … Starting REDUCING ONLY` (WARNING, keeps running) | M10 1.25: Supabase is unreachable at start-up, the last lease recorded in the journal was this machine's own and the broker shows no other engine. Exits, stops, sales and fills are managed; **nothing is bought**, no dashboard command is applied and the heartbeat is not written until `engine lease confirmed and journal verified`. A session that began while Supabase was down could not read your settings; they are read again then (and every minute after, while unreadable), so the day's buys still go out if the 15:50-15:57 send window has not closed. | Nothing, if Supabase comes back. If it found another engine's lease when it came back, it stops with one of the lines above. See [failsafe.md](failsafe.md#degraded-start-supabase-down-when-the-engine-starts-m10-125-450). |
| `engine lease confirmed and journal verified: fully active (entries allowed)` (INFO) | A reducing-only engine reached Supabase: it holds the lease, its journal is the newest, and nothing blocks buys. | Nothing. |
| `engine lease confirmed and journal verified, but entries stay OFF: config refused: config unreadable: …` (WARNING, keeps running) | The lease is confirmed but your settings still cannot be read: no buys (exits, stops and sales go on). They are asked again every minute; `owner config vN read again: the session's config refusal is lifted` means buys are allowed again. `… config vN rejected …` instead: the settings were read and are invalid; no buys until the next session. | Usually nothing. If it lasts, run `./scripts/run_engine.sh --check`. For `rejected`, fix the settings on the dashboard (they apply from the next session). |
| `engine lease renewal failed (…): no new entries until it works again` (keeps running) | A renewal call failed. The heartbeat shows `lease: unverified`. **No new entries**; open positions are still managed (stops, 15:55 flatten). | Usually a Supabase or network blip: it clears itself on the next good renewal (`engine lease renewed again`). If it lasts, check the network. |
| `The engine lease is no longer ours` then `engine stopped: lease lost` | This engine went quiet for more than 90 s (laptop slept, network dropped) and another engine took over, or someone released the lease by hand. This one stops and writes nothing more (the new engine's `engine_lease_takeover` event is the record). | Nothing to fix: the other engine is in charge. Open positions keep their broker-held stops and the other engine manages them. Decide which machine should run, and stop the other. |

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
5. 15:55 ET (5 minutes before the close, so 12:55 on an early close): sells the intraday positions. Multi-day
   positions (`KAIBOT_MULTIDAY=on`) are kept: their rule trades them from 15:50 to 15:57 ET with DAY market orders
   (12:50-12:57 on an early close).
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

## What will the next send window do? (read-only preview)
`python scripts/preview_next_send.py --env <the engine's .env>` (with the checkout's `.venv`) reads the local journal
and the daily store and says, per multi-day rule, what the next 15:50-15:57 ET window would do: the decision's own
send, a late send of a decision whose window was missed (M9 1.4), a carry-over of buys still short, only pending
sales, or nothing; per stock the target, the shares held and working, and the shares to buy or sell; and every stock
left out with its reason. `--journal <file>` and `--daily-data <folder>` name them directly, `--session YYYY-MM-DD`
picks a session, `--json` prints the same as JSON.

It is safe while the engine is stopped or running: the journal is opened read-only and no entry is written, no
lease is taken, and neither the broker nor Supabase is contacted (no key is read). The plan comes from the
engine's own planning code, not from a copy of it. It cannot know what is read only in the window: announced
corporate actions, the pre-send quote check, entry blocks, fills the engine has not booked yet. It lists these every
time. Run it after the daily refresh (20:15 ET): before that, the next session's decision cannot be previewed.
