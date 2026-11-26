# New-listing watch (story S102, #376)

A **shadow collector**. It writes a store that the engine never reads. It changes no order, no rule and no engine
decision. `tests/unit/test_listings.py` proves that nothing under `src/kaibot/{engine,risk,strategies,broker,
features,replay}` imports it.

Why: on 19 Nov 2026 Anthropic (ANT) began trading on Nasdaq and Kaibot did not know, although Nasdaq had published
the date on 11 Nov. No rule could have traded it, and none should have (design for M8, section 3). But Kaibot should
know that a listing is coming, record it point in time, and say when each rule could first consider it.

## What the stakeholder runs on the Mac

```bash
cd ~/Projects/kaibot && git pull

# 1. Once: the history since 2016. About 45 SEC requests, a few minutes (it downloads 44 quarterly indexes).
.venv/bin/python scripts/fetch_listings.py --since 2016-01-01

# 1b. Once, after 1: first sessions of the listings that data/daily does not hold, from Alpaca daily bars (paper
#     keys; one request per listing, at most 40 a minute, resumable). Writes data/listings only.
.venv/bin/python scripts/fetch_listings.py --env ~/Projects/kaibot-secrets/.env --first-bars

# 2. Once: the first night of the watch. About 85 daily indexes and up to a few hundred company lookups at SEC,
#    one request to Alpaca for the asset list, three to Nasdaq Trader: 3-5 minutes. The --listing-notice part is
#    only for this first run: it reads Nasdaq's notice of 11 Nov in case the news feed no longer lists it.
.venv/bin/python scripts/collect_alt.py --env ~/Projects/kaibot-secrets/.env --source listings \
  --listing-notice "https://www.nasdaqtrader.com/TraderNews.aspx?id=DTN2026-28"

# 3. Look at the result. These read local files only.
.venv/bin/python scripts/fetch_listings.py --show ANT       # ANT, what it rests on, and its "first eligible for" table
.venv/bin/python scripts/fetch_listings.py --status         # the history: counts of the last build
.venv/bin/python -c "import pandas as pd; d = pd.read_parquet('data/listings/ipos.parquet'); print(d[d.symbol.isin(['SPCX', 'ANT'])].T)"
```

The times and request counts on this page are estimates; each run prints its own.

| Command | Requests | Time | When |
|---|---|---|---|
| `fetch_listings.py --since 2016-01-01` | about 45 to SEC | a few minutes | **outside 15:30-22:00 Berlin** |
| `collect_alt.py --source listings`, first run | about 85 + up to 400 to SEC, 1 + a few to Alpaca, 3-4 to Nasdaq Trader | 3-5 minutes | **outside 15:30-22:00 Berlin** |
| `collect_alt.py --source listings`, every night after | about 20-100 to SEC, 1 + at most 40 to Alpaca, 3 to Nasdaq Trader | about a minute | 00:45 Berlin with the other collectors |
| `fetch_listings.py --first-bars` (after the first command) | one to Alpaca per listing of kind `ipo` outside `data/daily`, perhaps 1,000-1,500 | at most 40 a minute, so up to about 40 minutes; resumable | any time; the engine may stay on |
| `fetch_listings.py --show / --report / --status / --build / --eligible` | none | seconds | any time: they read local files only |
| `fetch_listings.py --since 2016-01-01 --prices` (optional, not needed for the acceptance criteria) | one per listing, a few thousand | 10-20 minutes, about 500 MB | **outside 15:30-22:00 Berlin** |

- **Every night after that there is nothing to run** if the nightly job of `docs/ops/alt_data.md`
  (`com.kaibot.collect-alt`, 00:45 Berlin) is installed: `listings` is one more source of `collect_alt.py`, and the
  job's default is all sources. If that job is not installed, run the command of step 2 without `--listing-notice`
  once a night after 00:45 Berlin.
- The watch needs `SEC_USER_AGENT` and the Alpaca **paper** keys from the `.env`. Both scripts read them at run
  time and print no key. `fetch_listings.py` reads only `SEC_USER_AGENT`, from `~/Projects/kaibot-secrets/.env`
  unless `--env` or `KAIBOT_ENV_FILE` says otherwise.
- Do not run either at the same time as `fetch_earnings_dates.sh`, `fetch_fundamentals.py` or `fetch_spinoffs.sh`:
  each process has its own SEC budget of 5 requests a second.
- The engine may stay on. The watch sends Alpaca one read-only request on the paper trading host and a few on the
  data host, far inside both budgets.
- Resumable. `fetch_listings.py` keeps every finished quarter; the watch keeps a ledger of the index days, the
  prospectuses and the notice pages it has read. A stopped run continues where it stopped.
- If one input fails (for example Nasdaq Trader), the others are still stored, the failed one is named in the log
  and the command ends with exit code 1. **Please send that line to the Data team**: the three sources could not
  be reached from the machine this was built on (see "Not verified" below).
- `fetch_listings.py` stops after 3 quarters in a row that fail the same way (it does not ask the other 41), and
  an answer that is not an index is named: `SEC answered HTTP <status>, <content-type>, <bytes>, starts '<first
  80 characters>'` (e-mail addresses and long tokens redacted).

## What it collects

| Input | What it gives | Requests | Budget (`config/sources.yaml`) |
|---|---|---|---|
| Alpaca assets, `/v2/assets?status=active&asset_class=us_equity` on the paper host | the symbol, once Alpaca has set it up. No listing date: tonight's list is compared with what the store knows, and a new asset id is a first sighting | 1 a night | `alpaca_trading` (entry `alpaca_assets`) |
| SEC EDGAR daily index, `daily-index/YYYY/QTRn/master.YYYYMMDD.idx` | forms S-1, S-1/A, F-1, F-1/A (planned), 8-A12B (exchange registration), 424B4 (final prospectus), 10-12B (spin-off): company, CIK, filing date | 1-2 a night; 120 days on the first run | `sec_edgar` (entry `sec_listings`) |
| SEC submissions file of a company with a fresh 8-A12B or 424B4 | the acceptance time of its filings, its ticker and exchange | one per such company | `sec_edgar` |
| The top 200 kB of a 424B4 of a company that also registered on an exchange | offer price, shares offered, the prospectus date, when the cover states exactly one of each | one per prospectus | `sec_edgar` |
| Nasdaq Trader symbol directory, `nasdaqlisted.txt` and `otherlisted.txt` | every listed symbol with its security name, the ETF flag and the test-issue flag | 2 a night | `nasdaq_trader`, at most one request a second |
| Nasdaq Trader "Data Technical News" feed, and the notice page when the feed text names no symbol | the symbol and the first trading date of a large listing, days ahead (ANT: 11 Nov for 19 Nov) | 1 a night + 1 per new notice | `nasdaq_trader` |
| Alpaca daily bars of a symbol that looks like a new listing | its first daily bar: the first session, confirmed. Or bars from weeks before: it traded before and is not new | at most 40 a night | `alpaca` (entry `alpaca_first_bar`), SIP data at least 20 minutes old |

## What it writes (not in git)

```
data/alt/listings/date=YYYY-MM-DD/part-*.parquet   the watch: one row per observation, append-only
data/alt/_meta/listings_state.json                 what was already read (index days, prospectuses, notice pages)
data/listings/ipos.parquet                         the history since 2016, one row per listing
data/listings/build.json, sec/                     counts of the last build; the cached quarterly extracts
```

**Point in time.** Every row has `available_ts`, the first time Kaibot could have known it, and `ingested_ts`,
when this Mac stored it.

| Row | `available_ts` |
|---|---|
| a symbol first seen (Alpaca, symbol directory), a ticker read from a submissions file, "traded before" | the collection time: a sighting has no earlier public time that could be proven |
| an EDGAR filing from the daily index | the file's Last-Modified, never before 22:00 ET of the filing day (when the nightly build starts); without that header, the collection time if it was read that night, else 06:00 ET the next morning |
| a Nasdaq notice | its publication time in the feed; for a notice page with a date only, the next midnight ET |
| a first daily bar | that session's close plus 15 minutes |
| a row that gained a value later (a ticker, an offer price) | the new version counts from the night it was added; the old version stays |

`kaibot.ingest.listings_pit.Listings` reads it. By default a row counts from `available_ts` (research: "what was
public then"). With `live=True` it counts from the later of `available_ts` and `ingested_ts` ("what this Mac had
really stored then"); a row collected late differs between the two.

```python
from datetime import UTC, datetime
from kaibot.ingest.listings_pit import Listings, first_eligible, listing_age_sessions

L = Listings.load()                                   # data/alt/listings and data/listings/ipos.parquet
now = datetime.now(UTC)
L.table(now)                                          # one row per listing candidate known now
L.listing("ANT", now)                                 # kind, status, first session and where the date comes from
L.evidence("ANT", now)                                # the filings, the notice, the sightings, each with available_ts
listing_age_sessions("ANT", now)                      # sessions traded so far, the first one counted as 1
first_eligible(L.listing("ANT", now)["first_session"])   # per rule: the first date it could consider the stock
```

`test_point_in_time_invariance` asks the reader at about 100 decision times and checks that deleting every row that
became available later never changes the answer.

## What counts as a new listing

Only an **initial public offering** or a **direct listing**. Everything else that makes a new symbol appear is
recognised and never reported as one:

| Kind | Recognised by |
|---|---|
| `ipo` | a Nasdaq notice that says so; Alpaca's `ipo` attribute; or an exchange registration (8-A12B) with a registration statement or a final prospectus around it |
| `direct_listing` | a Nasdaq notice that says so. From SEC filings alone it cannot be told from an IPO |
| `fund` | the ETF flag of the symbol directory, or "ETF", "ETN", "Fund", "Index", "Portfolio" in the name |
| `unit_warrant_right_preferred` | "Unit", "Warrant", "Right", "Preferred", "Notes", "Debentures" or a percentage in the name, or a symbol ending in `.U`, `.WS`, `.R`, `.PRx` |
| `ticker_change` | the same Alpaca asset under a new symbol; a security name that was on the list before under another symbol; or daily bars from weeks before the symbol appeared |
| `spin_off` | a Form 10-12B of the company and no final prospectus |
| `spac` | "Acquisition" in the company's name (a blank-check company) |
| `unknown` | a new symbol with nothing else known. Listed for review, not reported |

Symbols on Alpaca's over-the-counter list are stored and never become candidates. An S-1 alone is stored and not
reported: most S-1 filings are not initial public offerings.

Status: `registered` (8-A12B seen), `priced` (424B4 seen), `expected` (a first trading date is known and has not
come), `pre_listing` (Alpaca takes orders before the first trade), `listed`. The first session comes, best first,
from the first daily bar, from Nasdaq's notice, or from the night Alpaca first showed the symbol as tradable.

`scripts/fetch_listings.py --report` prints, for the last 30 nights, how many new Alpaca symbols were of each kind
and which were reported as new listings. That is the false-alarm check of the issue: after 30 nights no fund,
unit, warrant or ticker change may be in that last list.

## "First eligible for", per rule

From each rule's own warm-up (design for M8, section 3.2). The numbers are copied into `listings_pit.RULES`, because
the data package may not import the rules; `test_the_warm_ups_are_the_ones_in_the_rules_own_code` fails when a
rule's own number moves.

| Rule | Sessions of its own | Needs the S&P 500 | For ANT (first session 19 Nov 2026) |
|---|---|---|---|
| `orb_stocks_in_play` (shadow) | 15 | no | 10 Dec 2026, if it were in the live list |
| `news_continuation` (shadow) | 21 | no | 18 Dec 2026, if it were in the live list |
| the owner's own ticker list | 60 | no | 17 Feb 2027 |
| `trend_momentum` (on paper) | 100 | yes | 19 Nov 2027 at the earliest (its own 100 sessions are complete on 15 Apr 2027) |
| `mom_12_1_band` (off) | 273 | yes | 21 Dec 2027 |
| `post_earnings_week`, `news_gap_week` | none | yes | 19 Nov 2027 at the earliest, then the first qualifying event |

"Needs the S&P 500": the earliest date is the first session after 12 months of trading, which is the earliest S&P's
rules allow. It is an earliest date, not a forecast: S&P decides, and also asks for a public float of at least 10%.
These are the dates of section 3.3 of the design; a test holds them.

## The history (`data/listings/ipos.parquet`)

One row per first sale of shares on an exchange since 2016: the first 424B4 of a company that has an exchange
registration (8-A12B) from 60 days before to 10 days after it. A 424B4 without one is a later sale by a company
that is already listed and is left out.

| Column | Meaning |
|---|---|
| `company`, `cik` | EDGAR's company name and filer id |
| `symbol` | today's ticker of that filer; empty when the company no longer trades |
| `kind` | `ipo`, or `spac` for a blank-check company. A direct listing cannot be told apart here |
| `first_filed`, `registration_filed`, `prospectus_filed` | the dates of the first S-1 or F-1, the 8-A12B and the 424B4 |
| `first_session`, `first_session_source` | `daily_bar`: the first daily bar in `data/daily`, when the store holds the symbol and that bar lies within 10 days of the prospectus. Else `alpaca_bar`: the first Alpaca daily bar of today's ticker read by `--first-bars`, under the same 10-day test, and only if the ticker did not already trade in the first week of the 45 days asked for before the prospectus (an older company under the same ticker). **Otherwise empty** |
| `pricing_date`, `offer_price`, `shares_offered` | empty unless `--prices` read the prospectus cover and it stated exactly one of each. **Never guessed** |
| `available_ts` | the first session's close plus 15 minutes; without a first session, 06:00 ET the day after the prospectus |

The daily store holds the research universe and the S&P 500 members, so most listings have no first session from
it. SpaceX (SPCX) and Anthropic (ANT) are in neither. `fetch_listings.py --first-bars` reads the first daily bar of
such a listing from Alpaca into `data/listings/alpaca_first_bars.json`, one request per listing, and never writes
into `data/daily`, which the live daily engine reads (Product, 19 Nov 2026, on #386). Each answer is kept with its
verdict (`first_bar`, `traded_before`, `not_near`, `no_bars`, `no_data`) and is never asked again; a recent
listing whose 20 days after the prospectus have not passed yet, or whose bar is not final, is asked again on a later
run. `--build` merges the file offline. A blank-check company (`spac`) is not asked: it trades as units first.

## Not verified, and known limits

Not one of the three sources could be reached from the machine this was built on: Nasdaq Trader is blocked there,
`www.sec.gov` refuses a client that declares no contact, and Alpaca needs the keys. **Every test fixture is
hand-built** (`tests/fixtures/listings/README.md`). The first run on the Mac is the first real check of:

- the layout of EDGAR's daily and quarterly `master.idx` (written from memory; the first Mac run, 25 Nov 2026,
  showed that the quarterly column line ends in `Filename`, not `File Name` as the daily one: fixed in #376),
  and what EDGAR answers for a weekday without an index (404 is assumed; a 403 on every index is reported as a
  User-Agent problem);
- whether a company's submissions file already carries the ticker before the first trading day;
- the wording of a prospectus cover. An unreadable cover leaves the price empty;
- the URLs of the symbol directory files and the layout of the news feed (generic RSS is assumed), and the wording
  of a notice. Read on 19 Nov 2026 through a summarising tool: the notice's title, its date and the sentence "is
  expected to be listed and begin trading on the Nasdaq Global Select Market under the ticker symbol ANT on
  November 19, 2026";
- Alpaca's asset fields (`id`, `class`, `exchange`, `symbol`, `name`, `status`, `tradable`, `attributes` with
  `ipo`): read in Alpaca's API reference on 19 Nov 2026, no live answer seen. Not known: whether an asset keeps its
  id through a ticker change, and when the `ipo` attribute is removed on the first day.

Known limits:

- Alpaca's list on the first night is a baseline: a symbol already on it is never "first seen". ANT is found by
  Nasdaq's notice, its SEC filings and its first daily bar instead.
- The nightly job runs at 18:45 ET and EDGAR builds the day's index from 22:00 ET, so a filing is collected the
  night after it was filed.
- Nasdaq publishes such notices for large listings; NYSE listings come from Alpaca and EDGAR only.
- A company that traded over the counter and moves to an exchange with a priced offering is reported as an `ipo`.
- `Listings` folds the whole store on every new decision time. That is fine for a nightly look and for small
  studies, and too slow for a loop over years of decision times; such a study should use `ipos.parquet`.
- `news_continuation`: the table says the 21st session, as section 3.3 of the design does. By the rule's code
  (`prior_stats`: the standard deviation of the 20 daily returns before the session, and the first return needs two
  closes) its first possible signal is on the 22nd session, one later (21 Dec 2026 for ANT). Backend-quant should
  say which reading the table is to show. For `orb_stocks_in_play` code and design agree on the 15th.

## On the dashboard (#378)

`scripts/publish_notices.py` turns the watch's listings (and, once #377 is merged, the market-event alarm) into the
dashboard's "New on the market" and "Big day" notices: rows of `kaibot.market_notice` (migration 0035), read by
`kb_market_notices()`. Off by default. To switch it on, after the stakeholder's "apply" of 0035:

```bash
.venv/bin/python scripts/publish_notices.py --now 2026-11-19T06:45:00Z   # dry run: what 19 Nov looked like
.venv/bin/python scripts/publish_notices.py --write --env ~/Projects/kaibot-secrets/.env   # once, by hand
echo 'KAIBOT_NOTICES=1' >> ~/Projects/kaibot-secrets/.env               # then the nightly daily job runs it
```

It writes only `kaibot.market_notice`, never fails the daily job (log `logs/notices.log`) and is not on the engine's
path. Design: `docs/design/m8_market_notices.md`.
