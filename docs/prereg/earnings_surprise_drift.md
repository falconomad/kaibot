# Pre-registration: `earnings_surprise_drift`

- **Rule:** `earnings_surprise_drift`
- **Status:** pre-registered
- **Date:** 2026-11-26
- **Author:** data (for backend-quant, who runs it)
- **Issue:** #797 (decision 20 and its Amendment 1)
- **design_saw_holdout:** false. No setting below came from data on or after 2025-01-01, and no earnings data has
  been analysed: the Alpha Vantage cache is empty or unread, and the SEC announcement and XBRL stores have never
  been joined to returns for this rule. The 5-session window, the quintile, the 1/99 winsorisation, the 8-quarter
  (min 4) standard deviation and the 20/40/60 holds are the literature's defaults (Bernard and Thomas 1989/1990;
  Livnat and Mendenhall 2006), chosen before any download.

This file is the rule file `kaibot.evaluation.prereg.require` reads. The full record, with the data rules, is
`docs/decisions/20_earnings_surprise_drift.md` (sections 1-4 and Amendment 1); where the two differ, Amendment 1
of the decision wins, and this file is the frozen copy of it.

## Hypothesis

Stocks whose latest quarterly earnings beat their own seasonal trend by the most (high standardized unexpected
earnings, SUE) keep drifting up for weeks after the news: post-earnings-announcement drift. Expected, written
before any run: in S&P 500 stocks, net of costs, **no better than SPY** (drift has largely gone from large caps since
about 2006); a small positive active return in the first 20 sessions at most. "Fails gate v3.1" is the likely and
legitimate result.

## Who is on the other side of the trade

Investors who under-react to earnings news (they anchor on the old forecast and update slowly), and holders who sell
winners after good news to lock in a gain (the disposition effect). Both are documented; both have weakened as
large-cap trading became faster and cheaper, which is why the expected result is about zero.

## Published evidence

Bernard and Thomas (1989, 1990): SUE from a seasonal random walk predicts 60-day returns, strongest in small firms.
Livnat and Mendenhall (2006): the drift is larger with analyst-based surprises than with time-series SUE. Chordia,
Goyal, Sadka, Sadka and Shivakumar (2009): concentrated in illiquid stocks, small after costs. Martineau (Critical
Finance Review, 2022): drift in large stocks has not existed since about 2006. Subrahmanyam (2025): not significant
once microcaps are excluded (2001-2024). Our own earlier test of the price-reaction variant:
`docs/prereg/post_earnings_week.md`; research notes `docs/research/engine_v3/notes_multi_day_strategies.md`
section 3.

## Frozen settings

**Family and trials.** Seven trials, each a row in the trial registry (`data/trials.jsonl`), in the gate's
`earnings_drift` family (`config/gate_v3.yaml`, with `post_earnings_week`): the deflated Sharpe's N is every
configuration logged for that family (these 7, the neighbours when run, and `post_earnings_week`'s), and Holm runs
as configured:
`sue_sec_h20`, `sue_sec_h40`, `sue_sec_h60` (primary), `av_surprise_h20`, `av_surprise_h40`, `av_surprise_h60`
(secondary), `no_buy_before_report` (risk filter).

**Primary: SUE-SEC** (public-domain data only).

- Universe: the point-in-time S&P 500 (#123, `Membership`): a stock may be bought only on a day it is a member,
  former members included, so there is no survivorship bias. Harness filter as `post_earnings_week`: close >= $5,
  20-day average dollar volume >= $50M, raw prices for orders.
- Event: a quarterly report = a `report` row of `data/earnings/announcements.parquet` (SEC 8-K Item 2.02, exact
  `accepted_ts`, since 2016), matched to its fiscal quarter q = the latest XBRL quarter (`period_end`) ending before
  `accepted_ts` and at most 100 days before it.
- EPS: diluted EPS of quarter q from the XBRL store (us-gaap `EarningsPerShareDiluted`, else
  `EarningsPerShareBasic`; unit USD/shares; a quarterly duration of 80-100 days). A fiscal Q4 that is filed only as
  a full year is FY EPS minus the three quarters of that year, all as known at entry (marked `derived`; counted in the
  coverage report). **Point-in-time:** only versions with `available_date <= entry session` (filed + 1 day, the
  store's rule); for each quarter the latest such version.
- **Entry: the open of the first NYSE session that is both after `accepted_ts` and on or after the
  `available_date` of EPS_q.** The 8-K usually comes days before the 10-Q/10-K that carries EPS_q in XBRL, so for
  most reports the entry is later than the 8-K; this is required to stay point-in-time with XBRL only. The run
  prints the share of events where EPS_q was available by the first open after `accepted_ts`, and the median lag in
  sessions.
- Signal: SUE = (EPS_q - EPS_{q-4}) / sd, where sd is the standard deviation of (EPS_j - EPS_{j-4}) over the 8
  quarters j = q-8 .. q-1 (at least 4 present; else no signal). sd = 0 or |sd| < 0.01: no signal. Share basis: EPS
  values filed before a split are rescaled by the split ratios of the daily store's corporate actions known at entry;
  a quarter whose basis cannot be made the same is dropped.
- Freshness: the event's entry session must be within the last **N = 5** sessions of the decision; else no signal.
- Ranking and size: winsorise SUE at the 1st and 99th percentiles of all signal values in the research window up to
  the decision day (expanding, point-in-time); buy the **top quintile** among the universe's stocks with a signal that
  day, equal weight, within the book's sleeve budget and `HARD_CEILINGS`.
- Market filter: no new buy while SPY's previous close is below its 200-day average; held positions are not sold by
  it.
- Exit: the close of the 20th, 40th or 60th session after entry (one trial each). No stop but the engine's book stop
  (decision 17). No re-entry into a stock already held.
- Earnings version: "hold through" (a position may hold through its next report).

**Secondary: AV surprise** (Alpha Vantage consensus surprise; personal-use data).

- Signal: `surprisePercentage` of the matched quarter (rows with |estimatedEPS| < 0.01 dropped), same freshness,
  winsorisation, quintile, filter, sizing and holds as the primary.
- Event time: the **SEC 8-K Item 2.02 `accepted_ts`** of the same company's `report` filing matched to the AV
  quarter by fiscal period: accepted after `fiscalDateEnding` and within 100 days of it, the one nearest AV's
  `reportedDate`, at most 3 sessions away. Entry: the open of the first session after `accepted_ts`. Only when there
  is no SEC match: AV's `reportedDate` with the conservative rule of decision 20 section 1 (open of the first
  session after `reportedDate`). The run prints the match rate (overall and by year).
- Universe: today's 500 members only (the AV cache holds no former member), with the point-in-time membership mask;
  every result is labelled **survivorship-biased** and cannot count as out-of-sample evidence for a book.

**Risk filter: `no_buy_before_report`.** No new buy of a stock in the 3 sessions before its next expected report,
the date estimated point-in-time from the SEC `report` sessions known at the decision (a year after the same quarter's
report, else 91 days after the last report: the rule of `replay/earnings.py`). Applied to the existing book's rules;
compared with the same book without it. The exact dates (look-ahead) are printed as a bound only.

**Windows.** Research window 2016-01-01 (or the first date with enough history) to 2024-12-31; hold-out from
2025-01-01, opened once after the research verdict.

**Licence.** SEC EDGAR data is public ("may be copied or further distributed"): the SEC variant and the filter are
safe for a multi-user product. The Alpha Vantage variant is personal, non-commercial use only: it may never reach a
multi-user path without a commercial licence (flagged on #475, M13).

## Neighbours (at most five)

- `sue_n3`: freshness window N = 3 sessions instead of 5.
- `sue_n10`: freshness window N = 10 sessions instead of 5.
- `sue_tercile`: top tercile instead of top quintile.
- `sue_sd12`: sd over the prior 12 quarters (at least 6) instead of 8 (at least 4).
- `sue_no_winsor`: no winsorisation.

## Fill model

Engine with `SimBroker`, cost model v2. Base: the order decided before the open fills at the open of the entry
session at half a spread per side; exits at the close of the exit session. Stress: twice the half-spread at the open,
a stop gapped through fills at the open (decision 15). Dividends credited; orders, costs and share counts on raw
prices.

## Pass marks

The gate v3.1 marks of `config/gate_v3.yaml` as committed together with this file (decision 18), including the B7
control book (same budget in SPY, five start offsets), the deflated Sharpe with N = the `earnings_drift` family's logged configurations, and Holm with
`holm_inputs: final_requires`. Nothing here loosens a mark. Not for live decisions unless a trial passes gate v3.1
**and** the stakeholder approves it in writing; the AV variant can never go live without a commercial licence.
