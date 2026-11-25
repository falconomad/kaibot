# Fixtures for the new-listing watch (story S102, #376)

**Every file here is hand-built. None is a recorded response.** `www.nasdaqtrader.com` is blocked from the sandbox
this story was built in, `www.sec.gov` refuses a client that does not declare a contact, and Alpaca needs keys.
The CIKs, accession numbers, asset ids and every company except Anthropic and SpaceX are invented.

| File | Shape of | How sure the shape is |
|---|---|---|
| `alpaca_assets_night1.json`, `_night2.json`, `_night3.json` | `GET /v2/assets?status=active&asset_class=us_equity` | field names and the `ipo` attribute read in Alpaca's API reference on 19 Nov 2026; no live response seen |
| `alpaca_bars_ANT.json`, `alpaca_bars_OLD.json`, `alpaca_bars_none.json`, `alpaca_bars_SPCX.json` | `GET /v2/stocks/{symbol}/bars` (1Day) | the shape `ingest/daily_bars.py` already reads |
| `master.20261116.idx`, `master.20261118.idx`, `master.20261119.idx` | EDGAR daily index, sorted by CIK | from memory: `CIK\|Company Name\|Form Type\|Date Filed\|File Name`, date as `YYYYMMDD` |
| `master_2026QTR2.idx`, `master_2026QTR4.idx` | EDGAR quarterly full index | from memory: the same columns, date as `YYYY-MM-DD` |
| `CIK0009990001.json` | `data.sec.gov/submissions/CIK##########.json` | the keys of a real response seen on 19 Nov 2026 (`name`, `tickers`, `exchanges`, `sic`, `filings.recent`) |
| `prospectus_ant.htm`, `prospectus_ambiguous.htm` | the cover page of a 424B4 | **not a prospectus**: wording as such covers are usually phrased, from memory |
| `nasdaqlisted.txt`, `otherlisted.txt`, `*_night2.txt` | Nasdaq Trader symbol directory | field names from Nasdaq's "Symbol Look-Up/Directory Data Fields & Definitions" page, read on 19 Nov 2026; the `ETF` and `NextShares` columns of `nasdaqlisted.txt` from memory |
| `nasdaq_dtn.xml` | Nasdaq Trader "Data Technical News" RSS feed | **generic RSS 2.0, not seen** |
| `nasdaq_notice_DTN2026-28.html` | the notice page | title, date and the listing sentence as a summarising tool reported them on 19 Nov 2026; the markup is invented |
| `company_tickers.json` | `www.sec.gov/files/company_tickers.json` | the shape `ingest/earnings_sec.py` already reads |

Facts used for Anthropic (design for M8, section 3.3 and its sources): Nasdaq's notice dated 11 Nov 2026, priced at
$83 on 18 Nov, 980 million shares sold, first session 19 Nov 2026. The dates of its S-1, S-1/A, 8-A12B and 424B4 in
these files are invented.

The parsers have therefore **not been checked against a live response**. The first run on the stakeholder's Mac
does that: each input reports what it could not read and leaves the field empty.
