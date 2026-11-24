# Company fundamentals from SEC EDGAR XBRL (point-in-time research store)

Research data only. The engine does not read it: no trading package may import `kaibot.data` or
`kaibot.ingest.sec_fundamentals` (`tests/unit/test_research_data_isolation.py`). Wiring it into features is a later,
separate story.

## Source and terms

| | |
|---|---|
| Endpoint | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, one request per company |
| Docs | sec.gov "EDGAR Application Programming Interfaces" (`/edgar/sec-api-documentation`), checked 24 Nov 2026 |
| Cost / key | Free, no key: "These APIs do not require any authentication or API keys to access." |
| Reuse | "Information presented on www.sec.gov is considered public information and may be copied or further distributed by users of the web site" (SEC web site privacy and security policy). Do not use the SEC seal or logos. |
| Fair access | "Current max request rate: 10 requests/second", with a declared User-Agent ("Sample Company Name AdminContact@<sample company domain>.com") (sec.gov "Accessing EDGAR Data"). Kaibot: 5 req/s via the shared `sec_edgar` budget (`config/sources.yaml`), 429/5xx retried with backoff, a 403 stops the run. |
| User-Agent | `KAIBOT_SEC_USER_AGENT="Your Name you@example.com"`, from the environment or the env file passed with `--env` (only that line is read; never printed). No value, or no email in it: the script refuses to run. |
| Cadence | SEC updates the JSON "throughout the day, in real time, as submissions are disseminated". Values change quarterly (10-Q, 10-K) plus amendments; a weekly or monthly fetch is enough for research. |

**The frames API is not used.** Frames "aggregates one fact for each reporting entity that is last filed": the
latest filed version, assigned with hindsight. For the same reason the `frame` field of companyfacts is not stored.

## What is stored (`data/fundamentals/`, not in git)

| File | What |
|---|---|
| `facts/cik=##########.parquet` | every filed version of the metrics below, one file per company |
| `ciks.parquet` | company, CIK, the index ticker in force, `valid_from` / `valid_to` (exclusive), how the CIK was found |
| `raw/companyfacts/CIK##########.json.gz` | the raw download (cache; `--build` rebuilds the parquet from it offline) |
| `build.json` | the run: requests, cached companies, new versions, companies without XBRL facts, errors |
| `cik_map.csv`, `sec/` | only when `data/earnings/cik_map.csv` did not exist: the resolver's output and cache |

Metrics (`kaibot.ingest.sec_fundamentals.METRICS`; concepts in priority order):

| metric | XBRL concepts | unit |
|---|---|---|
| `revenues` | us-gaap Revenues, RevenueFromContractWithCustomerExcludingAssessedTax, ...IncludingAssessedTax, SalesRevenueNet, SalesRevenueGoodsNet, SalesRevenueServicesNet | USD |
| `net_income` | NetIncomeLoss, ProfitLoss, NetIncomeLossAvailableToCommonStockholdersBasic | USD |
| `operating_cash_flow` | NetCashProvidedByUsedInOperatingActivities, ...ContinuingOperations | USD |
| `gross_profit` | GrossProfit | USD |
| `total_assets` | Assets | USD |
| `total_liabilities` | Liabilities | USD |
| `long_term_debt` | LongTermDebt, LongTermDebtNoncurrent, LongTermDebtAndCapitalLeaseObligations | USD |
| `shares_outstanding` | dei EntityCommonStockSharesOutstanding (cover page), us-gaap CommonStockSharesOutstanding | shares |

Columns: `cik, metric, priority, taxonomy, concept, unit, period_start` (null for an instant), `period_end,
duration_days, value, accn, form, fy, fp, filed, available_date, first_seen_ts`. `fy` / `fp` belong to the
**filing**, not to the value (a FY2023 10-K carries FY2021 comparatives with fy=2023); use the period columns.

## Point in time

- A version is usable from `available_date = filed + 1 day`, never from the period end. companyfacts gives the
  filing date, not the acceptance time, and EDGAR dates a filing accepted after 17:30 ET on the next business day,
  so the day after `filed` is the first day it was surely public.
- **Restatements:** every filed version is kept (same period, different `accn` / `filed`). The store is append-only:
  a later download that no longer carries an old version does not remove it.
- `Fundamentals(root).as_of(d)` returns, per (cik, metric, period_start, period_end), among versions with
  `available_date <= d`: the preferred concept, then the latest filed (tie: the larger accession number).
  `latest(d, metric, period="Q"|"H"|"9M"|"FY"|"instant")` gives the newest period per company;
  `history(cik, metric)` every version; `cik_for(ticker, d)` / `ticker_for(cik, d)` the point-in-time mapping.
- Tested on a fixture without network: `tests/test_sec_fundamentals.py` (a restated quarter and a 10-K/A switch
  exactly the day after their filing; a property test that nothing is visible on or before its filing day).

## CIK and ticker

The CIK is the key. The ticker on a date is the S&P 500 index ticker then in force (`ingest/sp500_pit.py`: FB until
8 June 2022, META after), joined to the CIK found by the earnings store's resolver (`earnings_sec.resolve`:
committed overrides, SEC's `company_tickers.json`, EDGAR's `cik-lookup-data.txt` name list, each CIK checked against
its own filings in the membership spell). The script reuses `data/earnings/cik_map.csv` when it exists, else
resolves the same way (about 2,000 more requests the first time).

## Run it on the Mac

```bash
cd ~/Projects/kaibot && git pull
# once: add the declared User-Agent to the env file (the same value as SEC_USER_AGENT is fine)
echo 'KAIBOT_SEC_USER_AGENT="Your Name you@example.com"' >> ~/Projects/kaibot-secrets/.env
PYTHONPATH=src python3 scripts/fetch_sec_fundamentals.py --env ~/Projects/kaibot-secrets/.env --limit 5   # trial
PYTHONPATH=src python3 scripts/fetch_sec_fundamentals.py --env ~/Projects/kaibot-secrets/.env             # all
PYTHONPATH=src python3 scripts/fetch_sec_fundamentals.py --status
```

About 700 requests, roughly 3-5 minutes at 5 req/s, a few hundred MB of gzip transfer, and on disk about 0.5-1 GB
of raw cache plus a few tens of MB of parquet. These are estimates; the first run confirms them in `build.json`.
Re-runs skip companies downloaded in the last 7 days (`--max-age-days`, `--refresh`). The run can be interrupted and
restarted. It may run while the engine runs, because the engine does not call SEC. Do not run it at the same time as
`collect_alt.py`, `fetch_earnings_dates.py` or `fetch_fundamentals.py`, since each process has its own 5 req/s SEC
budget. Please send `data/fundamentals/build.json` to the Data team.

## Known gaps

- **XBRL starts in 2009-2011** (phased in by filer size). The research window starts in 2016, so this is fine.
- **Only standard taxonomies.** Company-specific extension concepts are not in companyfacts. A company that
  reports revenue only under its own tag, or under a bank or insurance concept not listed (for example
  InterestAndDividendIncomeOperating), has no `revenues` row. `gross_profit` is missing for most banks, insurers
  and many service companies.
- **`total_liabilities`:** many companies do not tag `Liabilities` and report only LiabilitiesAndStockholdersEquity.
  No value is derived.
- **`long_term_debt` mixes definitions.** LongTermDebt includes the current portion; LongTermDebtNoncurrent does
  not. Check `concept` before comparing companies.
- **Quarterly flows:** Q4 is usually not filed; it only appears inside the 10-K's annual value, and many 10-Qs
  report Q2/Q3 as year-to-date (H, 9M). Deriving Q4 as FY minus 9M is left to the user, who must apply the
  `available_date` of the later of the two.
- **Share counts:** the dei cover-page count is dated near the filing date, not at the period end. Classes may be
  reported separately (only the total-class row, without dimensions, is in companyfacts).
- **Non-USD filers** (20-F, 40-F) report in other currencies and are skipped (counted in `other_unit_rows_skipped`).
- **Filing day, not time:** a value filed before the open could have been used the same day. The store waits one
  day, on the safe side.
- **Companies without a CIK** (`cik_misses.csv` of the earnings store) and CIKs without XBRL facts (`no_facts` in
  `build.json`, a 404) have no rows.
- **Corrections without a new filing:** if SEC re-disseminates a filing's data, the version keeps its `accn` and
  `filed` but may carry a new value. Both are kept, and the reader takes the larger `accn`, then the larger value
  for the same `filed`. This is rare and has not been seen in a test.
