# Pre-registration: `news_gap_week`

- **Rule:** `news_gap_week`
- **Status:** pre-registered (every result PROVISIONAL: research harness of #113, cost model v1; the headline versions run on today's stock list; it can fail Gate B, it cannot pass it)
- **Date:** 2026-11-21
- **Author:** backend-quant
- **Issue:** #372 (story S98, milestone M8); design `docs/research/event_driven/design.md`, section 2.5; data: #407 (news coverage), S100 (#374, 8-K filings), S85 (#125, earnings dates)

Written and committed before any run of this rule anywhere, and before its code: no headline, filing or bar has
been read for it. Append-only: a change is an `## Amendment <n>` at the end, committed before the run it affects.
Every number below is borrowed from a rule that is already frozen (`news_continuation`, `post_earnings_week`);
nothing is tuned here.

## Hypothesis

A large stock that closes sharply up on heavy volume **with** company news behind the move keeps drifting up for
about a week; the same jump **without** news tends to fade. Expectation written before the run: about zero alpha
net of costs in S&P 500 stocks (the documented effect is larger in small, thinly traded stocks and after bad
news, and Kaibot buys only); the headline must add something over "buy what jumped", or the rule is dropped.
"No better than SPY" and "no better than the no-news jumps" are legitimate findings.

## Who is on the other side of the trade

Investors who under-react to the news (they have not read it, or discount it and sell into the jump), and
short-term liquidity providers who sell the jump expecting the usual reversal of a move without information.

## Published evidence

Savor (2012, Journal of Financial Economics): large price moves with information (analyst reports) drift in the
same direction, moves without information reverse [checked: abstract only, no magnitudes]. Chan (2003): stocks
with public news continue, stocks that moved without news reverse. Tetlock (2010, 2011): less reversal after news
days, reversal after stale news. Jiang, Li and Wang (2021): news-driven returns continue for days [memory]. No
long-only, large-stock, after-cost test of this construction is known. Written up in design section 2.5; the
repository's own note: stock-level intraday continuation is weak outside the first minutes
(`docs/research/engine_v3/notes_intraday_overnight_strategies.md` section 4).

## Frozen settings

- **Harness.** The daily one-shot harness of #112 (`replay/oneshots_daily.py`, `scripts/oneshots.py`,
  `./scripts/run_experiments.sh oneshots`): the simulator of #113 (`multiday.Engine`), cost model v1, the data
  bundle, statistics, exposure-matched control and Gate B of `multiday_core`. No new simulator.
- **Universe.** The point-in-time S&P 500 (#123): a stock can be bought only on a decision day when it is a
  member and passes the harness filter (close >= $5, 20-day average dollar volume >= $50M, raw prices). Bars from
  the daily store (#124): signals on split-adjusted closes and volumes without dividends; orders, costs and share
  counts on raw prices; dividends credited.
- **Headline coverage (every version except `info8k`).** Headlines are the stored articles of `data/news`
  (`created_at`), one directory per company: a company has headlines when `data/news/symbol=<its bar-store key>`
  holds at least one. Former index members have none (#407), so these versions trade **today's list inside the
  point-in-time universe** (survivorship bias; mark 5 cannot be met). They trade only on event days in a
  **complete news month**, as `docs/prereg/news_continuation.md` defines it (`complete_months` of
  `replay/oneshots_intraday.py`): (a) at least 90% of the companies of `config/universe_history.yaml` with a
  daily bar that month have at least one stored headline in it; (b) the month's headline count is at least half
  the median monthly count over all months of the store; (c) the month ends on or before the day of the last
  stored headline. The report lists the complete months as date ranges and the number of companies with
  headlines.
- **Filings.** `data/filings/filings_8k.parquet` (S100): form `8-K` only (an `8-K/A` repeats an earlier filing),
  matched to bar-store columns by company as `earnings_pit` does; `accepted_ts` is the time it could be known.
- **Earnings days.** `data/earnings/announcements.parquet` (S85), the `report` rows: day 0 of a report (the
  first regular session that opens after the filing was accepted, as in `post_earnings_week`) is never an event
  day for that company. Those days belong to `post_earnings_week`.
- **Event day k** for a stock, all on data up to the close of k:
  1. jump: the stock's close-to-close return from k - 1 to k minus SPY's over the same closes (both
     split-adjusted) is at least **1.0 x sigma**, sigma being the standard deviation (ddof 1) of the stock's 20
     daily close-to-close returns of sessions k - 20 to k - 1 (all 20 present); the excess return is > 0;
  2. volume: the stock's volume on k is at least **3 x** the mean of its volumes on sessions k - 20 to k - 1
     (all 20 present);
  3. news: at least one stored headline of the company with `created_at` after the close of session k - 1 and at
     or before 15 minutes before the close of session k (NYSE times, early closes included,
     `ingest/nyse_sessions.close_utc`); a headline after that time never counts;
  4. not day 0 of an earnings report of the company;
  5. the stock is eligible on k (universe above) and, for the headline versions, has headlines and k lies in a
     complete news month.
- **Entry and exit.** Decide after the close of the event day k, buy at the close of k + 1, sell at the close of
  k + 6 (five sessions held, days +2 to +6). No stop. A name already held is not bought again.
- **Book.** At most 10 names, each sized to 10% of equity (whole shares, from the previous close), under the
  level's maximum position (Balanced 15%); more candidates than free slots: the largest excess return first (ties
  by column). Cash reserve: buys are sized against cash minus 3% of equity; a buy above the cash at the fill is
  cut to the whole shares that fit (Amendment 3 of the momentum pre-registrations, as in `post_earnings_week`).
- No index filter. Level Balanced, 100% exposure cap; the 30% and 60% cap rows of the harness grid are **not**
  run for this rule (they would add two configurations to the trial count for no question this story asks).
- Account: one continuous $100,000 account from 2017-05 to the end of the data, starting flat; idle cash stays
  cash (option A of design section 2.8; option B, chosen by Product for S99, is a new rule with its own file).

## Neighbours (at most five)

- `move2`: jump of at least 2.0 x sigma instead of 1.0.
- `relvol2`: volume of at least 2 x the 20-session mean instead of 3.
- `hold10`: hold 10 sessions instead of 5 (sell at the close of k + 11).
- `info8k`: the news test (item 3) is "an 8-K (form `8-K`) whose items do not include 2.02 was accepted after the
  close of k - 1 and at or before 15 minutes before the close of k" instead of a headline; no headline coverage
  needed and no complete-month restriction, so this neighbour runs on the **full point-in-time universe**.

**Control (one, not a neighbour):** `news_gap_week_nonews`: the base construction with item 3 reversed: the
company has headlines, k is in a complete news month, and there is **no** stored headline and **no** 8-K (any
items) in the window. Same book, entry and exit. The rule must clearly beat it (pass marks). It is not part of the
plateau check (mark 6).

## Fill model

BASE (decision 15): decide after the close of T, buy and sell at the close of T + 1 at half a spread a side plus
impact (cost model v1, `multiday.Engine`). STRESS: the open of T + 1 with spread and impact doubled. Costs 1x, 2x,
3x and a 0x run for the break-even multiple, for the base version. The neighbours and the control run at the base
fill and 1x costs only. A name without a bar on the fill day is not bought; a sale waits for the next bar. No
stops, so "a stop gapped through fills at the open" does not apply.

## Pass marks

The Gate B marks of `config/gate_v2.yaml` as committed together with this file (decision 15 section 7, marks
1-6, with the section 10 clarifications), judged on the base version (base fill, 1x costs, 100% cap) with SPY
total return from the daily store and the exposure-matched random control of the harness (500 runs; it draws from
every eligible point-in-time member, as for every daily rule). Mark 5 is "cannot be judged": the headline version
is on today's list. Results stay provisional (the go/no-go run is the engine with `SimBroker`): the one-shot can
fail, it cannot pass.

**Against the control.** "Clearly above the `nonews` control" means both: (1) the base version's daily Sharpe is
higher than the control's over the same window; (2) per trade, the net return (P&L after all costs over the
entry value) minus SPY's close-to-close return over the same holding sessions is higher on average for the base
than for the control, with a Welch t-statistic of at least 2.0. Fewer than 30 trades in either: "cannot be
judged", which counts as not clearly above.

**Reading (S96's).** Option A leaves cash idle, so mark 2 fails almost by construction (design section 2.8). The
rule goes on to S99 only if marks 1, 3 and 4 do not fail **and** it is clearly above the control; otherwise it is
dropped. The report shows marks 1-6, the comparison with the control, the average exposure, and the complete
news months and number of companies the headline versions could use.

**Trial count.** One frozen run. Six configurations are counted in the trial registry (`data/trials.jsonl`,
`n_trials: 1` each, with this file's sha256 and that of `config/gate_v2.yaml`): the base, the four neighbours and
the control. The base's cost and fill stress rows are logged with `n_trials: 0`, as for every one-shot. A second
run with the same dates finds the report and is skipped; no second attempt under another definition.
