Fixtures of tests/test_headline_feed.py (the dashboard's headline line).

- `submissions_cik0000320193.json`: the shape of `data.sec.gov/submissions/CIK##########.json` (the same columns
  as `tests/fixtures/sec_submissions_aapl.json`, which was recorded on 9 Nov 2026), with made-up 8-K rows that
  cover the item codes the line renders, an 8-K/A, an old filing, a 10-Q and malformed rows. No live call in tests.
- The recorded `tests/fixtures/sec_submissions_aapl.json` and `tests/fixtures/sec_company_tickers.json` are used as
  they are.
