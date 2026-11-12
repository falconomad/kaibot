# Daily bar store (story S84, issue #124)

Raw daily bars from 2016 for SPY, the 11 sector ETFs and the 288 stocks of `config/universe_history.yaml`, with a
table of splits and dividends, kept under `data/daily`. Prices are adjusted when they are read, as of a decision
date: signals read adjusted prices, orders and costs read raw prices. This repairs audit finding E4 (bars
split-adjusted as of the download date, cost floors applied to back-adjusted prices, no dividends in the strategy
or in the SPY benchmark). **Research data only: the live engine never reads it** (a test enforces this).

## The one command (on the Mac, from the repo root)

```
./scripts/fetch_daily.sh
```

| | |
|---|---|
| What it does | downloads the bars and the corporate actions, then runs the offline checks |
| Time | about 7 minutes for today's 300 symbols (about 17 minutes while the engine runs); the checks take 1-2 minutes more |
| Size | about 180 MB of transfer, 50 MB on disk |
| Basis of both numbers | an estimate, not a measurement: about 620 requests, and file sizes measured on synthetic data of the same shape |
| With `data/universe/former_members.csv` (story S83) | every former index member is added, as of a date inside its life; expect roughly double |
| Resumable | stop with Ctrl-C and run it again: finished symbols are skipped |
| Requests | two per symbol plus a few dozen for the corporate actions, at 120 a minute (40 while the engine runs); Alpaca allows 200 |
| Other forms | `./scripts/fetch_daily.sh --status` (what is stored), `./scripts/fetch_daily.sh --check` (checks only, no keys) |

The keys are read from `~/Projects/kaibot-secrets/.env` (or `KAIBOT_ENV_FILE`) and never printed. Logs:
`logs/fetch_daily.log`, `logs/fetch_daily_check.log`. **After the first run, send `data/daily/check/summary.txt`
to the Data team**: it answers the open questions listed under "Not verified" below.

Run it again whenever fresh data is wanted. Raw bars of the past never change, so a symbol is downloaded again
only when a new session may exist; a delisted series is never downloaded twice. A session is stored once it is
complete (20:15 New York time: extended-hours trades still add to the day's volume until 20:00).

For a hand-picked list: `.venv/bin/python scripts/fetch_daily.py --symbols NBIS,CRWV` (SPY plus exactly these), or
`--no-universe --symbols-file some.csv` (only the lines of that file). All options: `--help`.

## What is stored

```
data/daily/bars/symbol=<KEY>.parquet      raw bars of one company, one row per session
data/daily/bars/symbol=<KEY>.empty        asked, nothing usable came back (JSON says why)
data/daily/holes.csv                      the hole log: every key without usable bars
data/daily/ref_all/symbol=<KEY>.parquet   Alpaca's own adjusted close and volume as of the same fetch (check only)
data/daily/corporate_actions.parquet      splits, stock dividends, cash dividends; spin-offs and name changes as information
data/daily/check/                         what the checks found (see "The checks")
```

`KEY` is the ticker (today's company with that ticker), or `TICKER@YYYY-MM-DD` for a ticker requested as of a date.
The second form comes from `--symbols-file`, a CSV with the header `symbol,asof_date` (the contract with story
S83, #123). It is needed because a ticker can belong to different companies over time: `DOW@2017-06-30` is the
old Dow Chemical, `DOW` is the company listed in 2019. If Alpaca does not know the ticker on that date it answers
for the ticker alone, which may be another company; the fetcher detects this (no bar within 10 days of the date)
and logs a hole (`asof_not_covered`) instead of storing the wrong company.

**Bars** (`bars/`): `date` (session date, New York), `o h l c` (raw prices, as traded), `v` (raw shares),
`n` (trades), `vw` (volume-weighted price), `available_ts` (UTC, 20:15 New York time of the session: when the
complete bar can first be known).

**Corporate actions**: `key`, `symbol`, `type` (`forward_split`, `reverse_split`, `stock_dividend`,
`cash_dividend`, `spin_off`, `name_change`), `ex_date`, `ratio` (new shares per old share: 4.0 for a 4-for-1
split, 0.1 for a 1-for-10 reverse split, 1.05 for a 5% stock dividend), `cash` (dividend per share), `special`,
`foreign`, `process_date`, `record_date`, `payable_date`, `cusip`, `id`, `dup`, `raw` (the row as Alpaca sent it).

- An action is tied to a key when the ticker matches and the ex-date lies inside that key's bars.
- Alpaca sometimes lists one payment twice (two ids, often two CUSIPs). A row with the same key, type, ex-date,
  amount and special flag as an earlier row gets `dup = true` and is skipped by the reader. Nothing is deleted.
- Spin-offs and name changes are stored but not applied to prices (see "Known limits").

## Reading it

```python
from kaibot.ingest.daily_store import DailyStore

store = DailyStore("data/daily")
raw = store.panel(symbols, "2016-01-01", "2026-11-11")   # (sessions x symbols) arrays: o h l c v, split, div
sig = raw.adjusted("total")                              # splits, stock dividends and cash dividends removed
sig_split = raw.adjusted("split")                        # splits and stock dividends only
then = raw.adjusted("total", as_of="2020-06-30")         # the history as it looked after that day's close
spy = store.total_return("SPY")                          # date, close, dividend, split, ret, index
```

The rule, for a session t on or before the decision date D:

```
adjusted[t] = raw[t] x the factor of every action with  t < ex-date <= D
    split or stock dividend:  1 / ratio            (volumes: x ratio)
    cash dividend:            1 - cash / (last raw close before the ex-date)      ("total" only)
```

- **No look-ahead.** An action that goes ex after D is not applied, sessions after D are not visible, and the
  price on D is the raw price of D. The view as of D is computed from data up to D only (a property test changes
  everything after D and requires an identical result, bit for bit).
- **Why a fast harness may still adjust once.** The series adjusted as of the last session, cut at D, equals the
  series as of D times one constant per symbol (also a property test). Returns, price over its average and any
  other ratio are therefore identical. Levels are not: divide an adjusted level by `sig.factor[i, j]` to get the
  tradable price of session i.
- **Total return** of a session is `(close x split + dividend) / previous close - 1`: the dividend counts on its
  ex-date and is reinvested at that close, as in a total-return index. `raw.total_return("SPY")` gives the daily
  series aligned with the panel; `store.total_return("SPY")` a table with the index.
- **Positions** live on raw prices. `raw.carry(i, j, qty)` returns the shares now held and the cash dividend of a
  position held at the previous close; `raw.hold(j, i_entry, i_exit, qty)` does it for a whole trade. A position
  bought on the ex-date does not get that dividend; one sold on the ex-date does.
- `source="auto"` (see the checks) uses Alpaca's own factor for the keys whose table failed the check.

## Hand-off to Backend-quant: the multi-day harness (#113)

`src/kaibot/replay/multiday.py` builds its `DailyBars` (o, h, l, c, v, c1555, v_first, v_last) from the minute
history, which is split-adjusted as of its download and has no dividends. The store gives the same fields:

```python
minute, _ = load_or_build_daily(data, symbols, start, end)        # unchanged: the only source of the 15:55 price
panel = DailyStore(data / "daily").panel(symbols, start, end, intraday=minute.to_frame())
adj = panel.adjusted("total", source="auto")
raw = DailyBars.from_frame(panel.to_frame(), symbols)             # market side
sig = DailyBars.from_frame(adj.to_frame(), symbols)               # signal side
```

- `o h l c v` come from the store (official open and close, full-day volume). `c1555`, `v_first`, `v_last` exist
  only inside the day, so they come from the `intraday` frame and are put back on the raw basis; a day whose
  minute close is more than 5% from the stored close is dropped (`panel.notes`). Without minute data (former
  index members) `c1555` is NaN, so a rule that decides at 15:55 cannot trade them, and the official close never
  stands in for a 15:55 price; `v_first` and `v_last` are then 2% of the day's volume (the number
  `DailyBars.make` already uses).
- **Signals on `sig`**: `indicators(sig)` (ATR, sigma, returns), new highs, momentum ranks, averages.
- **Market on `raw`**: fills, `half_spread`, `buy_px`, `sell_px`, fees, whole-share counts, stop levels, the
  minimum-price filter and dollar volume (`MIN_PRICE`, `MIN_ADV`). An ATR computed on `sig` is in adjusted
  dollars: use `atr / adj.factor[i, j]` (or the ratio `atr / sig.c`) before sizing or placing a stop.
- **Positions through corporate actions**, at the start of `Engine.step(i)`, for every open position:
  `qty, cash = panel.carry(i, j, qty)`; add `cash` to the account; divide the stop, the highest close, the fill
  and the last mark by `panel.split[i, j]`. Without this a held position shows a split as a 75% loss.
- **Benchmark**: replace `spy_buy_and_hold` (price only) by `panel.total_return("SPY")[i0:i1 + 1]`.
- **Keys to leave out**: `store.adjustment_report()`; for a key with `split_basis_days > 0` the table and
  Alpaca's bars disagree about a split, so its share count cannot be carried through that date (the check prints
  these keys as a warning).
- **Former members** (S83) appear as `TICKER@DATE` keys; `store.keys()` lists everything that has bars and
  `store.holes()` what Alpaca could not serve.

**Why costs must use raw prices (audit E4).** The cost model has absolute floors: `half_spread = max(1 bp of the
price, $0.005)` (`replay/sim.py`) and `min_spread = max(..., $0.01)` (`broker/shadow_costs.py`). NVDA traded near
$100 in early 2017. Its two later splits (4-for-1, then 10-for-1) put that bar at about $2.50 in a back-adjusted
series. Half a cent is 1 bp of $100 but 20 bp of $2.50, on each side, and the per-share fee is charged on forty
times as many shares. That is the 30 to 60 bp per round trip, instead of about 5, of the audit. On `raw` the
floors apply to the price the order would really have met, so the distortion disappears with no change to the
cost functions themselves.

## The XLC hole (audit E3)

XLC, the communication-services sector ETF, was launched in June 2018 (the check prints its first bar). The
intraday features need the sector ETF on the same dates, so the 18 stocks mapped to XLC produce no candidate
before about mid-July 2018. Those stocks did not lack a sector before that: the sector did not exist yet. It was
formed from the old telecom sector plus selected technology and consumer-discretionary companies, and the
matching changes to XLK and XLY took effect after the close of 21 September 2018 (launch announcement of the
fund). Until then each of the 18 stocks was in one of those older sectors.

| Treatment | What it means | Verdict |
|---|---|---|
| Use the sector each stock really had on each date (XLK or XLY before the reshuffle, XLC after) | exact, and also repairs "the sector map is today's, fixed" | correct; needs a dated sector map, which belongs with the point-in-time universe (S83) |
| Continue XLC backwards with SPY before its first bar | sector-relative features become market-relative for those stocks until June 2018 | simple and honest; recommended now |
| Build a synthetic sector index from the 18 members | keeps a sector-like series | adds today's membership to the past; not recommended |

Inside Data's folders: `panel.spliced_close("XLC", "SPY")` returns XLC's close with the sessions before its first
bar filled from SPY's moves and joined at that bar without a jump (use an adjusted panel; the filled levels are
synthetic, so use them for returns, betas and relative strength only). The check prints XLC's first bar.
**Hand-off:** the gap itself is in `src/kaibot/replay/features.py` (minute bars of the sector ETF). There the
same fallback is one substitution: for sessions before XLC's first bar, pass SPY's bars as the sector series of
those 18 stocks.

## The checks (`./scripts/fetch_daily.sh --check`, offline)

1. **Minute history against the daily bars**, for 20 fixed symbols (splits, large dividends, spin-offs, renamed
   and reused tickers) and 10 drawn with a fixed seed. Every session of `data/bars_1m` is turned into one bar the
   way the harness does it and compared with Alpaca's daily bar on the same split basis. Nothing is corrected on
   either side. Expected: the open matches on most days; the close differs on most days by a few basis points
   (the minute bars stop at 15:59, the daily close is the last trade of the day, normally the closing-auction
   print); the 09:30-15:59 minutes hold less than the daily volume (closing auction and extended hours). A close
   more than 5% apart is not an auction effect but another split basis (minute files downloaded before a later
   split, or a split missing from the table); those sessions are counted and listed separately.
2. **The corporate-actions table against Alpaca's own adjusted bars**, for every key. The ratio of Alpaca's
   `adjustment=all` series to the raw series is the factor Alpaca applies; it is compared step by step with the
   table's. Verdict per key: `ok`, `mismatch`, `noisy` (rounding too coarse to verify), `no_reference`.
3. SPY's dividends per calendar year (fewer than four is called out) and XLC's first bar.

| File in `data/daily/check/` | Content |
|---|---|
| `summary.txt` | the findings in plain words (also printed) |
| `minute_vs_daily.csv` | every compared session: difference of open, high, low, close in basis points, volume ratio |
| `minute_vs_daily_summary.csv` | per symbol: how often each price differs, median, 95th percentile, largest |
| `adjustment.csv` | per key: verdict, number of differing steps, days on another split basis |
| `adjustment_steps.csv` | every differing step: date, the factor Alpaca applies, the factor the table gives, class |

Step classes: `missing_in_table` (Alpaca adjusts, the table has nothing), `spin_off` (the table lists a spin-off
there), `dropped_duplicate` (the table has a row there that was flagged as a repeat), `factor_differs`.
`adjusted(source="auto")` uses Alpaca's factor for every `mismatch` key and the table for the rest, and records
which in `adj_source`.

## Alpaca parameters

Checked against docs.alpaca.markets on 12 Nov 2026 (bars reference, corporate-actions reference, market-data FAQ,
plan table):

- `GET https://data.alpaca.markets/v2/stocks/{symbol}/bars`: `timeframe=1Day`; `adjustment=raw` (the default;
  `split` adjusts price and volume, `dividend` and `spin-off` price only, `all` applies the three); `feed=sip`;
  `limit` up to 10000 a page with `next_page_token` / `page_token`; `start` and `end` inclusive; `asof=YYYY-MM-DD`
  finds the company that had the ticker on that date (default: today; if the ticker is not found on that date the
  answer is for the ticker alone).
- SIP history is free when `end` is at least 15 minutes old; history since 2016; 200 calls a minute.
- Daily bars are built from trades, stamped with midnight New York time. Extended-hours trades add to the daily
  volume but not to open, high, low or close; "official open / close" prints change nothing.
- `GET https://data.alpaca.markets/v1/corporate-actions`: `symbols`, `types` (among them `forward_split`,
  `reverse_split`, `stock_dividend`, `cash_dividend`, `spin_off`, `name_change`), `start`, `end` (inclusive,
  default today), `limit` up to 1000, `page_token`.

**Not verified** (the documentation is silent or the page shows no schema; the first run settles each):

| Question | What the code assumes | How the first run answers it |
|---|---|---|
| Field names of the corporate-actions response | `corporate_actions.<type>s[]` with `symbol`, `ex_date`, `process_date`, `new_rate` / `old_rate` (splits), `rate` (dividends), `special`, `foreign`; `source_symbol` (spin-offs), `old_symbol` / `new_symbol` (name changes); `cusip` and `id` if present. Sources: a July 2024 example on Alpaca's forum (splits), a third-party client library (dividends, stock dividends, spin-offs), an Alpaca changelog entry (the `<type>s` list names); not the reference page | rows by type and the ex-date range are printed; another shape stops with a clear error; a field under another name shows as rows "left out" or as `missing_in_table` steps |
| Does the free plan serve `/v1/corporate-actions`? | yes (project research; `scripts/smoke_test_keys.py` probes it) | HTTP 403 stops the run and says so; the bars stay |
| How far back do corporate actions go? | to 2016 | the first ex-date is printed; `missing_in_table` steps and "fewer than 4 SPY dividends" show a shorter history |
| Do `start` / `end` filter on the process date, may `end` lie in the future, is there a maximum range? | one request from 2016 to today + 120 days | a 400 or 422 switches to one calendar year per request, automatically |
| Meaning of `stock_dividend.rate` | extra shares per share (ratio = 1 + rate) | the split-basis check against Alpaca's adjusted volume |
| Are actions booked under a former ticker returned for today's ticker (META / FB, ELV / ANTM)? | not relied on | `missing_in_table` steps before the rename date for those keys; `source="auto"` covers prices meanwhile |
| What `asof` returns for a delisted ticker (the page describes renames only) | the old company's bars | holes with `asof_not_covered` in `holes.csv` |
| How Alpaca rounds adjusted prices, and its exact dividend formula | none assumed: the check tolerates rounding and 25% of a dividend's size | `noisy` verdicts |
| Field names of the bars response (`bars`, `t o h l c v n vw`, `next_page_token`) | as `scripts/fetch_history.py` has used them since M0 | - |

## Known limits

- **Spin-offs** are not applied by the table: the parent shows the loss of the spun-off value on the ex-date. In
  signals `source="auto"` removes it (Alpaca's factor); a simulated position is not credited the new shares, which
  understates the return of the few affected names. Mergers and delistings are not in the table: the last bar is
  the last price (terminal returns for holes are story S83).
- A dividend is credited on its ex-date, not on its pay date. When a dividend and a split share an ex-date the
  dividend is taken per share held before the split. An action lands on the first bar on or after its ex-date.
- Two genuinely separate payments with the same ex-date, amount and special flag would be read as one (the
  repeat flag); the check reports them as `dropped_duplicate` if Alpaca's own bars apply both.
- Capital-gains distributions (a type Alpaca added in August 2026) are not requested: none are expected for
  stocks, SPY or the sector ETFs. If Alpaca's bars adjust for one, the check shows it as `missing_in_table`.
- The corporate-actions table is rebuilt on every run; an action Alpaca adds late appears with the next run.
