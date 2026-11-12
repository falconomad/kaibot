# Earnings dates, point-in-time, from SEC filings (story S85, #125)

Research data only. The live engine does not read any of it. Audit finding E6: the only earnings protection is "any
headline in 24 hours", and `replay/earnings.py` (#113) can only *estimate* the next report from headlines. Free
calendars do not reach back ten years. EDGAR does: every company furnishes its earnings release on an 8-K with
**Item 2.02**, and EDGAR stamps the filing with its acceptance time, to the second.

## What the stakeholder runs on the Mac

```bash
cd ~/Projects/kaibot && git pull
./scripts/fetch_earnings_dates.sh                  # first run: about 10-15 minutes, about 2,000 SEC requests
./scripts/fetch_earnings_dates.sh --install-daily  # once: the forward-calendar snapshot every night at 01:10
```

| | First run | `--refresh` (monthly is enough) | Daily snapshot |
|---|---|---|---|
| Requests | about 2,000 to SEC (1 per company + its older-filings files + name checks) | about 700 to SEC | 10 to Finnhub |
| Time | about 10-15 min (5 requests/s; SEC allows 10) | about 3 min | seconds |
| Transfer | about 150-400 MB (gzip), including EDGAR's CIK/name list once | about 50-150 MB | under 1 MB |
| On disk | about 10 MB of extracts + the name list (tens of MB; can be deleted after the run) | | about 100 kB a day |

These are estimates: the number of older-filings files per company depends on how much each company files (banks
file thousands of prospectus supplements a year). The run is resumable (Ctrl-C, run again: finished companies are
skipped) and keeps the Mac awake. It needs only `SEC_USER_AGENT` from the `.env` ("Name email@domain", SEC's
fair-access rule); the snapshot uses `FINNHUB_API_KEY`. It may run while the engine runs: the engine does not call
SEC, and the snapshot's 10 Finnhub requests sit far inside the 45/min budget. Do not run it at the same time as
`scripts/fetch_fundamentals.py` or the nightly `collect_alt.py` (each process has its own SEC budget of 5/s).

At the end it prints the mapping result, the row counts, the acceptance-time check and the coverage report. Please
send `data/earnings/build.json`, `coverage.json` and `cik_misses.csv` (a few kB) to the Data team.

## What it writes (`data/earnings/`, not in git)

| File | What |
|---|---|
| `announcements.parquet` | one row per 8-K with Item 2.02 since 1 Jan 2016, for every company in the S&P 500 since 2016 (current and former members): `symbol` (the index ticker in force that day, FB before June 2022), `company` (the company id of `sp500/changes.csv`), `cik`, `accepted_ts` (UTC), `session`, `last_close_before`, `filing_date`, `form`, `items`, `accession`, `report`, `source` |
| `cik_map.csv`, `cik_misses.csv` | company -> CIK, how it was found, whether the CIK's own filings confirm it; the misses with the reason |
| `build.json`, `coverage.json` | counts, requests, errors, the acceptance-time check; the coverage report |
| `sec/submissions/CIK##########.json` | the per-company extract (only what this story needs): the resume state |
| `calendar/snapshot=YYYY-MM-DD.parquet` | the daily forward-calendar snapshots |

- **`session`** is the first regular NYSE session whose 09:30 ET open is after `accepted_ts`: a release at 07:00
  ET belongs to that day, one at 11:00 or at 16:05 to the next session.
- **`last_close_before`** is the last session whose close (16:00, or 13:00 on early-close days) is at or before
  `accepted_ts`. To be out of the market for the release, be flat after that close. For an intraday release
  (rare) this is the day before.
- **`report`** is True for the first 2.02 filing after a gap of at least 30 days: the quarterly report. Other 2.02
  filings within 30 days are follow-ups (corrections, a second release). Pre-announcements months apart count as a
  report, which errs on the safe side.
- Sessions come from NYSE's holiday rules (`ingest/nyse_sessions.py`), including the 2018 and 2025 days of mourning.
  The coverage report compares them with the store's SPY dates when `data/daily` exists.

## Point in time

`kaibot.ingest.earnings_pit.Announcements.next_known_announcement(symbol, as_of)` never uses a filing accepted after
`as_of` or a calendar snapshot taken after `as_of`. It returns, best first:

1. `filed`: a 2.02 filing already accepted whose first tradable session has not opened yet;
2. `scheduled`: the date in the latest forward-calendar snapshot taken by then (from Nov 2026 on);
3. `estimate`: from the company's earlier reports only, the rule of `replay/earnings.py` (a year after the same
   quarter, or 91 days after the last report), now anchored on exact SEC times instead of headline detection.

The test `test_point_in_time_invariance` asks it at ~190 decision times (random, and one second before, at and after
every acceptance and snapshot) and checks that the answer does not change when every later filing and snapshot is
deleted, and that its evidence is never newer than the decision time.

`symbol` may be a company id, a bar-store key (`SYMBOL@ASOF`) or a ticker as it was on the `as_of` day.

## Hand-off to Backend-quant: the exit-before-earnings variant (`src/kaibot/replay/multiday*.py`)

```python
import pandas as pd
from kaibot.ingest.earnings_pit import exact_matrix, report_events
from kaibot.ingest.sp500_pit import Membership
from kaibot.replay.earnings import calendar
table = pd.read_parquet("data/earnings/announcements.parquet")
m = Membership.load()
events = report_events(table, bars.dates, bars.symbols, m)        # {column: SEC report session indexes}
hold, entry = calendar(bars.dates, bars.symbols, events)          # point in time: the default
hold_x, entry_x = exact_matrix(table, bars.dates, bars.symbols, m)  # look-ahead upper bound only
# hold[t, j]: be flat in column j after the close of t; entry[t, j]: open nothing at the close of t
```

- **Use the default (point-in-time) for every go/no-go number.** It is the harness's own `calendar` /
  `blackout` of `replay/earnings.py`, fed with the SEC report sessions instead of headline detections (same
  shapes, same meaning, a one-line change): row t uses only filings accepted before session t opened. The
  `RELIABLE` gate on headline detection is no longer needed for these events; the window can still miss an
  unusually early report, which `coverage.json` measures (`sec_estimate.flat_before_report_share`).
- **`exact_matrix` is a look-ahead comparison only**: flat the night before each real report, no entry in the 5
  sessions before it. Companies usually publish the date a few weeks ahead, so it is close to what a live calendar
  allows, but it is not point-in-time history. Report it as a bound next to the default, never as the result.
- The data package does not import the research code (`test_multiday.py` guards that), which is why `calendar`
  comes from `replay/earnings.py` on the harness side.
- Columns are store keys; `Membership.key_map` gives each company its column, so former members work with the member
  mask of #123 unchanged. Columns that are no company (SPY, sector ETFs) stay all False.
- Order of checks at the close of t: exit held names with `hold[t]`, then skip candidates with `entry[t]`. The
  pre-registration should state the variant (`exit_before_earnings: sec_pit`), the window (10/10/7 days, from
  `replay/earnings.py`) and that the exact variant is reported as a bound.
- Companies without a CIK (`cik_misses.csv`) or without 2.02 filings (`build.json` `mapped_without_rows`) have no
  blackout: treat them like today (no protection), and count the trades in them separately.
- From the snapshots on, the live engine could use `next_known_announcement` with `kind == "scheduled"`; that is a
  live-behaviour change and a separate story.

## Coverage expectations (before the first run)

- **Mapping:** 714 companies. Current members (500) map through today's ticker table. Former members still trading
  under the same ticker (GME, THC, ADT, ...) map the same way, checked against the company's IVV issuer name.
  The others are found by their IVV issuer name in EDGAR's name list and kept only if exactly one match filed during
  the membership spell. 29 former members have no IVV name at all; about half of them still trade. Expect about
  95-98% mapped and 15-30 misses, nearly all companies that left in 2016-2017 (Broadcom Corp,
  Baxalta, Cameron, EMC, Time Warner Cable, Starwood, ...), each with only a few reports inside the window.
- **Rows:** about 4 reports a company-year, so about 25,000-30,000 filings since 2016.
- **Gaps by design:** a few large companies do not furnish their results on an 8-K Item 2.02 (they appear in
  `mapped_without_rows`); a few file under Item 7.01 / 8.01 only. Those need a second source.
- **Against `replay/earnings.py` (#113):** `coverage.json` measures, against SEC truth, the share of reports the
  headline detector finds within one session, the share of its detections that are real reports, and the share of
  reports before which each estimate (headline-anchored and SEC-anchored) had the account flat. The headline part
  needs `data/news` on the same machine.

## Misses and fixes

`cik_misses.csv` lists each company without a CIK and why. To fix one, look it up in EDGAR's company search
(https://www.sec.gov/edgar/search), add a row to `src/kaibot/ingest/earnings/cik_overrides.csv` (company, cik, note
with the source; a blank cik records "checked, no filer"), and run the script again: only the new company is
fetched.

## Forward calendar

The daily snapshot archives Finnhub's free earnings calendar (`/calendar/earnings`, 64 days ahead in 7-day chunks,
date and bmo/amc) with the time it was seen, so the calendar becomes point-in-time from the first snapshot on. Free-plan
access to this endpoint is confirmed by the first run: if Finnhub answers 401/403/429 the job prints "unavailable",
writes nothing and exits 0. If that happens, the fallback is Alpha Vantage's `EARNINGS_CALENDAR` (one CSV request a
day for the whole market, free key, 25 requests a day), which needs a new free key and a new `sources.yaml` entry.
Nasdaq's and Yahoo's calendar pages are not used (unofficial; Yahoo is barred by `CLAUDE.md`).

## Sources and limits

| Source | Used for | Limits |
|---|---|---|
| SEC submissions API (`data.sec.gov/submissions/CIK##########.json` and its older-filings files) | 8-K items and acceptance times | "at least one year's of filing or to 1,000 (whichever is more)" in `recent`, the rest in older files. Fair access: 10 requests/s, declared User-Agent; we use 5/s |
| `www.sec.gov/files/company_tickers.json` | today's ticker -> CIK | today's listed companies only |
| `www.sec.gov/Archives/edgar/cik-lookup-data.txt` | issuer name -> CIK for companies that no longer trade | includes funds and individuals; names normalised the way the IVV cross-check does |
| `sp500/ivv_names.csv` (#123) | the issuer names | only companies held on one of five quarter-end dates |
| Finnhub `/calendar/earnings` | forward dates from Nov 2026 on | free-plan access verified by the first run |

acceptanceDateTime is read as UTC (it ends in `Z`, and the Form 4 collector checked it against live EDGAR on 10 Nov
2026). `build.json` `tz_check` re-checks it on the real data: EDGAR accepts filings only 06:00-22:00 ET and dates an
8-K accepted after 17:30 ET on the next business day, so under the right reading nearly every row is in EDGAR hours
and every filing before 17:30 ET carries the same day's date. If `utc_reading_ok` is false, stop and tell the Data team.
