# Pre-registration: `market_alarm_study`

- **Rule:** `market_alarm_study`
- **Kind:** a study, not a trading rule: it buys and sells nothing
- **Status:** pre-registered
- **Date:** 2026-11-20
- **Author:** data, backend-quant
- **Issue:** #377 (S103); design `docs/research/event_driven/design.md`, section 4

Written and committed before any flag was computed on history and before any outcome (SPY move, rule P&L) was
read for this study. Append-only: a change is an `## Amendment <n>` at the end, committed before the run it
affects.

## Hypothesis

Sessions with one very large scheduled event (a Fed statement, the inflation report, results of the largest
companies, an index reshuffle at the close) differ from other sessions in the size of SPY's move and in the daily
profit and loss of `trend_momentum`. The expectation is a **larger absolute SPY move** on `fed_day` and `cpi_day`,
little or none on `index_reshuffle`, and no clear difference in `trend_momentum`'s mean daily P&L (scheduled
macro days have earned higher average returns, Savor and Wilson 2013; Lucca and Moench 2015 [memory]). The study
decides only whether a flag is **worth proposing** as a gate later. It gates nothing.

## Who is on the other side of the trade

Nobody: the study places no order. If a gate is ever proposed from it, that proposal is a new rule variant with
its own pre-registration and its own Gate B (design, section 4), and names its counterparty there.

## Published evidence

Design section 4 and section 8 (Savor and Wilson 2013; Lucca and Moench 2015; Greenwood and Sammon on the
disappearing index effect [checked]; Hsu, Reed and Rocholl 2010 and Braun and Larrain 2009 on large listings
[memory]). None of it is a reason to skip the measurement.

## Frozen settings

**Sessions.** Every regular NYSE session from 2016-01-04 to the last session before the run with a SPY bar in
the daily store (`kaibot.ingest.nyse_sessions`). The alarm of session S is computed **as of 09:00 ET on S**
(`as_of`), from inputs with `available_ts <= as_of` only.

**Inputs and their `available_ts`.**

| Input | File | `available_ts` |
|---|---|---|
| Scheduled Fed statements (14:00 ET) | `config/macro_calendar.yaml` `fomc_statement_1400` | 7 calendar days before the session (every date here was published months earlier; the unscheduled statement of 2020-03-03 is not an input) |
| CPI releases (08:30 ET) | `config/macro_calendar.yaml` `cpi_0830` (2025 onward) and `config/market_events.yaml` `cpi_0830_history` (2016-2024) | 7 calendar days before the session |
| Index reshuffles | `config/market_events.yaml` `index_reshuffle` (S&P quarterly rebalance, Russell reconstitution, Nasdaq-100 annual change), each with its source | 7 calendar days before the session |
| Mega listings and their follow-ups | `config/market_events.yaml` `mega_listing`, each with `known_since` and source | `known_since` (00:00 ET of that date) |
| Earnings reports | `data/earnings/announcements.parquet` (S85): rows with `report = True` | `accepted_ts` |
| Ten largest companies | `data/daily` (raw close and volume) and `src/kaibot/ingest/sp500/changes.csv` | the close of the ranking day plus 15 minutes |

**The flags of session S.**

1. `fed_day`: S is a date of `fomc_statement_1400`.
2. `cpi_day`: S is a date of `cpi_0830` or `cpi_0830_history`.
3. `megacap_report`: at least one of the ten largest companies (below) has an Item 2.02 report (`report = True`)
   whose `session` is S and whose `accepted_ts <= as_of`. The flag carries the count `n_megacaps`. In history only
   filed reports count; the "due before the next open" part of the design needs the forward calendar snapshots,
   which exist from November 2026 only, and is not part of this backfill.
4. `index_reshuffle`: an entry of `index_reshuffle` takes effect at the close of S.
5. `mega_listing`: S is the first session of a listing worth at least $100 billion at its offer price, or a dated
   follow-up of one (options start, index entry, lock-up end) listed with it.

**Tiers.** `major`: `fed_day`, or `mega_listing`, or `n_megacaps >= 3`. `notice`: any other flag. `none`: no flag.

**Ten largest, point in time.** The cover-page share counts of the SEC filings are **not** used: dual-class
companies (GOOGL and GOOG, BRK.B) make a per-ticker count wrong, and the store's coverage of former members is
not known. The issue's declared fallback is used instead: at the last session of each calendar month, the ten
S&P 500 members (point in time, one ticker per company) with the highest mean raw dollar volume (close x volume)
over the last 60 sessions with a bar (at least 40); they are the ten largest for every session of the next
calendar month.

**If `major` covers more than one session in ten** over the backfill window, the megacap part of `major` is
tightened before any comparison is read: `n_megacaps >= 4`, then 5, and so on, until `major` covers at most 10%
of sessions. The threshold used is printed in the report. The comparisons below use the flags, not the tiers, so
the tightening cannot change them.

**The eight comparisons (no ninth).** For each of `fed_day`, `cpi_day`, `megacap_report`, `index_reshuffle`:

- **M1:** the absolute close-to-close return of SPY on the session (split-adjusted closes from `data/daily`);
- **M2:** the daily return (`pnl / start`) of the base account of the newest ten-year engine + `SimBroker` run
  of `trend_momentum` on the point-in-time universe in `data/reports` (`engine_*_pit_*/daily_*.csv` of the base
  label: base fill, 1x costs, 100% exposure cap), on the sessions of that run's file.

Each comparison is the OLS regression `y_t = a + b * flag_t + e_t` over all sessions of its window, `b` the
difference between flagged sessions and all others, with a Newey-West standard error with 5 lags (Bartlett
kernel). Reported: sessions flagged, mean flagged, mean other, `b`, its t. **A comparison is "clear" only with
|t| >= 3.** If the M2 file does not exist, M2 is not run, its four rows are not logged, and the report says so;
M2 then runs once, when the file exists.

`mega_listing` is not tested: it is listed case by case with SPY's move on each session.

## Neighbours (at most five)

None. The study has no setting to vary; each flag is defined once above.

## Fill model

None: no order, no fill. Outcomes are read from SPY's closes and from the account of an existing run.

## Pass marks

No pass marks: this is not a rule. One trial row per comparison that runs (`strategy: market_alarm_study`,
`n_trials: 1` each, eight in all), as decided in design section 7, decision 6 ("count them"). Only a clear
comparison (|t| >= 3) lets anyone **propose** a gate, and that proposal needs its own pre-registration and Gate
B. If none is clear, no gate is ever proposed from this study.

Run: `.venv/bin/python scripts/market_alarm_report.py --start 2016-01-01` on the machine with `data/daily`,
`data/earnings` and `data/reports`. A second run does not log new trial rows for a comparison that has one (the
script refuses), so the study runs once.

## Amendment 1 (23 Nov 2026, quant research, before the `megacap_report` M1 comparison is run)

A clarification of how a session without a ranking is handled. No frozen setting changes: the sessions, inputs,
`available_ts`, flags, tiers, the ranking (60 sessions, at least 40 with a bar), the tightening and the eight
comparisons stay as above.

**Why.** The run of 23 Nov 2026 (`--end 2024-12-31`) left `megacap_report` unknown on 61 sessions, and did not run
its M1 comparison ("unknown on 60 sessions": the first session has no previous close, so no SPY return). The daily
store begins on 2016-01-04. At the end of January 2016 a stock has 19 sessions with a bar, at the end of February 39,
both below the 40 the ranking needs; the first ranking is made on 2016-03-31 and covers April 2016. January to
March 2016 is 19 + 20 + 22 = 61 sessions with no ranking public at `as_of`, so the flag cannot say yes or no on them.
The M2 rows are not affected: the `trend_momentum` run starts after its own warm-up, past March 2016.

**Rule.** A session on which a tested flag is unknown **only because no ranking is public yet at its `as_of`**
(before the first ranking of the backfill, with the earnings file read) is left out of that flag's comparisons.
The report states how many sessions were left out, the first and the last, and the trial row carries the same.
Any other unknown session (a missing earnings file, an unknown session after the first ranking) still stops the
comparison, as before. The three M1 rows already run (`fed_day`, `cpi_day`, `index_reshuffle`) have no unknown
session and are unchanged; no row already logged is logged again.
