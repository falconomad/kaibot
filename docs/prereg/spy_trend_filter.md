# Pre-registration: spy_trend_filter

- **Rule:** `spy_trend_filter`
- **Status:** pre-registered
- **Date:** 2026-11-13
- **Author:** backend-quant
- **Issue:** #201

Written and committed before any code that runs this rule exists and before any run of it on synthetic or real
data. Approved by the stakeholder on 13 Nov 2026 as the ONE new candidate after the momentum core failed Gate B on
the point-in-time universe. The stakeholder's goal, unchanged: "market-like return with smaller drawdowns, proven
honestly".

**Status of every result from this document: PROVISIONAL.** The test runs in a small SPY-only research routine
(`kaibot.replay.spy_trend`, like `kaibot.replay.spy_noise_band`) that prices fills with the cost models of
`kaibot.broker.costs_v2`; it is not the engine with `SimBroker`. Under `config/gate_v2.yaml` (`final_requires`)
such a run can fail Gate B, it cannot pass it. If it holds every mark, the next step is the same frozen rule through
the engine with `SimBroker`, under this same file. Research only: nothing in the engine, the risk gate, the broker,
the strategies or the config changes.

**Hindsight, stated plainly.** The out-of-sample window (2017-05 to 2026-11) is not unseen history to the people who
wrote this file: the 2018, 2020 and 2022 falls are common knowledge. That is why the base setting below is the
published one (Faber's 10-month average, checked at month-end), copied rather than chosen, and why the neighbours are
the textbook alternatives around it, declared now and judged by the same marks. Nothing may be changed after the
first run (a change is a new rule under a new name, and a new trial).

## Hypothesis

Holding SPY only while its month-end close is above its 10-month simple moving average, and cash otherwise, keeps
most of the market's return while sidestepping part of the long, slow bear markets, so the maximum drawdown is
clearly shallower than SPY's. Expectation written before the run, net of costs, against SPY total return over the
same sessions: **CAGR about SPY -3 to 0 points a year, maximum drawdown about 0.5 to 0.8 times SPY's**, in the market
about 75 to 90% of sessions, a handful of round trips a decade. The published record since 2009 says the filter is
insurance, not alpha: it lags in V-shaped recoveries (whipsaw: it sells after a fast fall and buys back higher), so
a failure of mark 2 (return more than 2 points behind SPY, or a drawdown not shallow enough) or of mark 3 (no better
than random timing with the same time in the market) is a real possibility. "Nothing beats holding SPY" is a
legitimate finding.

## Who is on the other side of the trade

There is no edge against a counterparty in the usual sense; the rule is a risk overlay. What it relies on is that
large falls of the index tend to come in persistent downtrends rather than single days: investors underreact to
slowly arriving bad news and then sell together, and mechanical de-risking (volatility-targeting and risk-parity
funds, margin calls, trend-following CTAs, pension and target-date rebalancing bands) adds to selling once a decline
is under way. Time-series momentum at horizons of 1 to 12 months is documented across asset classes for that reason.
Who gains when the rule is wrong: the investors who buy the dip and hold through the fall are paid when the market
turns quickly (2020), and they collect the return the rule gives up by sitting in cash while SPY rises; that
whipsaw cost, plus the spread paid on each switch, is what the rule pays for the shallower drawdown.

## Published evidence

- Faber, M. T. (2007), "A Quantitative Approach to Tactical Asset Allocation", Journal of Wealth Management 9(4)
  (updated 2013): buy when the monthly close is above the 10-month simple moving average, sell to cash when it is
  below, checked on the last day of the month; on US stocks since 1900 a similar return to buy-and-hold with much
  lower volatility and drawdown. Out of sample 2006-2016 (after publication): 8.5% a year against 7.7% for the
  S&P 500, volatility 7.4% against 14.7%, maximum drawdown 16.7% against 51%, but behind stocks in six of the eight
  years after 2009 (Swedroe, etf.com, in `docs/research/engine_v3/notes_multi_day_strategies.md` sections 2 and 5).
- The 200-day rule on the S&P 500 through 2024: better than buy-and-hold 1999-2010 with half the volatility, behind
  it in every period since the financial crisis; about 45 of 47 switches in 1999-2010 were false signals; in 2020 it
  sold during the crash and re-entered late (Of Dollars and Data, March 2025; same notes, section 2).
- S&P Composite 1860-2014 with 0.25% a side: moving-average timing had a higher Sharpe than the market over the full
  period, but only 7 to 14% higher and not significant over 1932-2014 (CXO Advisory; same notes). SPY 1993-2010 at
  0.2% a side: the 10-month rule beat buy-and-hold on terminal value and volatility, but random monthly timing also
  tended to beat buy-and-hold in the 2000s, which weakens the evidence for the rule itself (CXO Advisory; same notes).
  That is exactly the question mark 3's random-timing control asks.
- Moskowitz, Ooi and Pedersen (2012), "Time Series Momentum", Journal of Financial Economics 104(2), and Hurst, Ooi
  and Pedersen (2017), "A Century of Evidence on Trend-Following Investing", Journal of Portfolio Management 44(1):
  the persistence the rule relies on, across 58 to 67 markets; it comes from long-short, volatility-targeted futures
  portfolios, not from a long-only, single-index filter (same notes, section 2). Zakamulin (2014), "The real-life
  performance of market timing with moving average and time-series momentum rules", Journal of Asset Management
  15(4): after costs and out of sample, the advantage of such rules over buy-and-hold is much smaller than in-sample
  tests show and concentrated in a few bear markets (cited from the literature, not re-read for this file).
- Our own research summary, `docs/research/engine_v3/report.md` (table of claims) and
  `docs/research/engine_v3/notes_multi_day_strategies.md` section 2: "Since 2009 an index trend filter has cost
  return in exchange for lower drawdown: it is insurance, not alpha ... Whipsaw is the cost ... monthly evaluation
  reduces but does not remove them."

## Frozen settings

| Part | Setting (source) |
|---|---|
| Instrument | SPY only, long only, 1x. When in, the whole account is in SPY (fractional shares, never more than the cash); when out, all cash (decision of the stakeholder, 13 Nov 2026) |
| Data | the daily store `data/daily` (#124): raw SPY bars with the corporate-actions table; no `.env` and no network needed |
| Signal price | SPY's close, split-adjusted, without dividends (Faber uses the price; the store's `adjusted("split")`, the signal convention of the other multi-day rules). A ratio, so the same whatever the adjustment date |
| Check | the last session of each calendar month (Faber 2007): a session whose next session in the store is in another month |
| Trend | the 10-month simple moving average: the mean of the closes of the last 10 month-end sessions, the current check included (Faber 2007) |
| Decision | at the close of a check session T: **in** if the close is strictly above the average, **out** otherwise. No decision before 10 month-ends of history exist (the account stays in cash) |
| Fill | at the **close of T+1** (decision 15 base fill: decide after the close of T, fill at the close of T+1). Between checks nothing changes |
| Dividends | credited in cash at the ex-date (to the shares held at the previous close), and while the account is in, reinvested at that session's close through the same fill model (total return, as the SPY benchmark reinvests at the ex-date close). The pay-date lag is not modelled |
| Cash | earns 0 (the base). A T-bill variant is reported separately, never judged, only if a free T-bill series is on disk (section "T-bill variant" below) |
| Account | one continuous $100,000 account, no walk-forward tuning. It starts flat at the close of the session before the first test session and takes the position of the last check made before it at the first test session's close |
| Window | out of sample **2017-05-01 to 2026-11-06**, the window of the other decision-15 rules (`gate_b.out_of_sample_from` 2017-05-01; history from 2016-01-01 for the averages; `KAIBOT_EXP_END` 2026-11-06). An open position at the end is marked at the last close, not sold |
| Index filter, stops, sizing, earnings, level | none: the rule is itself the index filter; no stop, no risk-profile sizing, no earnings version (an index); the level of the risk profiles plays no part |

Choices where the published rule is silent (made here, before any run):

1. The month-end is read from the store's own session calendar (the next stored session is in another month); the
   exchange calendar knows it in advance, so this is not look-ahead. The last stored session is never a check.
2. A missing SPY close on a session (none expected) is not a check and carries the previous state; the fill moves to
   the next session that has a close.
3. "Strictly above": a close equal to the average is out.
4. Amounts under $1 of cash are not invested (dividend crumbs); a buy spends the cash less the modelled costs.
5. Statistics are daily mark-to-market returns of the account at each close; Sharpe annualised with 252 sessions
   and no risk-free rate, as every Sharpe of gate v2.

## Neighbours (at most five)

Declared now for the plateau check; they are not a menu, and none of them replaces the frozen setting. Each differs
from it in one setting only; every one must pass mark 1 of Gate B (mark 6).

- `sma150d`: the trend is the mean of the last 150 session closes instead of 10 month-ends (still checked at month-end)
- `sma200d`: the mean of the last 200 session closes (the classic 200-day rule, checked at month-end)
- `sma250d`: the mean of the last 250 session closes (checked at month-end)
- `weekly`: checked at the last session of every week instead of every month; the average is the current close and the previous 9 month-end closes
- `band1`: a 1% band around the 10-month average: in only when the close is above 1.01 x the average, out only when it is below 0.99 x the average, otherwise the previous state is kept (starting out)

## Fill model

- **Base (judged):** decide after the close of T, fill at the **close of T+1** at the closing-auction price with
  half a spread per side plus impact.
- **Cost model v1** (`costs_v2.get_model("v1")`, the replay's half-spread of at least 1 bp and $0.005, plus v1's
  impact): sigma from the previous 20 sessions' daily close-to-close returns over sqrt(390); the fill period's volume
  is 2% of the day's volume (the daily store's convention without minute data); sale fees on every sale.
- **Cost model v2** (`costs_v2.get_model("v2")`, issue #111): venue `close` (one full half-spread for SPY's ADV
  bucket, half the continuous impact), ADV the mean dollar volume of the 20 sessions before the fill, raw prices;
  sale fees.
- **Stress fill (reported):** the **open of T+1** with the spread and impact doubled (v1) or venue `open` (v2, which
  charges twice the midday half-spread).
- **Cost multiples:** 0x (gross; break-even), 1x, 2x and 3x of spread and impact for both models; fees stay at 1x.
  Break-even: the multiple of spread and impact at which the account's net profit reaches zero, from the 0x and 1x
  runs; gate v2's own break-even multiple of the daily cost series is reported beside it.

## Pass marks

The Gate B and Gate C marks of `config/gate_v2.yaml` as committed together with this file (decision 15 sections 7
and 10), unchanged, judged by `kaibot.evaluation.gate_v2.evaluate_gate_b` on the base configuration over
2017-05-01 to 2026-11-06, once with cost model v1 and once with cost model v2 (both reported; a mark failing under
either is reported as failing; the gate's `final_requires` names v2):

1. net return positive overall and in at least 6 of 10 calendar years (a part-year counts with at least 6 months of
   out-of-sample data: 2017 from May and 2026 to 6 November both count, so 10 years);
2. either CAGR at least SPY total-return CAGR - 2 points with maximum drawdown at most 0.75 x SPY's, or CAGR at least
   SPY + 1 point with maximum drawdown at most 1.1 x SPY's; SPY total return from the daily store over the same
   sessions (`multiday_core.spy_total_return`), never price only;
3. net Sharpe at or above the 90th percentile of at least 500 seeded **random-timing** accounts (decision 15 section
   10, mark 3 for SPY-only rules), each built as follows:
   - the **same number of SPY round trips** as the rule's own run, a position still open at the end of the window
     counting as one, held to the end and not sold;
   - the rule's **own holding durations** in sessions (from the buy fill to the sell fill), each used exactly once, in
     a random order, so the time in the market (the exposure) is the rule's;
   - each round trip bought at the close of a **random session** of the window, drawn uniformly among the sessions
     where the whole holding fits inside the window and does not touch a round trip already placed (never sold and
     bought at the same close);
   - one position at a time, the whole account in SPY when in, cash at 0 otherwise, dividends as in the rule, priced
     by the **same account routine and the same costs** (the base fill and costs of the model being judged).
   Run k is seeded by (201, k). The percentile is the share of random accounts with a net Sharpe strictly below the
   rule's (ties count against it); the total-return percentile is reported beside it, not judged. The control is a
   report of the configuration, not a trial;
4. marks 1 and 2 still hold at 2x costs (gate v2 recomputes them on the daily cost series; the 2x and 3x runs and the
   break-even are reported);
5. holds on the point-in-time universe: the rule has no stock universe, and SPY traded on every session of the
   window, so the run itself is the point-in-time run (no survivorship choice is involved); marks 1 to 4 of this run
   are mark 5;
6. every pre-declared neighbour above passes mark 1 (base fill, the model being judged).

Nothing stricter is added and nothing is loosened. Gate C (paper incubation, including the retirement trigger) is
config/gate_v2.yaml's, unchanged, and arises only after a pass from the engine with `SimBroker`.

## T-bill variant (reported, never judged)

If, and only if, the daily store already holds **BIL** (SPDR Bloomberg 1-3 Month T-Bill ETF, free daily bars from the
same source as SPY: `python scripts/fetch_daily.py --symbols BIL`), the base configuration is reported once more with
the cash balance earning BIL's daily total return (no cost for the cash sweep). If BIL is missing or has a gap in the
window, the variant is skipped with that reason. It is a report of the base configuration (`n_trials` 0) and plays
no part in any mark.

## Trials

Every run appends rows to the one trial registry, `data/trials.jsonl` (counted by `scripts/trial_registry.py count`),
each carrying this file's sha256 and the sha256 of `config/gate_v2.yaml`. Decision 15 section 10: every new
configuration counts 1, cost and fill re-runs of the same configuration count 0. So the base configuration at cost
model v1, base fill, 1x costs is one trial (`n_trials` 1), each of the five neighbours at the same setting is one
more (the plateau check is part of the search and is counted, never hidden); every other row (cost model v2, 0x, 2x,
3x, the stress fill, the T-bill variant) and the random-timing control count 0.

## Amendment 1 (13 Nov 2026, backend-quant, issue #201, before any run on real data)

Written before the rule or its control had been run on real data, and before any percentile of the control had been
seen on any data. It changes how mark 3's random round trips are placed and nothing else: the construction, the
neighbours, the fill and cost models, the trials and marks 1 to 6 stand as committed.

Why: while the control was being written, a unit test on synthetic data showed that placing the round trips one
after another, each at a uniformly drawn free session, often leaves no room for the last ones when the rule is in
the market most of the time (a few long holdings filling 70 to 90% of the window): such a run is not matched, and
mark 3 would stay unknown for a reason that has nothing to do with the rule.

The placement, instead: each random account takes the rule's holding durations in a **random order** and separates
them by **random gaps**. A holding of d sessions occupies its d sessions and its sale session (a holding that reaches
the end of the window is held to the end and its "sale session" is past the end), so a sale and a buy never share a
close and two holdings never merge. The free sessions left over are split into one more gap than there are holdings
by a uniform random composition (every split equally likely), so the first holding can start on the first session
and the last can run to the end. Every holding is placed exactly once whenever the rule's own holdings fit, which
they always do, so the control is matched; the number of round trips, the durations and the time in the market are
the rule's exactly. Everything else of mark 3 is as committed: at least 500 runs, run k seeded by (201, k), the same
account routine and costs, ties counting against the rule, the 90th percentile of `config/gate_v2.yaml`.
