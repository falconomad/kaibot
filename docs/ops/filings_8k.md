# Every 8-K filing with its items, point-in-time (story S100, #374)

Research data only. The live engine does not read any of it, and nothing here changes an order, a rule or a setting.

`data/earnings/announcements.parquet` (S85, `docs/ops/earnings_dates.md`) keeps only the 8-K filings with Item 2.02,
the earnings release. The same EDGAR files list **every** 8-K of a company with the items it was filed under and its
acceptance time to the second. This store keeps them all, so that filing types can be tested (S101) and used as
deterministic event flags (design for M8, section 2.3).

## What the stakeholder runs on the Mac

```bash
cd ~/Projects/kaibot && git pull
./scripts/fetch_earnings_dates.sh --all-items      # about 10-15 minutes, about 2,000 SEC requests; resumable
cat data/filings/coverage.md                       # the counts per item and year: paste them into issue #374
```

| | First run | `--all-items --refresh` (monthly is enough) | `--all-items --build`, `--all-items --status` |
|---|---|---|---|
| Requests to SEC | about 2,000 (1 per company + its older-filings files) | about 700 | none |
| Time | about 10-15 min at 5 requests a second (SEC allows 10) | about 3 min | seconds |
| When | **outside 15:30-22:00 Berlin** | **outside 15:30-22:00 Berlin** | any time: they read local files only |

- It needs only `SEC_USER_AGENT` from the `.env` ("Name email@domain", SEC's fair-access rule). The wrapper reads it
  at run time from `~/Projects/kaibot-secrets/.env` (or `KAIBOT_ENV_FILE`) and prints no key.
- Resumable: stop it with Ctrl-C and run it again. A company whose extract is already on disk is not asked for again;
  a company that failed is listed at the end and read again by the next run.
- Do not run it at the same time as the plain `./scripts/fetch_earnings_dates.sh`, `scripts/fetch_fundamentals.py`,
  `./scripts/fetch_spinoffs.sh` or the nightly `collect_alt.py` (00:45 Berlin): each process has its own SEC budget
  of 5 requests a second, and two of them together reach SEC's limit.
- The engine may stay on: it does not call SEC.
- The times and request counts are the estimates of S85 for the same files. The first real run prints its own.

Run the plain `./scripts/fetch_earnings_dates.sh` first if it has never run on this Mac: `--all-items` then reuses
its company list (`data/earnings/cik_map.csv`) and both tables cover the same companies. Without it `--all-items`
works out the company list itself (the same method; it also downloads EDGAR's name list once) and skips the
comparison below.

## What it writes (`data/filings/`, not in git)

| File | What |
|---|---|
| `filings_8k.parquet` | one row per 8-K and 8-K/A since 1 Jan 2016, for every company in the S&P 500 since 2016 (current and former members) |
| `coverage.md`, `coverage.json` | filings per year, per form, per item, per item and year; mapped companies with no 8-K at all; the share accepted outside regular hours; the acceptance-time check; the comparison with `announcements.parquet` |
| `build.json` | counts, requests, errors of the last run |
| `cik_map.csv` | the company -> CIK map this table was built with |
| `sec/submissions/CIK##########.json` | the per-company extract: the resume state of this store |

Nothing under `data/earnings/` is written. `announcements.parquet` stays byte-identical: the S85 extract and its
cache are untouched, and the all-items extract has a cache of its own.

Columns of `filings_8k.parquet`:

| Column | Meaning |
|---|---|
| `symbol` | the index ticker in force on the day of acceptance (FB before June 2022) |
| `company` | the company id of `sp500/changes.csv` |
| `cik` | the SEC filer id, 10 digits |
| `accepted_ts` | EDGAR's acceptance time, UTC, to the second. In research this is the row's `available_ts` |
| `session` | the first regular NYSE session whose 09:30 ET open is after `accepted_ts` |
| `last_close_before` | the last session whose close is at or before `accepted_ts` |
| `filing_date` | EDGAR's filing date (the acceptance-time check needs it) |
| `form` | `8-K` or `8-K/A` |
| `items` | the items exactly as filed, comma-separated (`2.02,9.01`); empty for the few filings without any |
| `accession` | EDGAR's accession number |

Rows are kept as filed: nothing is merged, and an amendment (8-K/A) is its own row.

## Point in time

```python
from datetime import UTC, datetime
from kaibot.ingest.filings_pit import Filings

f = Filings.load()                                               # data/filings/filings_8k.parquet
f.filings("META", datetime(2024, 2, 1, 21, 30, tzinfo=UTC))      # every 8-K and 8-K/A accepted by then
f.filings("META", as_of, items=["4.01", "4.02"])                 # those that list at least one of these items
f.filings("META", as_of, forms=["8-K"])                          # without amendments
```

`filings(symbol, as_of)` never returns a filing accepted after `as_of`. `symbol` may be a company id, a bar-store key
(`SYMBOL@ASOF`) or a ticker as it was on the `as_of` day, as in `earnings_pit.Announcements`. The test
`test_point_in_time_invariance` (in `tests/unit/test_filings_8k.py`) asks it at about 190 decision times (random,
and one second before, at and after every acceptance) and checks that the answer does not change when every later
filing is deleted.

A live rule could not use `accepted_ts` as it is: it would need a poller with its own storage time. That is not built
here (design for M8, section 2: built only for a rule that has passed).

## How to read the report

- **`item_202_vs_announcements`**: the 8-K rows with Item 2.02 against `data/earnings/announcements.parquet`, by
  accession number. The two stores are fetched on different days, so `equal_up_to_cutoff` compares the filings
  accepted up to the newest one in `announcements.parquet`; later ones are new, not a difference. It should be `true`
  with `n_only_in_announcements` 0 and `value_mismatches` 0. To make the two sets exactly equal (`equal: true`), run
  `./scripts/fetch_earnings_dates.sh --refresh` and `--all-items --refresh` one after the other.
- **`tz_check`**: the acceptance-time check of S85 on the new table (EDGAR accepts filings 06:00-22:00 ET and dates
  an 8-K accepted after 17:30 ET on the next business day). If `utc_reading_ok` is false, stop and tell the Data team.
- **`outside_regular_hours_share`**: the share of filings accepted when no regular session was open (before 09:30 ET,
  after the close, 13:00 ET on early-close days, weekends and holidays).
- **`companies_with_none`**: companies with a CIK and not a single 8-K since 2016. Expect none or very few; a name
  here usually means its extract failed (see `build.json` `errors`) and the next run reads it.
- A filing counts once under each of its items, so the item counts add up to more than the filings.

## Sources and limits

The same as `docs/ops/earnings_dates.md`: the SEC submissions API (`data.sec.gov/submissions/CIK##########.json`
and its older-filings files), the shared `sec_edgar` budget of 5 requests a second (`config/sources.yaml`, entry
`sec_filings_8k`), and the declared `SEC_USER_AGENT`.

Not checked against live EDGAR in this story (the tests use hand-built files in EDGAR's shape):

- how many 8-K filings carry an empty `items` field, and whether old amendments list their items the same way;
- the real request count and run time of `--all-items`;
- the row count. S85 expected 25,000-30,000 Item 2.02 filings; all items together should be several times that.
