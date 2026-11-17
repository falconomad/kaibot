# Spin-off share ratios from SEC filings, as a shadow collector (story #298)

Research data only, in shadow: nothing in the live path reads it (a test asserts that). #262 found that the
corporate-actions table lists spin-offs without a distribution value, and #275 does not adopt a record without a
share ratio. This collector reads the ratio from the parent's own 8-K filings and stamps it with the time EDGAR
accepted the filing.

**Status on 17 Nov 2026: built and tested, not yet checked against real filing documents.** The sandbox it was
built in reaches `data.sec.gov` (filing lists) but not `www.sec.gov` (the documents). So the filing lists in the
tests are real, the 8-K documents in the tests are synthetic, and the check against the #262 spin-offs is a script
the Mac runs (below). Until that check has run, the rows are not to be used.

## Source note: where a free spin-off ratio or value can come from

| Source | Carries | Free? Limits | Used here |
|---|---|---|---|
| **SEC EDGAR, the parent's 8-K** (Item 8.01 or 7.01 when the board declares the distribution, Item 2.01 when it is completed; usually with an EX-99 press release) | share ratio, record and distribution date, in prose; acceptance time to the second | Free, no key. Fair access: 10 requests/s with a declared User-Agent. Kaibot's `sec_edgar` budget: 5/s | **Yes** |
| SEC EDGAR, the new company's Form 10-12B and its information statement (EX-99.1) | ratio (often blank until the last amendment), no market value | Free, same rules. Filed under the new company's CIK, which the ticker table has only once it trades; documents of several MB | No (a later step, for spin-offs whose parent 8-K states no ratio) |
| Alpaca `/v1/corporate-actions`, `spin_offs` | `new_rate` / `source_rate`, new symbol, ex-date | Free with the paper key; already in the daily store (`raw` column). No value, and no time at which the ratio became known | Seed and comparison |
| Our own daily store | the value: ratio x the new company's first close (#275's first source) | No request at all. Needs the new company's bars in the store | Comparison only |
| Form 8937 (the issuer's tax-basis statement) | the value split between parent and new company | Free, but published on each company's own website, weeks later, in no fixed format. Not on EDGAR | No: not collectable under one budget |
| EDGAR full-text search (`efts.sec.gov`) | would find spin-offs without a seed | Free, but it is not in `config/sources.yaml`, has no documented limit, and was not reachable from the sandbox | No |
| Yahoo, Reddit, X | | Not allowed by the rules | No |

Checked on 17 Nov 2026: `data.sec.gov/submissions` (reachable, the format the earnings collector already reads).
From memory, not re-checked tonight: what Form 8937 holds and where it is published, and the Form 10-12B habits.
Finnhub, Tiingo and Massive were not checked for a spin-off endpoint on their free plans.

## What the stakeholder runs on the Mac

```bash
cd ~/Projects/kaibot && git pull
./scripts/fetch_spinoffs.sh --check262    # the 15 spin-offs of #262: a few minutes, a few hundred SEC requests
./scripts/fetch_spinoffs.sh               # afterwards: every spin-off in the daily store's corporate-actions table
```

| | `--check262` | All spin-offs of the store | A later run |
|---|---|---|---|
| Requests | 14 filing lists, then the documents of 112 candidate 8-Ks: about 250, at most about 570 | at most about 100 per spin-off | none for a spin-off already read; `--refresh` re-reads the filing lists (1-3 per parent) |
| Time | 1-3 minutes at 5 requests/s | minutes | seconds |

It needs only `SEC_USER_AGENT` from the `.env`. It is resumable (Ctrl-C, run again). It may run while the engine
runs (the engine does not call SEC). Do not run it at the same time as `fetch_earnings_dates.sh`,
`fetch_fundamentals.py` or `collect_alt.py`: each process has its own SEC budget of 5/s.

`--check262` prints, for each spin-off named in #262: found or not, the SEC ratio, Alpaca's record ratio, and
Alpaca's close factor next to the factor the SEC ratio implies. Please send `data/spinoffs/check_262.csv`,
`build.json` and `misses.csv` (a few kB) to the Data team.

## What it writes (`data/spinoffs/`, not in git)

| File | What |
|---|---|
| `spinoffs.parquet` | one row per spin-off whose filings state exactly one ratio (columns below) |
| `misses.csv` | every spin-off without a row, with the reason |
| `build.json` | counts, requests, errors, how many ratios equal Alpaca's record |
| `check_262.csv` | the #262 check (`--check262`) |
| `sec/submissions/CIK##########.json`, `sec/filings/<accession>.json` | the parent's 8-K list and what each 8-K states: the resume state (a few hundred bytes each, not the documents) |

| Column | Meaning |
|---|---|
| `parent`, `new_symbol`, `ex_date` | from the seed (Alpaca's row), not from the filing |
| `ratio`, `new_shares`, `parent_shares` | new shares per parent share: "one for every three" is 1, 3 and 0.3333 |
| `available_ts` | **when EDGAR accepted the earliest filing that states the ratio** (UTC). Never the ex-date |
| `known_before_ex` | `available_ts` is before 09:30 New York on the ex-date |
| `value_per_parent_share`, `value_available_ts` | a dollar value of the distribution per parent share, only when a filing says so in one sentence, with the acceptance time of *that* filing. Expect it to be empty nearly always |
| `filing_url`, `accession`, `form`, `items`, `cik`, `doc`, `quote` | the document and the sentence the ratio was read from |
| `ratio_filings`, `filings_read` | how many filings state this ratio; how many candidate 8-Ks were read |
| `match` | `only_ratio`: the filings state one ratio. `child_name`: they state several (two spin-offs on one day) and the new company's EDGAR name picked this one |
| `alpaca_ratio` | Alpaca's `new_rate / source_rate` for the same spin-off, when the store has it |

## How a row is made

1. Seeds: the `spin_off` rows of the daily store's corporate-actions table (`--events` or `--check262` for a list).
2. Parent ticker to CIK with today's `company_tickers.json` (a parent that no longer trades needs the `cik` column).
3. The parent's 8-Ks accepted from 120 days before the ex-date to 7 days after it, with Item 1.01, 2.01, 7.01 or
   8.01; at most 25, the ones nearest the ex-date.
4. Per 8-K: the primary document; only if it states no ratio and the filing has exhibits, the folder's
   `index.json` and up to three EX-99 documents.
5. Fixed patterns read "N shares of A for every M shares of B" (numbers as digits or words, "(1)" after the word,
   decimals, either order), only next to "distribution", "spin-off" or "separation", and only when A and B are
   different companies. No model reads the filing.
6. A row is written only if the filings state exactly one ratio for the new company. Several, or none: a miss.

## Point in time

`kaibot.ingest.spinoff_sec.known_at(table, as_of)` returns the rows with `available_ts <= as_of` and blanks a value
that a later filing stated. Tests: `test_available_ts_is_the_acceptance_time_never_the_ex_date`,
`test_known_at_is_point_in_time`.

Careful: `available_ts` says when the *ratio* was public. The ex-date in the row is Alpaca's and has no
`available_ts` of its own here.

## What is verified and what is not (17 Nov 2026)

Verified, with real EDGAR filing lists (`tests/fixtures/spinoff/recorded`, from `data.sec.gov`):

| Parent | Ex-date (#262) | Candidate 8-Ks | accepted before the ex-date opened | 8-K with Item 2.01 within 3 days of the ex-date |
|---|---|---|---|---|
| EXC | 2022-02-02 | 12 | 11 | 2022-02-02 12:13Z |
| T | 2022-04-11 | 11 | 8 | 2022-04-11 20:46Z |
| GE | 2023-01-04 | 4 | 4 | 2023-01-04 11:29Z |
| DHR | 2023-10-02 | 5 | 5 | none |
| MMM | 2024-04-01 | 6 | 4 | none (2024-04-04 20:14Z) |
| GE | 2024-04-02 | 3 | 3 | 2024-04-02 10:42Z |
| LEN | 2025-01-21 | 3 | 3 | none |
| HON | 2025-10-30 | 7 | 6 | none |
| DD | 2025-11-03 | 12 | 12 | 2025-11-03 11:30Z |
| CMCSA | 2026-01-05 | 11 | 11 | none |
| BDX | 2026-02-10 | 4 | 3 | none |
| APTV | 2026-04-01 | 11 | 9 | 2026-04-01 21:19Z |
| FDX | 2026-06-01 | 5 | 5 | 2026-06-01 10:42Z |
| HON | 2026-06-29 | 9 | 9 | 2026-06-29 10:41Z |
| SPGI | 2026-07-01 | 9 | 7 | 2026-07-02 01:25Z |

So for all 15 spin-offs the parent filed 8-Ks the collector would read, and each has at least one accepted before
the ex-date opened. **That is all that is verified about the real filings.**

Not verified:
- **Whether those documents state the ratio in words the patterns read.** The test documents are synthetic. The
  number of the 15 that are found, and how the ratios compare with Alpaca's, is what `--check262` prints.
- The comparison with Alpaca's close factor. The repository holds no recorded factors; the nine factors in
  `src/kaibot/ingest/spinoff/check_262.csv` are copied from the text of #262. On the Mac the script reads the
  measured factors from `data/daily/check/adjustment_steps.csv` and the closes from the daily store.
- Expected misses, from how such deals are usually worded: AT&T / WarnerMedia (a Reverse Morris Trust: the ratio is
  WBD shares per AT&T share, computed at closing), BDX (a Reverse Morris Trust with an exchange), and any deal
  that states a fraction in words.

## Limits

- One row per spin-off, from the earliest filing. A ratio that a later filing changes makes the spin-off a miss
  (both ratios are listed), not a second row.
- The value column is nearly always empty. For a value, multiply the ratio by the new company's first close, as
  #275 already does.
- Split-offs (exchange offers) have no fixed ratio per parent share and are not read.
- The first 8-K that states a ratio may be older than the 120-day window; then `available_ts` is later than the
  true first publication (safe for point-in-time use, never earlier than the truth).
- Amendments (8-K/A) and filings by the new company are not read.

## Recommendation for Backend-quant

**Do not use the rows yet.** They are not good enough until `--check262` has run on real filings. After that:

- Use them as a **second, independent check of Alpaca's ratio** (and as the ratio when Alpaca's record has none),
  if the check shows: at least 12 of the 15 found, every found ratio equal to Alpaca's where both exist, the
  implied factor within 0.02 of Alpaca's close factor where the store can price both companies, and no wrong row
  (a miss is acceptable, a wrong ratio is not).
- Read them only through `known_at` (or `available_ts <= decision_ts`), and prefer rows with `known_before_ex`.
- Do **not** take `value_per_parent_share` as the distribution value. Keep #275's order (ratio x the new company's
  first close, then Alpaca's factor).
- Using the rows in the signals or the ledger is a separate two-key story (Backend-quant and QA-Ops), with a
  replay that logs a `trial` row. This story does not do it.
