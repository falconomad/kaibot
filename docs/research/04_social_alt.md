# 04 · Social and alternative attention data (verified 10 Nov 2026)

Limits come from official pages fetched today unless marked *(unverified)*. Live probes were blocked by the sandbox proxy, so no endpoint was tested directly.

## Summary table

| Source | Auth | Free limit | Latency | Ticker extraction | Verdict |
|---|---|---|---|---|---|
| ApeWisdom | none stated | not stated (be polite: 1 call per 15 min per filter) | ~30 min (scans twice an hour) | done for you | **USE** |
| Tradestie WSB API | none, or a free key | 20 req/min anon, 200 req/min with key | 15 min | done for you (top 50 + sentiment) | **USE** |
| Wikipedia pageviews | User-Agent with contact | no hard cap; ~100 ms pacing | daily API (next day); hourly dumps *(unverified lag ~1–2 h)* | map ticker to article once | **USE** (slow confirmer) |
| Bluesky Jetstream | none in official examples | not stated | real time | hard (cashtag regex, sparse) | **MAYBE** |
| Hacker News (Firebase + Algolia) | none | "no rate limit" (Firebase) | real time | hard; tech names only | **MAYBE** |
| Google Trends API (alpha) | application required | n/a | daily granularity | query = name/ticker | **MAYBE** (apply) |
| 4chan /biz/ JSON | none | 1 req/s, threads ≥10 s | real time | hard, very noisy | **SKIP** (ApeWisdom covers it) |
| Reddit Data API | OAuth, **pre-approval since Nov 2025** | 100 QPM OAuth / 10 QPM anon | real time | DIY | **SKIP** (use via aggregators) |
| X / Twitter API | paid credits | **no free read tier** | real time | DIY | **SKIP** |
| StockTwits API | closed | **new registrations closed** | n/a | n/a | **SKIP** |
| pytrends | none (unofficial) | breaks with 429s | n/a | n/a | **SKIP** |
| YouTube Data API | API key | 10,000 units/day + 100 search.list | minutes to hours | titles only | **SKIP** |
| Mastodon | none for public timelines | 300 req / 5 min | real time | sparse | **SKIP** |
| Discord / Telegram | bot invites | n/a | n/a | n/a | **SKIP** (closed groups, pump venue) |

## Source notes

**X / Twitter. SKIP.** "Free tier" is a myth for reads. The pricing page lists pay-per-use only: **$0.005 per post read** and a cap of 3M reads per month. The $20 credit needs a saved card *and* a purchase. The ToS ban scraping "in any form, for any purpose without our prior written consent" (since Sep 2023).

**Reddit Data API. SKIP direct.** The limits are still 100 QPM per OAuth client and 10 QPM anonymous. Since Nov 2025, **all new OAuth tokens need pre-approval** (Responsible Builder Policy), and personal bots are rarely approved. Commercial use needs an agreement, and ML training is barred without permission *(unverified)*. Public `.json` URLs (~10/min) get blocked on cloud IPs.

**StockTwits. SKIP.** The official notice says: "won't be accepting new registrations until we have finished our review." Unofficial endpoints sit behind Cloudflare.

**ApeWisdom. USE.**
- Endpoint: `https://apewisdom.io/api/v1.0/filter/all-stocks/page/1`
- Filters: `wallstreetbets`, `stocks`, `options`, `Daytrading`, and others. Pages hold 100 rows.
- Fields: `rank, ticker, name, mentions, upvotes, rank_24h_ago, mentions_24h_ago`.
- Counting: one mention per post or comment. A ticker needs caps or `$`, and word-like tickers (YOLO) need `$`. Scans run twice an hour.
- No published terms or limits, so cache, attribute, and snapshot to build history.

**Tradestie. USE.**
- Endpoint: `https://tradestie.com/api/v1/apps/reddit?date=2026-11-09`
- Fields: top 50 `ticker, no_of_comments, sentiment, sentiment_score`, refreshed every 15 min.
- `date` allows backfill, which seeds the 30-day baseline on day one.
- Limits: 20 req/min anonymous, 200 with a free key.
- Commercial use is allowed with attribution and no raw resale. Best effort.

**Wikipedia pageviews. USE (confirmer).**
- Endpoint: `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia.org/all-access/user/Nvidia/daily/20261001/20261109`
- Daily only. Hourly dumps live at `dumps.wikimedia.org/other/pageviews/` *(lag unverified)*.
- Terms: an identifying User-Agent with contact details, and ~100 ms pacing.
- Use: shows whether attention spread beyond Reddit. Next-day signal only.

**Bluesky. MAYBE.** Jetstream (`wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post`) needs no credentials in the official examples and is real time. Finance volume is thin, so cashtags need a local `\$[A-Z]{1,5}\b` regex. Phase 2.

**Hacker News. MAYBE.** Base URL `https://hacker-news.firebaseio.com/v0/` (`/newstories`, `/item/<id>.json`). Docs: "There is currently no rate limit." Useful only for mega-cap tech novelty.

**Google Trends. MAYBE.** pytrends was archived on 17 Apr 2025 and now returns 429s and 404s: SKIP. The official **API alpha** is application-only, with daily-or-coarser data over 5 years. Apply for it as a slow overlay.

**Others. SKIP.**
- 4chan `a.4cdn.org/biz/catalog.json` (1 req/s, attribution required). ApeWisdom already ingests it.
- YouTube: 10,000 units/day plus 100 search.list/day. Not intraday.
- Mastodon: 300 req per 5 min, negligible volume.
- Finnhub social sentiment: reported premium *(unverified)*.

## Evidence: does attention predict short-term returns?

- **Google search** (Da, Engelberg & Gao, *JF* 2011): search spikes lift small caps ~0.08%/week for 2 weeks, then reverse.
- **StockTwits/Twitter intraday** (Agrawal, Azar, Lo & Singh, *JPM*):
  - Extreme (>3σ) sentiment follows momentum, then returns **mean-revert within ~30 min**.
  - Negative sentiment has about 2× the effect of positive.
  - Gains were mostly reachable at market-maker costs.
- **WSB** (Bradley et al., *RFS* 2024): due-diligence posts predicted 1-month returns before 2021. **That value vanished after GME.** Separately, Semenova & Winkler found strong bullish consensus predicts negative next-week returns.
- **Pump-and-dump** (Renault, OTC microcaps): tweet spikes >2σ brought +6.5% on the event day, then **−2.5% over the next 5 days**. The SEC's 2022 case against 8 influencers (~$100M) followed the same buy, hype, dump pattern.

**Takeaway:** attention is a volatility and reversal signal, not a buy signal. Kaibot should use it three ways:
1. As a risk filter.
2. As loser-hunter support: negative-sentiment spikes on strong names are mean-reversion candidates.
3. As regime context for the debate room.

## Recommended stack

1. **Core:** Tradestie plus ApeWisdom, polled every 15 min from 09:00 to 16:30 ET and hourly otherwise.
2. **Confirmer:** a nightly Wikipedia batch covering ~500 watchlist tickers mapped to their articles.
3. **Phase 2:** an HN and Bluesky novelty stream, with sentiment from a FinBERT-class Hugging Face model or Claude.
4. **Excluded:** X, direct Reddit, StockTwits, pytrends, Discord/Telegram.

## Feature spec (per ticker, per 15-min bar)

Store `ts, source, ticker, mentions, mentions_24h_ago, rank, rank_24h_ago, sentiment_score`.

- **Mention z-score.** `z = (log1p(x_t) − median_30d) / (1.4826·MAD_30d + 0.1)`
  - `x_t`: rolling 24 h mentions (ApeWisdom) or `no_of_comments` (Tradestie).
  - The baseline uses the same time of day over 30 days and needs at least 10 days, else NaN.
  - `z_attn = max(z_ape, z_tradestie)`, with 0.5 weight when only one source covers the ticker. Also track `Δz` over 1 h.
- **Sentiment.** `s = score · n/(n+20)`, which shrinks low-volume scores toward 0. `extreme_sent` fires when |s| exceeds the ticker's 30-day 95th percentile.
- **Novelty.** `novel = 1` when any of these hold:
  - the ticker was absent from both top-100 lists for 30 days;
  - `rank_24h_ago − rank ≥ 50`;
  - Wikipedia views exceed 3× the 30-day median with no Finnhub headline.
- **Pump risk.** `pump_risk = 1` when `z_attn ≥ 3` plus any of: price < $5, market cap < $300M, no news, or `novel`. **Never open longs** on it.
- **Usage.** Features gate trades but never trigger them alone.
  - Block longs on `z_attn ≥ 3` with positive extreme sentiment.
  - Boost loser-hunter candidates on `z_attn ≥ 2` with negative sentiment and strong fundamentals.
  - Backtest on the Tradestie backfill before giving the features any weight.

## Sources

- X API pricing: https://docs.x.com/x-api/getting-started/pricing
- X API intro: https://docs.x.com/x-api/introduction
- X scraping ban (TechCrunch): https://techcrunch.com/2023/09/08/x-updates-its-terms-to-ban-crawling-and-scraping
- Reddit Data API Wiki: https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki
- Reddit pre-approval change: https://molehill.pages.dev/blog/reddit_killed_self-service_api_keys_your_options_for_automated_reddit_integration
- StockTwits developers: https://api.stocktwits.com/developers
- StockTwits Cloudflare note: https://github.com/Antonio-1110/stocks_but_fr/issues/6
- Alpaca and StockTwits: https://alpaca.markets/blog/provide-real-time-social-sentiment-to-customers-with-alpacas-stocktwits-integration/
- ApeWisdom API: https://apewisdom.io/api/
- ApeWisdom methodology: https://apewisdom.io/methodology/
- Tradestie API: https://tradestie.com/apps/reddit/api/
- Wikimedia page metrics: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/examples/page-metrics.html
- Wikimedia Robot policy: https://wikitech.wikimedia.org/wiki/Robot_policy
- HN API: https://github.com/hackernews/api
- Bluesky firehose: https://github.com/bluesky-social/bsky-docs/blob/main/docs/advanced-guides/firehose.mdx
- Bluesky rate limits: https://docs.bsky.app/docs/advanced-guides/rate-limits
- Google Trends API alpha: https://developers.google.com/search/apis/trends
- pytrends status: https://apiserpent.com/blog/pytrends-dead-google-trends-data-2026
- YouTube quota: https://developers.google.com/youtube/data/getting-started
- Mastodon rate limits: https://docs.joinmastodon.org/api/rate-limits/
- 4chan API: https://github.com/4chan/4chan-API
- Da, Engelberg & Gao: https://www.cxoadvisory.com/sentiment-indicators/google-search-data-as-a-measure-of-investor-attention/
- Agrawal, Azar, Lo & Singh: https://alphaarchitect.com/fintwit-might-matter-for-momentum-and-mean-reversion-in-stock-prices/
- Bradley et al. (RFS 2024): https://suchscience.net/?p=33659
- Semenova & Winkler: https://arxiv.org/pdf/2104.01847.pdf
- Renault (pump-and-dump): https://cxoadvisory.com/?p=30149
- SEC influencer case: https://www.financemagnates.com/fintech/us-sec-charges-8-twitter-influencers-with-100m-pump-and-dump-fraud/
