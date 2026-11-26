# Decision 20: Earnings-surprise drift sleeve (research only), pre-registered

- **Status:** pre-registered by the Data team, 26 Nov 2026 (#797), **before any Alpha Vantage data was downloaded
  or analysed**. When this file is merged, no answer of `scripts/fetch_av_earnings.py` exists anywhere: the first
  batch runs after the owner adds the key (`docs/ops/alphavantage.md`). Nothing in this record may change after the
  first run on the data; a change is an amendment written before that run, or a new rule under a new name.
- **Research only.** Nothing here is used for a live decision. A rule from this record may reach the live engine
  only if it **passes gate v3.1** (decision 18) **and the stakeholder approves** it in writing. Until then the data
  stays in `data/earnings/alphavantage/` (not in git), and `kaibot.data` cannot be imported by any trading package
  (`tests/unit/test_research_data_isolation.py`).
- **Licence.** Alpha Vantage's free key is for personal, non-commercial use. That fits a one-person paper-trading
  research project. **Before any multi-user exposure** (decision 13: other users, the dashboard showing these
  numbers to anyone but the owner, a paid product) the licence must be flagged to the stakeholder and cleared, or
  the data replaced.
- **Data budget (stakeholder, 26 Nov 2026: free tier only, no paid plan).** 25 requests a day per key, shared with
  the owner's other uses. The backfill spends at most 20 a UTC day (never more than 23), 13 s apart, and stops on
  the first rate-limit answer. 500 symbols take about 25 days.
- **Before the first run** the rule file `docs/prereg/earnings_surprise_drift.md` (the template's headings, the
  settings below copied unchanged) must be committed, as `kaibot.evaluation.prereg.require` asks of every rule.
  It may not differ from this record.

## 1. Data and the point-in-time rule

Source: Alpha Vantage `function=EARNINGS`, one request per symbol, the full quarterly history (`quarterlyEarnings`:
`fiscalDateEnding`, `reportedDate`, `reportTime`, `reportedEPS`, `estimatedEPS`, `surprise`,
`surprisePercentage`). Reader: `kaibot.data.av_earnings` (`AVEarnings.known_as_of`). The string `"None"` is a
missing value, never zero.

- **When a report is known.** Nominally: a post-market report from the first session after `reportedDate`, a
  pre-market report from `reportedDate`'s session (this reader takes that session's close, since Alpha Vantage
  gives no time of day). **This test uses the conservative rule for both:** a report is usable from the **open of
  the first NYSE session strictly after `reportedDate`** (`rule="conservative"`, the default). A pre-market
  report therefore enters a day later than it could, which can only cost the rule, never flatter it. A blank or
  unknown `reportTime` is treated as post-market.
- **Split adjustment.** `reportedEPS`, `estimatedEPS` and `surprise` are split-adjusted by Alpha Vantage (a later
  split rescales the history), so they are not the numbers printed on the day. `surprisePercentage` is a ratio of
  two numbers on the same basis and is **not affected by splits**. The test uses `surprisePercentage` only.
- **What is not point-in-time.** The values are Alpha Vantage's as of the download, not as first published. A
  restated EPS or a revised consensus would not show. The reported dates are checked against the SEC 8-K Item 2.02
  times of `data/earnings/announcements.parquet` (#125) before the first run: the share of AV reports within one
  session of an SEC report is printed with the result, and a report more than one session away from any SEC
  filing is dropped (it cannot be placed in time with confidence).
- **Coverage.** Symbols: the engine universe as of today (the 500 current S&P 500 members). Former members are not
  in the cache, so the universe test below is run on today's members only and is labelled **survivorship-biased**
  in every result; the point-in-time S&P 500 (#123) restricts buying to the days each stock was a member.

## 2. The rule `earnings_surprise_drift` (primary test)

- **Signal.** `surprisePercentage` of the stock's most recent report usable at the decision (conservative rule),
  only if that report's entry session is within the last **N = 5** sessions; otherwise no signal. The value is
  winsorised at the **1st and 99th percentiles** of all signal values in the research window up to the decision
  day (expanding, point-in-time), and an estimate of `|estimatedEPS| < 0.01` drops the row (the ratio explodes).
- **Entry.** Buy at the **open of the first session after the report** (the conservative usable time), the stocks
  in the **top quintile** of the signal among the universe's stocks with a signal that day, equal weight, at most
  the sleeve budget of the book in config, the usual position caps (`HARD_CEILINGS`).
- **Market filter.** No new buy while SPY's close is below its 200-day average (the decision before the open uses
  the previous close). Held positions are not sold by the filter.
- **Holding period.** Three variants, each a separate trial in the trial registry (`data/trials.jsonl`): exit at
  the close of the **20th, 40th or 60th** session after entry. No stop other than the engine's book stop
  (decision 17). No re-entry into a stock already held.
- **Costs and fills.** As in the existing backtests: engine with `SimBroker`, cost model v2, the base fill and the
  stress fill of decision 15; dividends credited; raw prices for orders.
- **Evaluation.** Gate v3.1 (decision 18) as configured in `config/gate_v3.yaml` when this file is committed:
  research window 2016-01-01 (or the first date with data) to **2024-12-31**; **hold-out from 2025-01-01**, opened
  once, after the research verdict; the **B7 control book** (the same budget in SPY, five start offsets); the
  deflated Sharpe with N = every trial of this family (3 here, plus any later amendment) and Holm across families
  as configured. `design_saw_holdout: false` (no number of this design came from data after 2024-12-31; the 5
  sessions, the quintile and the 20/40/60 holds are the literature's defaults, chosen before any download).
- **Hypothesis.** Post-earnings-announcement drift in S&P 500 stocks is small since about 2006 (Martineau;
  Subrahmanyam 2025; `docs/prereg/post_earnings_week.md`). Expected: no better than SPY net of costs. "Fails" is
  a legitimate and likely result.

## 3. Secondary test: a pure risk filter `no_buy_before_report`

- **What.** No new buy of a stock in the **3 sessions before its next scheduled report**. Applied as a filter on
  the existing book's rules (not a new return source); compared with the same book without the filter.
- **The calendar, point-in-time.** The "scheduled" date is derived only from the historical `reportedDate` values
  known at the decision: the same fiscal quarter's report a year earlier plus 364 days, else the last report plus
  91 days (the estimate rule of `replay/earnings.py`). No future report date is used. The exact dates (look-ahead)
  are reported next to it as a bound only, never as the result.
- **Evaluation.** Gate v3.1 as above; one trial in the registry. It passes as a filter only if the book with it
  is not worse on B2 and B7 and has a lower or equal drawdown.

## 4. What this record does not do

It changes no rule, setting, gate mark, the engine, the risk gate or live paper trading. It adds no source to the
live path. The upcoming-earnings calendar on the dashboard (a separate change) is display only and is not this
filter.
