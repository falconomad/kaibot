# Starting the trend_momentum paper experiment

For the stakeholder, on the Mac. Decision: `docs/decisions/15_engine_v3.md` section 11 (13 Nov 2026). Issue #133.

**What this is.** `trend_momentum` **failed the ten-year test** (Gate B). You decided to run it on **paper only**, as a
labelled experiment, to see how the engine handles a book held for weeks. It never trades real money. A good paper
result does not change the Gate B verdict, and it does not count as Gate C.

**Exposure caps** (the share of the paper pot the rule may hold): Careful 30%, Balanced 60%, Hunter 100%.
**Stop the experiment** if the rule's drawdown passes 13% (Careful), 21% (Balanced) or 27% (Hunter). Product
checks this in the daily status. The level's own run-drawdown end (4% / 8% / 12%) still ends the whole run first.

> **Read this first.** The live daily path (issue #206) is what places `trend_momentum` orders. If your copy of the
> code does not have it yet (Product tells you), the engine only loads and labels the rule and places **no**
> `trend_momentum` orders, and steps 1-7 change nothing. With it, the rule trades only with `KAIBOT_MULTIDAY=on` **and** `allow_overnight: true` **and** a fresh daily store
> (step 6). Without the fresh store it decides nothing and sends nothing, and the engine says so (`multi_day_waiting`).

Do every step **outside 09:30-16:00 New York time** (15:30-22:00 Berlin), with the engine stopped (the daily
refresh of step 6 is the exception: it runs while the engine runs).

## 1. Stop the engine

- Started by hand: press Ctrl-C in its window.
- Always-on service: `cd ~/Projects/kaibot && ./scripts/service.sh stop`.

Open positions keep their stops at Alpaca while it is stopped.

## 2. Get the latest code

```
cd ~/Projects/kaibot
git status            # must say "nothing to commit"; if not, tell Product before going on
git pull --ff-only
```

If you pinned a release (`docs/ops/always_on.md`), ask Product which tag to pin instead.

## 3. Database (Product does this; you only confirm)

`docs/ops/migrations_pending.md`. Steps 1-5 (0014-0018) were applied by Product on 13 Nov 2026. What is left:

1. **You: turn Shorting off on the Alpaca paper account.** Alpaca dashboard, switch to the **paper** account,
   Account Configurations, **Shorting: off**, Save. (This stops a late stop order from ever opening a short.)
2. **Tell Product "shorting is off".** Product applies step 6, `migrations/0019_deadman_overnight.sql`, and runs its
   checks: `kaibot_deadman` is the only dead-man job, and `strategy_holding` has 3 multi-day rows.

**0019 and Shorting off must both be done before the first position is held overnight** (step 5). Without 0019 the
off-box dead-man would sell every position at 15:56 if the engine went quiet, the multi-day ones included.

## 4. Switch multi-day rules on (the engine's environment)

Open `~/Projects/kaibot-secrets/.env` in a text editor and add one line:

```
KAIBOT_MULTIDAY=on
```

Exactly `on`. Anything else (`true`, `1`, `yes`) counts as off. Only `trend_momentum` comes on. `mom_12_1_band` stays
off (`config/profiles.yaml`, `multi_day.rules`).

## 5. Allow overnight holding (the level limits)

The repository keeps `all_levels.allow_overnight: false`. While it is false the engine may not hold the rule's
positions over the close (its overnight cap is 0), whatever the exposure cap says.

**Recommended:** tell Product "flip allow_overnight". Product opens a one-line pull request
(`config/profiles.yaml`: `allow_overnight: true`), you approve it, Product merges it, and you `git pull --ff-only`
again. Do it this way because a change you make by hand on the Mac stops the engine's start script from pulling new
code ("could not pull the latest code") and blocks a pinned release.

Only after step 3 is complete (0019 applied, Shorting off).

## 6. Refresh the daily bars every weekday evening

The rule decides on **daily bars**, the same daily store the ten-year test used (`data/daily`, written by
`./scripts/fetch_daily.sh`; free Alpaca data). The engine never downloads it itself. It reads the store after each
close, and only once the day's bars are in it. Alpaca's daily bar counts as complete at **20:15 New York time**
(02:15 Berlin), because after-hours trading adds to the day's volume. So:

1. **First time (once):** run `./scripts/fetch_daily.sh` with `data/universe/former_members.csv` in place, as for
   the ten-year test (`docs/ops/daily_bars.md`). The rule's universe is the S&P 500 as of each day, so the store needs
   the former members too.
2. **Every weekday after 20:15 New York time, before the next open:** run `./scripts/fetch_daily.sh` again. It is
   resumable and skips what it already has. Running it in the early morning in Berlin (for example 07:00) also works:
   the engine decides as soon as the bars are there, as long as that is before the next open (09:30 New York,
   15:30 Berlin).
   To automate it, Product or QA-Ops can add it to the always-on service. Ask them, and do not edit the start script
   by hand.
3. **If you miss an evening:** the engine decides nothing for that session and sends no `trend_momentum` orders.
   Held positions are kept (and sold at the next session that has a decision, if the rule says so). The engine writes
   a `multi_day_waiting` event that names the missing day. Nothing is bought on stale data.

The engine reads the store from `~/Projects/kaibot/data/daily` by default. Set `KAIBOT_DAILY_DATA=/path/to/folder`
in the `.env` only if your store lives somewhere else (the folder that **holds** `daily/`). When the engine runs in
Docker, `./scripts/run_engine.sh` hands it that folder read-only, so nothing else is needed.

The engine also checks that the refresh **finished**: if most stocks are still missing the day's bar (for example
the script stopped half-way), it waits and decides nothing, as if you had missed the evening.

**Missing the decision evening skips a week.** The rule decides only once a week (see below). If the store is not
refreshed before the open of that session, there is no decision that week: nothing new is bought, and a held name the
rule would have sold is kept until the next week's decision.

**What happens each day** (New York time):

- After the close, once the bars are in the store: the rule decides for the next session. The decision is written to
  the engine's journal (`multi_day_decision` event). `trend_momentum` decides once a week (the first session from
  Wednesday on) and re-sizes every second week.
- Next session, from 09:31 until 15:45: the engine sizes the decision against the run's equity at the last close
  (the portfolio builder: whole shares, 3% cash reserve, at most 40% in one sector, within your level's caps) and sends
  **closing-auction (`cls`) orders** to Alpaca paper (`multi_day_orders` event). Alpaca does not accept them from
  15:50, so the engine stops at 15:45. They fill at the 16:00 close. Each fill is booked once (`multi_day_fill`).
- Sales always go out. Buys wait while entries are blocked (paused, daily stop, the book not matching Alpaca), but
  only until 15:45. A buy still blocked then is dropped for that day, and the next weekly decision decides again.
- If the daily stop fires, the engine cancels the rule's buy orders that have not filled yet. Its sales stay.
- The 15:55 flatten of the intraday rule never sells `trend_momentum` positions.

## 7. Check, then start

```
cd ~/Projects/kaibot
./scripts/run_engine.sh --check
```

It must end with `ALL GOOD`, and the `multi_day` line must show:

- `"KAIBOT_MULTIDAY": "on"`
- `"allow_overnight": true` (still `false` until step 5 is merged)
- `"rules_loaded": ["trend_momentum"]`
- `"overnight_cap_pct"`: 30, 60 or 100 for your level (0 while `allow_overnight` is false)
- `"experiments"`: one entry with `"label": "Experiment: failed the ten-year test"`, `"evidence": "failed_gate_b"`,
  `"real_money": false`, and your level's `max_exposure_pct` and `retire_drawdown_pct`
- `"daily_store"`: its `path`, and a `last_session` that is the last trading day (step 6)

Pick the level when you start the run on the dashboard (Careful, Balanced or Hunter), as usual. Then start the engine:

- By hand: `./scripts/run_engine.sh`
- Always-on service: `./scripts/service.sh start`

Start at least 30 minutes before the open.

## 8. See that it is running

- **Dashboard, Health:** Engine is green, and its code version is the one you pulled.
- **The experiment label:** the engine writes it in every heartbeat (`detail.experiments`) and in each day's
  `session_start` event. The dashboard does not show the label yet. Frontend adds it (it reads
  `heartbeat.detail.experiments[].label` and `.evidence`). Until then, step 7's `--check` is the way to see it.
- **Positions:** once the live daily path is merged, the rule decides after the close and buys or sells in the
  closing auction (orders sent before 15:50 ET). Its positions show on the Live page under `trend_momentum`.
- **No stop while the engine runs.** The rule as registered has no stop ("No fixed stop" in
  `docs/prereg/trend_momentum.md`), so the engine places none and the position rows say the stop is `missing`. That is
  expected. If the engine goes quiet, the watchdog (on the Mac) and, once 0019 is applied, the off-box dead-man put an
  emergency stop 10% below the last price on each multi-day position (and cancel any of its buy orders not yet
  filled). Before 0019 the dead-man sells everything at 15:56 instead (step 3).

## 9. Stopping the experiment

- **Stop it for good** (retirement trigger hit, or you decide): dashboard, **Flatten and stop**. This first cancels the
  rule's closing-auction orders that have not filled, then sells everything, the multi-day positions included. Do it
  before 15:50 New York time if you can: after 15:50 the exchange may no longer let a closing-auction order be
  cancelled, so one sent that day could still fill at 16:00. Check the Live page the next morning. Then remove `KAIBOT_MULTIDAY=on` from the `.env` (or set it to `off`) and restart
  the engine. With the flag off the engine refuses to load the rule.
- **Pause new buys only:** dashboard, **Pause entries**. Held positions are kept; the rule's sales still go out. A buy
  order already sent that day is not cancelled by a pause and still fills at the close.
- **Engine off for a while:** `./scripts/service.sh hold` (or Ctrl-C). With 0019 applied, the watchdog and the
  dead-man keep the multi-day positions (with an emergency stop each), and they sell only the intraday ones at the
  close. When the engine starts again it picks up where it left off: a decision already taken is not taken again, and
  an order already sent is not sent twice.

Real money is never switched on from this rule. If a rule passes Gate B one day, real money is a separate decision
(Gate D), and it stays yours.
