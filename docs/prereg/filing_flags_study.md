# Pre-registration: `filing_flags_study`

- **Rule:** `filing_flags_study`
- **Kind:** a study, not a trading rule: it buys and sells nothing
- **Status:** pre-registered
- **Date:** 2026-11-21
- **Author:** backend-quant, data
- **Issue:** #375 (S101); design `docs/research/event_driven/design.md`, section 2.3; data: S100 (#374)

Written and committed before `data/filings/filings_8k.parquet` was ever read for this study (that file exists only
on the stakeholder's Mac, and no run of the study script has happened anywhere). Append-only: a change is an
`## Amendment <n>` at the end, committed before the run it affects.

## Hypothesis

After some kinds of 8-K filings other than earnings, large stocks keep moving the same way for days to weeks,
because part of the reaction arrives late (Lerman and Livnat 2010 [memory]). The documented side is bad news, so
the expected sign is **negative** for 4.02, 4.01, 2.05/2.06 and 3.01/1.03, and unclear for 5.02 and 1.01. Kaibot
does not short, so a negative group can only become a "do not buy for N sessions" flag. The study decides
whether any group is worth logging as such a flag. It changes no order.

## Who is on the other side of the trade

Nobody: the study trades nothing. A later veto built on a counted group would be a new, pre-registered variant
of a rule that already passed Gate B, and would name its counterparty there. What it would avoid is buying from
holders who sell on news that the price has not fully absorbed.

## Published evidence

Design section 2.3. Lerman and Livnat (2010) on 8-K items and delayed reactions; restatements and auditor changes
as the best-documented bad-news filings [memory, not re-read; no numbers taken from them]. S&P 500 companies
rarely restate, so some groups will have too few cases to say anything.

## Frozen settings

**Data.** `data/filings/filings_8k.parquet` (S100): form `8-K` only (an `8-K/A` repeats an earlier filing and is
left out); its `session` column is day 0, the first regular session whose 09:30 ET open is after `accepted_ts`.
Daily closes from `data/daily`, **split-adjusted, dividends ignored**, for the stock and for SPY. The
point-in-time S&P 500 from `src/kaibot/ingest/sp500/changes.csv`: a filing is kept only if its company was a
member on day 0.

**Window of the study.** Day 0 from 2017-05-01 to the last session for which the 20-session window is complete
in the store.

**The six groups.** A filing belongs to a group when its `items` list at least one of the group's items. A
filing that also lists **2.02** (results) is left out of every group: its move is the earnings reaction, which
S96 studies.

| Group | Items |
|---|---|
| `g402_non_reliance` | 4.02 |
| `g401_auditor` | 4.01 |
| `g205_206_restructuring` | 2.05, 2.06 |
| `g502_officers` | 5.02 |
| `g101_agreement` | 1.01 |
| `g301_103_delisting_bankruptcy` | 3.01, 1.03 |

**One observation per company and calendar month** (of day 0) and group: the first filing of that month.

**The two windows.** `w5`: from the close of day 0 to the close of day +5. `w20`: to the close of day +20. The
decision a flag would block is taken after the close of day 0, so day 0's own move is not in the window. If the
stock has no close on the last day of the window (delisted, halted), the last close inside the window is used.
An observation with no close on day 0 or none after it is dropped and counted in the report.

**The measure.** The stock's window return minus SPY's over the same sessions.

**The twelve cells** (six groups x two windows; no thirteenth). For each: the number of observations, the mean,
two t-statistics, and the sign of the mean in each calendar year of day 0:

- `t_obs`: mean / (standard deviation / sqrt(n)) over the observations;
- `t_month`: the same on the calendar-month means of the cell (one value per month with at least one
  observation), which allows for filings bunched in time;
- the cell's `t` is the one with the smaller absolute value.

**A cell counts** only when all of these hold:

1. at least 30 observations;
2. |t| >= 3.5;
3. the mean has the same sign in at least 6 of the 10 calendar years 2017 to 2026 (2017 counts: May to December
   is at least six months, decision 15 section 10). A year with no observation does not count as the same sign.

**A group counts** when at least one of its two cells counts **and its mean is negative**. A positive cell that
passes is reported and becomes nothing (there is no "buy" use in this story). The flag of a counted group is "do
not buy for N sessions after day 0", N = 20 if `w20` counts, else 5.

## Neighbours (at most five)

None. Each cell is defined once above; the two windows are part of the declared twelve looks, not neighbours.

## Fill model

None: no order, no fill. Window returns are read from closes.

## Pass marks

No pass marks: this is not a rule. **One trial row per cell, twelve in all** (`strategy: filing_flags_study`,
`n_trials: 1` each), as design section 7, decision 6 says ("count them"); the script refuses to log a cell twice.
If no group counts, the idea is dropped and the design's "Results" section says so. A counted group becomes a
**logged** feature only (`filing_flag`, `config/filing_flags.yaml` `counted`); using it to veto needs its own
pre-registered rule variant and Gate B.

Run: `.venv/bin/python scripts/filing_flags_study.py --start 2017-05-01` on the machine with `data/filings` and
`data/daily`.
