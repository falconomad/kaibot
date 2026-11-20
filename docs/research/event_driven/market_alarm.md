# The market-event alarm: what it is and what it is worth so far (S103, #377)

- **Date:** 20 Nov 2026. **Team:** Data with Backend-quant. **Pre-registration:** `docs/prereg/market_alarm_study.md`
  (committed before any flag was computed on history; sha256 `73563ce4...0d40`).
- **Status:** the flags, the log line and the backfill script are built. The eight comparisons have **not run**.
  They need the Mac's `data/daily`, `data/earnings` and the engine + `SimBroker` run, so the answer to "what is it
  worth" is not known yet. Nothing below is a result about returns.
- **It gates nothing.** No order path, risk check or strategy reads it (`tests/unit/test_market_alarm.py` checks
  that only the engine's entry point imports it, and that a replay with it sends the same orders).

## What was built

| Part | Where | What it does |
|---|---|---|
| Index dates 2016-2027, CPI dates 2016-2024, mega listings | `config/market_events.yaml` | dated lists, each with its source and a `covers_until`. Kept out of `config/macro_calendar.yaml` so the engine's entry blackouts cannot change |
| The flag function | `src/kaibot/features/market_alarm.py` `alarm(session, as_of, inputs)` | pure; reads only inputs with `available_ts <= as_of`. A missing or stale input gives `None` (unknown), never `False` |
| The log line | `engine/core.py` `_log_market_alarm`, `engine/__main__.py` `market_alarm_from_env` | one `market_alarm` event per session start, **only with `KAIBOT_MARKET_ALARM=on`** (off by default). A failure writes `market_alarm_error` and the session goes on |
| Backfill and measurement | `scripts/market_alarm_report.py` | the ten largest per month, the flags of every session since 2016, the shares and the tightening, the 19 Nov check, the eight comparisons with Newey-West t, one trial row each |

Not built here: the flags inside each decision's features and in the engine journal. The event row is the log. A
feature column in `signal.features` touches the decision path, which is Backend-quant's (two keys). Proposed as
the follow-up if the comparisons give a reason.

## The flags on ten years of history, without the earnings data (flags only, no outcome read)

`scripts/market_alarm_report.py --start 2016-01-01 --end 2026-11-19 --flags-only` in a checkout without `data/`
(20 Nov 2026). `megacap_report` is unknown on every session here, because it needs `data/earnings` and
`data/daily`.

| Flag | Sessions (of 2,737) | Share |
|---|---|---|
| `fed_day` | 86 | 3.1% |
| `cpi_day` | 128 | 4.7% |
| `megacap_report` | unknown | - |
| `index_reshuffle` | 54 | 2.0% |
| `mega_listing` | 2 (SpaceX 12 Jun 2026, ANT 19 Nov 2026) | 0.1% |

| Tier | Sessions | Share |
|---|---|---|
| major | 88 | 3.2% |
| notice | 174 | 6.4% |
| none (or unknown) | 2,475 | 90.4% |

- **19 Nov 2026: `mega_listing` ANT, tier `major`**, from inputs dated 18 Nov (the pricing release). On 17 Nov the
  same function says `mega_listing: false`: the offer price was not public yet. (The issue says "dated 11 Nov or
  later". The Nasdaq notice of 11 Nov named the day, but the $100 billion test needs the offer price.)
- `major` is 3.2% without megacaps. The design expects `megacap_report` on up to 40 sessions a year. The ">= 3
  megacaps" part may push `major` past 10%; the script then tightens it before any comparison is read, as
  pre-registered.
- BLS published CPI on two Good Fridays (14 Apr 2017, 10 Apr 2020). No session carries those two flags.

## What it is worth: not known yet

The value is the eight comparisons. Until the Mac runs them, the honest answer is "unknown". What we expect,
said beforehand (pre-registration, hypothesis):

- **M1 (size of SPY's move):** clearly larger on `fed_day` and `cpi_day`. That is well documented. It would make
  the notice informative for the stakeholder ("expect a bigger move today"), but it is no reason to gate.
- **M2 (`trend_momentum`'s daily P&L):** probably no clear difference. Scheduled macro days have earned higher
  average returns (Savor and Wilson 2013; Lucca and Moench 2015). A gate that stays out on them would likely give
  up return for calm.
- If no comparison reaches |t| >= 3, no gate is ever proposed from this study. The notice may stay if the
  stakeholder finds it useful.

Trial cost: 8 rows (`strategy: market_alarm_study`), logged once each; the script refuses to log a comparison
twice. M2 runs only when an engine + `SimBroker` point-in-time run of `trend_momentum` exists in `data/reports`
(#202 / #373 produce it). Until then its four rows are not logged.

## On the Mac

```bash
cd ~/Projects/kaibot && git pull
.venv/bin/python scripts/market_alarm_report.py --start 2016-01-01      # minutes; reads data/daily, data/earnings
cat data/reports/market_alarm_backfill.md                                 # paste into #377
```

Then, to log the alarm live (a notice only), add `KAIBOT_MARKET_ALARM=on` to the engine's `.env` and restart the
engine outside 09:30-16:00 ET. Refresh the ranking monthly (`--rankings-only`). The live alarm treats a ranking
older than 45 days as stale and logs `megacap_report` as unknown.
