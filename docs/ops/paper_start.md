# Starting the trend_momentum paper experiment

For the stakeholder, on the Mac. Decision: `docs/decisions/15_engine_v3.md` section 11 (13 Nov 2026). Issue #133.

**What this is.** `trend_momentum` **failed the ten-year test** (Gate B). You decided to run it on **paper only**, as a
labelled experiment, to see how the engine handles a book held for weeks. It never trades real money. A good paper
result does not change the Gate B verdict, and it does not count as Gate C.

**Exposure caps** (the share of the paper pot the rule may hold): Careful 30%, Balanced 60%, Hunter 100%.
**Stop the experiment** if the rule's drawdown passes 13% (Careful), 21% (Balanced) or 27% (Hunter). Product
checks this in the daily status. The level's own run-drawdown end (4% / 8% / 12%) still ends the whole run first.

> **Read this first: it does not trade yet.** On `main` today the live engine loads and labels the rule, but it has
> no live daily path, so it places **no `trend_momentum` orders**. Only the research runner can do that. The
> experiment starts trading once the follow-up story that wires the daily path into the live engine is merged.
> Product tells you when. Steps 1-6 below are safe to do now. They change nothing until that code is in.

Do every step **outside 09:30-16:00 New York time** (15:30-22:00 Berlin), with the engine stopped.

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

## 6. Check, then start

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

Pick the level when you start the run on the dashboard (Careful, Balanced or Hunter), as usual. Then start the engine:

- By hand: `./scripts/run_engine.sh`
- Always-on service: `./scripts/service.sh start`

Start at least 30 minutes before the open.

## 7. See that it is running

- **Dashboard, Health:** Engine is green, and its code version is the one you pulled.
- **The experiment label:** the engine writes it in every heartbeat (`detail.experiments`) and in each day's
  `session_start` event. The dashboard does not show the label yet. Frontend adds it (it reads
  `heartbeat.detail.experiments[].label` and `.evidence`). Until then, step 6's `--check` is the way to see it.
- **Positions:** once the live daily path is merged, the rule decides after the close and buys or sells in the
  closing auction (orders sent before 15:50 ET). Its positions show on the Live page under `trend_momentum`, each with
  a stop resting at Alpaca.

## 8. Stopping the experiment

- **Stop it for good** (retirement trigger hit, or you decide): dashboard, **Flatten and stop**. This sells everything,
  the multi-day positions included. Then remove `KAIBOT_MULTIDAY=on` from the `.env` (or set it to `off`) and restart
  the engine. With the flag off the engine refuses to load the rule.
- **Pause new buys only:** dashboard, **Pause entries**. Held positions keep their stops.
- **Engine off for a while:** `./scripts/service.sh hold` (or Ctrl-C). Positions keep their stops at Alpaca. With
  0019 applied, the watchdog and the dead-man keep the multi-day positions and their stops, and they sell only the
  intraday ones at the close.

Real money is never switched on from this rule. If a rule passes Gate B one day, real money is a separate decision
(Gate D), and it stays yours.
