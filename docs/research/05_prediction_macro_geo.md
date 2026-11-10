# 05 · Prediction markets, macro and geopolitical data (verified 10 Nov 2026)

Free, non-company data that moves US stocks intraday. Facts come from the docs as read on 10 Nov 2026. No endpoint was tested live because the sandbox proxy blocked direct calls, so smoke-test each one first.

## Summary table

| Source | Auth | Free limits | Freshness | Verdict |
|---|---|---|---|---|
| Polymarket Gamma/CLOB/Data + WS | None for reads | Gamma 4,000/10s (`/markets` 300); CLOB `/book` 1,500/10s; Data 1,000/10s; throttled, not rejected | Real-time (WS) | **USE** |
| Kalshi market data | None for public reads | Basic tier 200 read tokens/s | Real-time | **USE** |
| Manifold / PredictIt / Metaculus | none / none / token | 500/min; about 1/min, non-commercial; n/a | varies | SKIP |
| FRED / ALFRED | Free API key | about 120 req/min/key *(third-party report)* | Daily or at release | **USE** |
| BLS API v2 | Free key (renew yearly) | 500 queries/day, 50 series/query, 50 req/10s | At release (8:30 ET) | **USE** |
| BEA API | Free UserID | 100 req, 100 MB, 30 errors per min | At release | MAYBE |
| Treasury Fiscal Data | None | No fixed limit; 429 means back off | Daily | MAYBE |
| Treasury yield-curve XML | None | none stated | Daily (end of day) | **USE** |
| EIA API v2 | Free key | under about 9,000 req/h, under 5 req/s | Weekly (Wed 10:30 ET) | **USE** |
| Fed RSS feeds | None | none | Minutes | **USE** |
| Federal Register API | None | "polite" cap; 1,000 results per page | Daily + public inspection | **USE** |
| White House presidential-actions RSS | None | none | Minutes to hours | **USE** |
| OFAC recent actions / delta file | None | none | At action | MAYBE |
| GDELT 2.0 (Events/GKG/DOC) | None | none published | 15 min | **USE** (filtered) |
| ACLED | Account | Open tier: no API | Weekly | SKIP |
| ReliefWeb | Approved appname | not published | Hours | SKIP |
| Cboe VIX/VIX9D/VVIX CSV | None | none | Daily | **USE** |
| FedWatch / Finnhub calendar / Trading Economics | Paid | from $25/month; $50/month; paid plans | n/a | SKIP |
| CNN Fear & Greed | Unofficial scrape | none, may break | Intraday | MAYBE (display only) |

## Prediction markets

**Polymarket** (**USE**). It is the deepest real-money source for macro, political and geopolitical odds.
- **Base URLs.** Gamma `https://gamma-api.polymarket.com` (discovery), CLOB `https://clob.polymarket.com` (book and prices), Data `https://data-api.polymarket.com` (trades). Limits are per IP.
- **Websocket.** The market channel `wss://ws-subscriptions-clob.polymarket.com/ws/market` needs no auth. Send `PING` every 10 s.
- **Examples.** `/events?active=true&closed=false&tag_slug=economy`, `clob…/prices-history?market=<token_id>&interval=1h`.
- **Terms.** The geoblock covers **order placement only**. The US is close-only for trading, and the docs say nothing about reading data. Read the ToS before showing raw odds on a public dashboard.

**Kalshi** (**USE**).
- **Base URL.** The docs give `https://external-api.kalshi.com/trade-api/v2`. Older clients use `api.elections.kalshi.com`, so smoke-test both.
- **Public endpoints.** `/markets?series_ticker=…&status=open`, `/events/{ticker}`, `/markets/{ticker}/orderbook`. Basic tier: 200 read tokens/s.
- **Why it matters.** Its CPI, payrolls and Fed-decision bracket markets are the only **free stand-in for consensus forecasts**.
- **Terms.** Check the Developer Agreement before redistributing data.

**Manifold, PredictIt, Metaculus: SKIP.** Play money, non-commercial use only, or too slow.

## Macro

- **FRED/ALFRED** (**USE**).
  - Endpoint: `https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key=…&file_type=json`.
  - ALFRED vintages use `realtime_start`/`realtime_end`, so backtests only see data that existed at the time. Release calendar: `/fred/releases/dates`.
  - Terms: show the "uses the FRED® API but is not endorsed…" notice. Series marked "Copyright" (ICE BofA spreads) carry extra limits.
  - Useful series: DGS2, DGS10, T10Y2Y, DTWEXBGS, BAMLH0A0HYM2, DCOILWTICO.
- **BLS v2** (**USE**). `POST https://api.bls.gov/publicAPI/v2/timeseries/data/` with series `CUUR0000SA0` (CPI) and `CES0000000001` (payrolls). Gives actual values at 8:30 ET.
- **BEA** (**MAYBE**). FRED already mirrors GDP and PCE.
- **Treasury.**
  - Yield-curve XML (`…/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=202611`): **USE**, end of day.
  - Fiscal Data: **MAYBE**, slow-moving.
- **EIA v2** (**USE**).
  - Endpoint: `https://api.eia.gov/v2/petroleum/stoc/wstk/data/?api_key=…&frequency=weekly&data[0]=value&facets[series][]=WCESTUS1`.
  - The weekly petroleum report normally comes out Wed 10:30 ET. This week it moves to **Thu 12 Nov, 12:00 ET** (Veterans Day).
- **Economic calendar.** There is no free consensus feed: FedWatch, Finnhub and Trading Economics are all paid. **Build our own calendar** from FRED release dates, the BLS/BEA schedules and the FOMC dates, and use Kalshi brackets as the expected value.

## Geopolitics and policy

- **GDELT 2.0** (**USE**, filtered).
  - `http://data.gdeltproject.org/gdeltv2/lastupdate.txt` lists the newest Events/GKG files every 15 min.
  - The DOC API (`https://api.gdeltproject.org/api/v2/doc/doc?query=tariff&mode=timelinevol&format=json`) covers 3 months, max 250 records.
  - Use it only to spot volume and tone spikes on whitelisted themes, because the raw feed is noisy.
- **Federal Register API** (**USE**). `https://www.federalregister.gov/api/v1/documents.json?conditions[type][]=PRESDOCU`, no key. Tariff proclamations and executive orders appear here.
- **White House and Fed feeds** (**USE**, poll every 60 s).
  - White House: `https://www.whitehouse.gov/presidential-actions/feed/`. A feed directory showed it active on 5 Nov 2026.
  - Fed: `press_monetary.xml`, `press_all.xml`, `speeches.xml`.
- **OFAC** (**MAYBE**). I found no RSS feed; options are scraping recent actions or using the delta file. Sanctions rarely move large caps. USTR: no RSS feed confirmed, so cover it through the Federal Register.
- **ACLED** (**SKIP**). A free Open account has no API and the terms flag monetised use.
- **ReliefWeb** (**SKIP**). It needs a pre-approved appname and is humanitarian-focused.

## Market regime

- **Cboe CSVs** (**USE**, daily, no auth): `https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`, plus `VIX9D_` and `VVIX_History.csv`.
- **Intraday volatility.** The delayed-quotes page has no API. Use VIXY and SPY/sector ETF bars from the market-data feed.
- **Sector ETFs** (**USE**): XLK, XLE, XLF, XLI, XLU, XLY, XLP, SMH, ITA, JETS, KRE.
- **CNN Fear & Greed** (**MAYBE**). Unofficial scrape, so dashboard display only, never an engine input.

## How these become engine features

**1. Event-risk signal.**
- Stream a curated set of 30–80 Polymarket/Kalshi markets: Fed, CPI, tariffs, shutdowns, wars, oil.
- Compute Δp over 5, 15 and 60 min, and a z-score against each market's own 30-day volatility. Skip illiquid or wide-spread markets.
- A move of |Δp| ≥ 10 points with z ≥ 3 emits `EventShock{market, direction, magnitude, ts}` to the agent room.
- CPI surprise = actual BLS print minus the Kalshi-implied value.

**2. Event → sector/ticker map.** A versioned YAML file maps each event tag to affected ETFs and tickers, with signs:
- China tariff up → SMH, AAPL, XLI down
- Middle East escalation up → XLE, ITA up; JETS down
- Fed cut odds up → KRE, IWM up

The loser-hunter buys names that fell *because of* a mapped shock while their fundamentals are intact. It skips drops with no mapped external cause, which may be company-specific.

**3. Calendar gate.** No new entries from 5 min before to 15 min after a CPI, payrolls or PCE release (8:30), an FOMC statement (14:00) or the EIA report.

**4. Regime classifier (switches the loser-hunter).**
- Daily inputs: VIX level and percentile, VIX9D/VIX, VVIX, high-yield spread change, 10-year yield change, dollar index change.
- Intraday inputs: SPY versus VWAP, the defensive/cyclical ETF ratio (XLU+XLP versus XLY+XLK), VIXY.
- Deterministic thresholds produce one of four states:
  - **RISK_ON / NEUTRAL**: loser-hunter **ON**.
  - **RISK_OFF**: ON at half size, only for mapped shocks whose Δp is already reversing.
  - **CRISIS** (VIX above 30 and rising, VIX9D/VIX above 1.1, spreads widening) or a pending scheduled event: **OFF**.
- This is Python logic like the risk officer: agents can explain the regime but not override it.

## Sources
- Polymarket rate limits: https://docs.polymarket.com/api-reference/rate-limits.md
- Polymarket API intro: https://docs.polymarket.com/api-reference/introduction.md
- Polymarket websocket: https://docs.polymarket.com/market-data/websocket/overview.md
- Polymarket geoblock: https://docs.polymarket.com/api-reference/geoblock.md
- Kalshi quick start (market data): https://docs.kalshi.com/getting_started/quick_start_market_data
- Kalshi rate limits: https://docs.kalshi.com/getting_started/rate_limits
- Manifold/PredictIt/Metaculus overview: https://newyorkcityservers.com/blog/best-prediction-market-apis
- Metaculus auth: https://www.alphascope.app/blog/metaculus-api
- FRED terms: https://fred.stlouisfed.org/docs/api/terms_of_use.html
- FRED rate limit (third-party profile): https://apis.io/rate-limits/fred/fred-rate-limits/
- BLS API FAQ: https://www.bls.gov/developers/api_faqs.htm
- BEA limits: https://ua-libraries-research-data-services.github.io/UALIB_ScholarlyAPI_Cookbook/overview/bea.html
- Treasury yield XML: https://home.treasury.gov/treasury-daily-interest-rate-xml-feed
- Fiscal Data API basics: https://cdn.jsdelivr.net/npm/@saibolla/ada@0.1.3/skills/usfiscaldata/references/api-basics.md
- EIA schedule: https://www.eia.gov/petroleum/supply/weekly/schedule.php
- EIA API limits: https://www.oilpriceapi.com/eia-api
- CME FedWatch API: https://www.cmegroup.com/market-data/market-data-api/fedwatch-api.html
- Finnhub calendar pricing: https://www.financecalendar.com/?p=2927
- Trading Economics limits: https://docs.tradingeconomics.com/get_started/rate-limits
- GDELT sources: https://rbozydar.github.io/py-gdelt/getting-started/data-sources
- ACLED access: https://acleddata.com/myacled-faqs
- ReliefWeb API: https://apidoc.reliefweb.int/
- OFAC updates: https://ofac.treasury.gov/faqs/90
- Fed RSS: https://federalreserve.gov/feeds
- Federal Register API: https://dev.to/ronin13/us-regulations-have-a-free-json-api-every-rule-notice-and-executive-order-without-scraping-4o34
- White House feed: https://feeder.co/discover/8d39eac927/whitehouse-gov-presidential-actions
- Cboe VIX history: https://www.cboe.com/tradable-products/vix/vix-historical-data/
- Cboe term structure: https://www.cboe.com/en/tradable-products/vix/term-structure/
- CNN F&G scraper: https://pypi.org/project/fear-greed-index
