# 02 — Fundamentals, Filings and Event Calendars (free sources)

Checked 10 Nov 2026. "Verified" means the official page was read today; "secondary" means only a third-party write-up was readable (the Finnhub pricing page and docs render with JS and could not be read).

The loser hunter must answer within seconds: is the drop company-specific (8-K, earnings, insider sale, downgrade in the last 24 h), and are the fundamentals still strong (cached score below)?

## Summary table

| Source | Auth | Free limit | Freshness | Verdict |
|---|---|---|---|---|
| SEC EDGAR submissions API | None, User-Agent required | 10 req/s | Real-time, usually <1 s | **USE** |
| SEC XBRL companyfacts/frames | None, User-Agent | 10 req/s | Usually <1 min; bulk ZIP nightly ~3am ET | **USE** |
| EDGAR Atom feeds (getcurrent) | None, User-Agent | 10 req/s | Updates through the day | **USE** |
| EDGAR full-text search (efts) | None, User-Agent | 10 req/s (unofficial endpoint) | Near real-time | MAYBE |
| SEC 13F / Form 3-4-5 data sets | None | Bulk ZIP | Quarterly | MAYBE |
| Finnhub free | API key | 60/min, 30/s burst | Real-time to daily | **USE** |
| Alpha Vantage | API key | 25/day | Daily | MAYBE |
| FMP Basic | API key | 250/day, 500 MB per 30 days | EOD | MAYBE |
| FINRA Reg SHO daily short volume | None (CDN file) | Unlimited file pulls | Same day by 6pm ET | **USE** |
| FINRA equity short interest | None (files) or API key | Free files | Twice a month, ~7 business-day lag | MAYBE |
| Cboe VIX history CSV | None | n/a | Daily | **USE** |
| Cboe daily put/call page | None | HTML only | Daily | MAYBE |
| Alpaca corporate actions | Alpaca key | 200/min (free plan) | Daily | **USE** |
| OpenFIGI | Optional key | 25/min (no key), 250/min (key) | Static | MAYBE |
| Nasdaq calendar JSON (unofficial) | None | Unknown | Daily | MAYBE |
| yfinance options/IV (unofficial) | None | Unofficial throttling | ~15-min delay | MAYBE |

## Source notes

**SEC EDGAR (data.sec.gov)** — USE. No key is needed. The SEC's fair-access limit is 10 req/s, and every request must send a User-Agent that names you and gives a contact email. Crawling bots are "managed" (in practice a temporary 403 block). CORS is not supported, so calls must go server-side.
- `https://data.sec.gov/submissions/CIK0000320193.json` returns recent filings, form types and accept times. It is real-time, typically under a second.
- `https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json` returns every XBRL fact (revenue, net income, debt and so on). It usually updates within a minute.
- `https://data.sec.gov/api/xbrl/frames/us-gaap/Revenues/USD/CY2025Q3.json` returns one concept for all companies, which suits cross-sectional ranking.
- `companyfacts.zip` and `submissions.zip` are rebuilt nightly at about 3:00am ET.
- Ticker to CIK map: `https://www.sec.gov/files/company_tickers.json`.

**EDGAR Atom feeds** — USE. These are the free "new filing" firehose.
- `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=100&output=atom`, and the same with `type=4` for insider trades.
- Per company: `...action=getcompany&CIK=AAPL&type=8-K&count=40&output=atom`.
- The `type` filter matches by prefix, so `8-K` also returns 8-K/A.
- Poll every 60–120 s and parse Form 4 XML for transaction code P (open-market buy) and S (open-market sale).

**EDGAR full-text search** — MAYBE. `https://efts.sec.gov/LATEST/search-index?q="guidance"&forms=8-K&startdt=2026-11-09&enddt=2026-11-10` covers filings from 2001 onward. The endpoint is used by sec.gov itself but is not documented. Pagination fails past offset 10,000, and an IP that exceeds the limit is blocked for about 10 minutes.

**SEC bulk data sets** — MAYBE. 13F and insider-transaction data sets are quarterly ZIPs: ownership trend and backfill only.

**Finnhub free** — USE (secondary). The limit is 60 calls/min with a 30/s burst cap; going over returns HTTP 429. A Feb-2026 third-party audit reports the following:
- Free: `/stock/metric?metric=all` (basic financials: margins, ROE, P/E, 52-week data), `/calendar/earnings` (about 1 month), `/stock/earnings` (last 4 surprises), `/stock/insider-transactions`, `/stock/insider-sentiment`.
- Probably free: `/stock/recommendation`.
- Premium: price targets, financials-as-reported, dividends, splits, and probably ownership and the IPO calendar.
- Base URL is `https://finnhub.io/api/v1/...&token=KEY`.
- Action: confirm each endpoint on day 1 by checking for a 403.

**Alpha Vantage** — MAYBE. 25 requests/day; personal, non-commercial license. Free: `OVERVIEW`, statements, `EARNINGS`, `EARNINGS_CALENDAR` (one call returns a CSV of all upcoming reports), `IPO_CALENDAR`, `INSIDER_TRANSACTIONS`, `HISTORICAL_PUT_CALL_RATIO`. Premium: `REALTIME_OPTIONS`, `HISTORICAL_OPTIONS`. Use for 1–2 calendar pulls a day.

**Financial Modeling Prep Basic** — MAYBE. The free plan gives 250 calls/day, 500 MB of bandwidth per trailing 30 days, 5 years of history and end-of-day data only. Annual statements are included for US companies. The earnings calendar is paid. Displaying or redistributing the data needs a separate license. This is a fallback for ratios when XBRL tags are messy.

**FINRA** — USE / MAYBE.
- Reg SHO daily short volume: `https://cdn.finra.org/equity/regsho/daily/CNMSshvol20261109.txt`, posted by 6:00pm ET same day, occasionally re-posted as "Updated". Off-exchange (TRF/ADF/ORF) volume only.
- Short interest: twice a month, published 7 business days after settlement. A slow factor.
- The Query API's Public credential is $0, but coverage of the short datasets is unclear. Use the CDN files.

**Cboe** — USE for VIX. `https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv` is updated daily (VIX9D, VVIX, OVX also available). Put/call ratios (total, index, equity) are HTML only on the daily statistics page; scrape once at end of day.

**Alpaca** — USE. The free plan allows 200 calls/min and includes corporate actions: `https://data.alpaca.markets/v1/corporate-actions?symbols=AAPL&types=cash_dividend,forward_split`. It covers dividends, splits, mergers and spin-offs. Options data on this plan is indicative only.

**OpenFIGI** — MAYBE. `POST https://api.openfigi.com/v3/mapping`: 25 req/min without a key, 250/min with one. Only for identifier edge cases; the SEC ticker file covers most.

**Nasdaq calendar (unofficial)** — MAYBE. `api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD` is used by community wrappers (finance_calendars). Not verified today; it needs a browser-like User-Agent, has no terms and can break. Cross-check earnings dates only.

**yfinance (unofficial)** — MAYBE. `Ticker.option_chain()` returns per-strike `impliedVolatility`. It scrapes Yahoo (personal use only), throttles without warning and breaks often. Use it only for a daily ATM-IV snapshot, behind a circuit breaker.

**Not used:** EODHD free (20 calls/day). IEX Cloud (shut down). Polygon/Massive free financials not checked.

## Recommended free stack
1. **Events:** poll EDGAR Atom (8-K, 4, SC 13D/G) every 60 s; for a dropping ticker call `submissions` (<1 s) for filings in the last 24 h; once a day pull the Finnhub earnings calendar and the Alpha Vantage `EARNINGS_CALENDAR` CSV.
2. **Fundamentals:** nightly `companyfacts.zip` scored locally (zero intraday calls), plus Finnhub `/stock/metric` for 52-week range, beta and ratios.
3. **Analyst/insider:** Finnhub `/stock/recommendation`, `/stock/insider-sentiment`, `/stock/earnings`, cached daily (300 tickers at 60/min is about 5 min per endpoint).
4. **Context:** daily FINRA CNMS short-volume ratio, short interest twice a month, Cboe VIX CSV and put/call.
5. **Corporate actions:** Alpaca, so splits and dividends do not look like crashes.
6. **Throttling:** one shared SEC client, 8 req/s token bucket, User-Agent `Kaibot research <contact email>`.

## Fast "fundamental quality score" (0–100)
Compute nightly from XBRL (trailing twelve months, or latest 10-K/10-Q). Rank each metric as a percentile within its sector (SIC code from `submissions`), then take a weighted average.

| Factor (weight) | Metric from XBRL tags |
|---|---|
| Profitability (25%) | Gross margin, operating margin, ROE (`NetIncomeLoss / StockholdersEquity`) |
| Growth (20%) | Revenue YoY, EPS YoY |
| Balance sheet (20%) | Debt/equity, current ratio, cash/total assets |
| Cash quality (15%) | Operating cash flow / net income, free-cash-flow margin |
| Earnings momentum (10%) | Finnhub surprise %, last 4 quarters |
| Sentiment (10%) | Recommendation trend (buy share), insider MSPR, penalty for net insider selling over 90 days |

**Loser-hunter gate.** A dropping stock is a rebound candidate only if the score is ≥ 70, there is no 8-K, earnings or downgrade in the last 24 h, the beta-adjusted residual versus SPY or the sector ETF is under about 1.5σ, and the short-volume ratio has not spiked. A large residual plus a filing means company-specific: skip.

## Sources
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC Accessing EDGAR Data (fair access)](https://www.sec.gov/os/accessing-edgar-data)
- [SEC Form 13F data sets](https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets)
- [SEC Insider Transactions data sets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets)
- [EDGAR RSS feed patterns (Feeder)](https://feeder.co/knowledge-base/rss-feed-creation/sec-edgar-rss-feeds/)
- [EDGAR full-text search API write-up](https://orthogonal.info/sec-edgar-full-text-search-api-efts-python/)
- [Finnhub pricing](https://api.finnhub.io/pricing) (JS page, could not be read)
- [Finnhub free tier capabilities 2026 (secondary)](https://github.com/RaquazaCode/Fintech-AI-SaaS-Using-Google-Antigravity-Skills-Stitch/blob/main/Finnhub%20API%20Free%20Tier%20Capabilities%20(2026).docx.md)
- [Alpha Vantage support (limits)](https://www.alphavantage.co/support/), [documentation](https://www.alphavantage.co/documentation/), [terms](https://www.alphavantage.co/terms_of_service/)
- [FMP pricing](https://site.financialmodelingprep.com/pricing-plans), [FMP FAQ](https://site.financialmodelingprep.com/faqs)
- [FINRA daily short sale volume files](https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files), [equity short interest](https://www.finra.org/finra-data/browse-catalog/equity-short-interest), [FINRA API fees](https://developer.finra.org/fees)
- [Cboe VIX historical data](https://www.cboe.com/tradable-products/vix/vix-historical-data), [Cboe daily options statistics](https://www.cboe.com/markets/us/options/market-statistics/daily/)
- [Alpaca market data plans](https://alpaca.markets/data), [Alpaca corporate actions](https://docs.alpaca.markets/us/reference/corporateactions-1)
- [OpenFIGI API documentation](https://www.openfigi.com/api/documentation)
- [finance_calendars (Nasdaq wrapper)](https://www.github.com/s-kerin/finance_calendars)
