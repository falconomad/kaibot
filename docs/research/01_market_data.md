# 01 Market Data: free sources for Kaibot (checked 10 Nov 2026)

Scope: quotes, bars, websockets, movers, corporate actions, clock and calendar. Free endpoints only (brief v2).

## Hard truths first
- **Real-time consolidated (SIP/NBBO) data is not free anywhere.** It costs $99/mo at Alpaca and $199/mo at Massive. Free real-time feeds are single-venue (IEX) or do not state their source.
- **IEX is a thin slice of the market.** In Alpaca's example, AAPL traded 923k shares on IEX against 51.9M on SIP (about 1.8%). IEX prices are usable for liquid names, but **IEX volume is not**. Use 15-minute-old SIP history for volume.
- Many "free" tiers are end-of-day only (Massive, FMP, Marketstack, EODHD, MarketData.app).
- Stooq has required an API key since March 2026. IEX Cloud is closed. Polygon.io is now **Massive**.

## Per-source findings

**Alpaca Market Data, Basic (free with a paper account)** USE
- Auth: key ID and secret sent as `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` headers. Sign up for a paper account.
- Limits: 200 historical REST calls/min. Websocket: 30 equity symbols, and normally **one** concurrent connection per endpoint (a second connection gets error 406).
- Delay: real-time **IEX only**. Historical SIP is allowed when `end` is at least 15 minutes old. Latest and snapshot calls on SIP need the paid plan.
- History: since 2016. Bars run from 1Min up to 1Month. `limit` is at most 10,000 data points per request in total (not per symbol), with pagination through `page_token`.
- Endpoints (base `https://data.alpaca.markets`):
  - `/v2/stocks/bars?symbols=AAPL,MSFT&timeframe=1Min&feed=iex`
  - `/v2/stocks/snapshots?symbols=...&feed=iex`
  - `/v2/stocks/trades`, `/v2/stocks/quotes`
  - `/v1beta1/screener/stocks/most-actives`, `/v1beta1/screener/stocks/movers`
  - corporate actions: `/v1/corporate-actions` (older clients use `v1beta1`)
  - websocket: `wss://stream.data.alpaca.markets/v2/iex` (channels: trades, quotes, bars, updatedBars, dailyBars, statuses, lulds), plus a test stream at `/v2/test`
  - clock and calendar: `https://paper-api.alpaca.markets/v2/clock` and `/v2/calendar`
- Terms: your own use, no redistribution. Reliability: high (official API).
- Verdict: **USE as primary.** It has the biggest free budget, multi-symbol calls and a websocket, and covers clock, calendar, corporate actions and movers with one key.

**Finnhub (free)** USE (secondary)
- Auth: free key, sent as `token=` or the `X-Finnhub-Token` header.
- Limits: 60 calls/min, plus a 30 calls/sec burst cap. Websocket: 50 symbols.
- Data: `/api/v1/quote?symbol=AAPL` returns c/h/l/o/pc/t. `wss://ws.finnhub.io?token=` streams US trades. **`/stock/candle` is not free**: Finnhub staff say the free plan "never allowed access". The source and latency of the free US feed are not stated.
- Other endpoints: `/stock/market-status?exchange=US`, `/stock/market-holiday?exchange=US`.
- Terms: the free tier is for personal use. Do not redistribute.
- Verdict: **USE** as a cross-check quote source and for 50 extra streamed symbols. It cannot supply bars.

**Massive (formerly Polygon.io), Stocks Basic** MAYBE
- Free key. 5 calls/min (HTTP 429 above that). End-of-day only: the previous day becomes available after **midnight ET**, and asking for the current day returns 403. About 2 years of history. No websocket. Individual use only.
- Key endpoint: `https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/2026-11-09` returns daily bars for every US stock in **one call**. Splits are at `/v3/reference/splits`. (The `api.polygon.io` host is legacy, and its status should be checked.)
- Verdict: **MAYBE.** Excellent for the nightly universe scan, useless intraday.

**Tiingo (free)** MAYBE
- Free key. 50 requests/hr, 1,000/day, 500 unique symbols/month, 1 GB/month. Internal and individual use only.
- Has IEX intraday from 2017 and an IEX websocket. Free-tier websocket access is unconfirmed.
- Endpoints: `https://api.tiingo.com/iex/?tickers=aapl,msft` and `/tiingo/daily/aapl/prices`.
- Verdict: **MAYBE.** A good third fallback, but the hourly cap rules it out as a primary.

**yfinance (unofficial Yahoo)** MAYBE, offline research only. No key. Rate limits are unpublished and enforced at Yahoo's discretion, with frequent "Too Many Requests" errors (one user was cut off after about 950 of 7,000 tickers). 1-minute bars cover 30 days, 2–90 minute bars 60 days, hourly 730 days. Yahoo's terms bar earning income from its APIs without permission, and Yahoo can cut access at any time.

**Nasdaq public endpoints** (`api.nasdaq.com/api/quote/AAPL/info?assetclass=stocks`, `/api/screener/stocks`) SKIP. An undocumented website backend that needs browser-like headers, so using it is scraping. Terms not verified.

## Summary table

| Source | Free limit | Delay | Intraday / history | WS | Terms | Verdict |
|---|---|---|---|---|---|---|
| Alpaca Basic | 200 req/min | RT IEX; SIP ≥15m old | 1Min+, since 2016 | 30 sym, 1 conn | own use | **USE** primary |
| Finnhub | 60/min, 30/s | not stated | no candles | 50 sym | personal | **USE** fallback |
| Massive Basic | 5/min | EOD (after 00:00 ET) | 2y daily/min | no | individual | **MAYBE** nightly |
| Tiingo | 50/hr, 1k/day, 500 sym/mo | RT IEX | IEX since 2017 | unclear | internal | **MAYBE** 3rd |
| yfinance | unpublished | ~RT (unofficial) | 1m for 30d | no | no income use | **MAYBE** research |
| Twelve Data | 8/min, 800/day | RT (claimed) | n/a | trial only | personal | **SKIP** budget |
| Alpha Vantage | 25/day | RT/15m premium | n/a | no | - | **SKIP** |
| FMP | 250/day | EOD | 5y EOD | no | licence to display | **SKIP** |
| EODHD | 20/day | 15m | 1y EOD | no | - | **SKIP** |
| Marketstack | 100/month | EOD | 1y | no | non-commercial | **SKIP** |
| MarketData.app | 100 credits/day | ≥24h | 1y | no | - | **SKIP** |
| Tradier sandbox | loose | 15m | - | none | - | **SKIP** delayed |
| Stooq | key since Mar 2026 | EOD | daily | no | - | **SKIP** |
| Nasdaq site API | undocumented | ~RT | - | no | scraping | **SKIP** |

Also checked: Databento gives a one-time $125 credit, not a free tier (SKIP).

## Recommended free stack

| Role | Source | What it does |
|---|---|---|
| Primary live | Alpaca IEX websocket | 30 "hot" symbols: trades, quotes, 1-min bars |
| Primary scan | Alpaca REST snapshots and bars (`feed=iex`) | The full 100–300 watchlist every 1–5 min |
| Volume and backfill | Alpaca REST bars (`feed=sip`, `end` ≤ now−15m) | True volume, RVOL baselines, gap and VWAP history |
| Movers | Alpaca screener most-actives and movers | Universe discovery for "loser hunter" |
| Calendar and corporate actions | Alpaca `/v2/clock`, `/v2/calendar`, corporate actions; Finnhub market-status as backup | Session gating, halts, split adjustments |
| Fallback quotes | Finnhub `/quote` plus 50-symbol websocket; Tiingo IEX as a third option | Cross-check and survive an Alpaca outage |
| Nightly universe | Massive grouped daily (1 call after midnight ET) | Full-market daily bars, % change, liquidity filter |
| Research | yfinance (offline only) | Backtest datasets |

**Rate-limit budget for 300 symbols on a 1-minute scan:**
- Snapshots: 300 symbols split into chunks of about 100 makes 3 calls/min.
- 1-min bars since the last poll: 300 symbols × 5 bars = 1,500 points, which fits in 1 call (limit=10,000). Paginate if needed.
- SIP volume refresh every 5 min: about 3 calls.
- Movers, most-actives, clock: about 1 call/min.
- Total: roughly **8–12 calls/min against the 200 limit**, leaving room for retries.
- Finnhub fallback in degraded mode: 60 calls/min covers 300 symbols every 5 minutes (one `/quote` each). Stream the top 50 on its websocket.

**Engineering rules**
1. Run one Alpaca websocket process. Its 30-symbol subscription rotates as the scanner promotes and demotes symbols, and it fans data out internally. A second connection gets error 406.
2. Put a token bucket per provider (Alpaca 180/min, Finnhub 55/min plus 25/s, Massive 5/min), with exponential backoff on 429.
3. Cache history in Postgres, and never re-fetch it.
4. Tag every price with its feed. Never build volume signals from IEX volume.
5. Simulated fills must assume slippage, because IEX top-of-book is not the NBBO.
6. Gate all trading on `/v2/clock` and the calendar's early-close days.

## Sources
- https://docs.alpaca.markets/us/docs/about-market-data-api.md
- https://docs.alpaca.markets/us/v1.1/docs/market-data-faq
- https://docs.alpaca.markets/us/docs/streaming-market-data
- https://docs.alpaca.markets/us/reference/stockbars
- https://alpaca.markets/sdks/python/api_reference/data/stock/screener.html
- https://docs.ccxt.com/docs/exchanges/alpaca/implicit-api
- https://massive.com/pricing
- https://massive.com/knowledge-base/article/on-the-free-basic-plan-when-can-i-pull-the-previous-trading-days-data
- https://massive.com/compare/massive-vs-finnhub.md
- https://massive.com/compare/massive-vs-tiingo.md
- https://massive.com/compare/massive-vs-yfinance
- https://nexusfi.com/d/data-providers/finnhub/
- https://github.com/finnhubio/Finnhub-API/issues/546
- https://matrix-db-api-directory.pages.dev/api/finnhub
- https://www.tiingo.com/products/iex-api
- https://twelvedata.com/pricing.md
- https://www.alphavantage.co/support/api-key
- https://site.financialmodelingprep.com/de/pricing-plans
- https://eodhd.com/pricing
- https://marketstack.com/pricing
- https://www.marketdata.app/docs/account/plans/free-forever/
- https://docs.tradier.com/docs/faq
- https://www.quantconnect.com/docs/v2/cloud-platform/datasets/tradier.md
- https://github.com/pydata/pandas-datareader/issues/1012
- https://qveris.ai/guides/stock-api-free-comparison/data/free-stock-api-comparison-2026-08-03.json

Caveats:
- finnhub.io's own pricing page and docs could not be fetched. Finnhub limits come from secondary sources plus Finnhub staff replies on GitHub.
- Alpaca screener access on the Basic plan and the current corporate-actions path (v1 vs v1beta1) should be confirmed with a live key.
