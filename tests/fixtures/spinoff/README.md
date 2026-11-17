# Fixtures for the spin-off ratio collector (story #298)

Two kinds. Do not mix them up.

## `recorded/` — real, from EDGAR

`CIK##########.json`: the filing lists of the 13 parents named in issue #262, downloaded from
`https://data.sec.gov/submissions/CIK##########.json` on 17 Nov 2026 (13 requests, plus AT&T's older-filings file
`CIK0000732717-submissions-001.json`; about one request a second). Trimmed: only the 8-K rows filed from 130 days
before to 10 days after each #262 ex-date, only the columns the collector reads (`accessionNumber`, `filingDate`,
`acceptanceDateTime`, `form`, `items`, `primaryDocument`), `formerNames` emptied, and the rows of AT&T's older file
merged into `filings.recent` with `filings.files` emptied. The values of the rows are unchanged.

## `synthetic/` — NOT SEC filings

Fictional companies (Zeta Parent, Quill Holdings, Xylo Industries, Mute Materials; tickers ZZ*, CIKs 9001-9008,
accession numbers starting 9999999). The files have the shape of EDGAR's `company_tickers.json`, submissions
files, filing-folder `index.json` and 8-K documents, and every document carries a comment that says so.

They exist because `www.sec.gov` (where the filing documents live) answered 403 to this sandbox on 17 Nov 2026,
so no real 8-K document could be recorded. The wording of the ratio sentences follows how such filings are usually
phrased, from memory. **The patterns have therefore not been checked against a real filing document yet**: that is
the job of `./scripts/fetch_spinoffs.sh --check262` on a machine that reaches `www.sec.gov`.
