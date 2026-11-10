# 03 — News and NLP sentiment (free sources)

Checked 10 Nov 2026 on official pages where reachable. Finnhub's own pages were unreachable, so its details come from secondary sources and are marked "unverified". No endpoint has been tested live yet: the proxy blocked curl.

## Summary table

| Source | Auth | Free limit | Latency | Tickers | History | Verdict |
|---|---|---|---|---|---|---|
| Alpaca News (Benzinga) | API key + secret | 200 calls/min on free plan; WebSocket stream | Near real-time; one user saw ~10 min lag and gaps | Yes (`symbols`) | 2015+ | **USE** |
| Finnhub company/market news | token | ~60 calls/min, personal use only | Minutes | Yes (by query) | ~1 yr (unverified) | **USE** |
| Finnhub news-sentiment | token | Premium (unverified) | – | – | – | **SKIP** |
| Alpha Vantage NEWS_SENTIMENT | key | 25 req/day | Minutes | Yes, with relevance and sentiment | Long | **MAYBE** |
| Marketaux | token | 100 req/day × 3 articles | Minutes | Yes (entities) | ? | **MAYBE** |
| Tiingo News | token | 50/hr, 1,000/day, internal use | Real-time | Yes | None on free | **MAYBE** |
| NewsAPI.org | key | 100/day, **24 h delay**, dev only | 24 h | No | 1 month | **SKIP** |
| NewsData.io | key | 200 credits/day, **12 h delay** | 12 h | No | ? | **SKIP** |
| GNews | key | 100/day × 10, **12 h delay**, non-commercial | 12 h | No | 30 d | **SKIP** |
| GDELT DOC 2.0 | none | Unmetered (be polite) | ~15 min cycle | No (keywords) | Timelines 2017+; artlist 3 mo | **USE** (macro) |
| Google News RSS | none | Unofficial; 100 items/query | Minutes–tens of min | No | Day-granular | **MAYBE** |
| Yahoo per-ticker RSS | none | Unofficial; 20 items/feed | Minutes | Yes (per feed) | None | **MAYBE** |
| PR Newswire / GlobeNewswire RSS | none | Polling | **Primary source, seconds–1 min** | Parse "(NASDAQ: X)" | Feed only | **USE** |
| Business Wire / Accesswire RSS | none | Unclear | Primary source | Parse | Feed only | **MAYBE** |
| SEC EDGAR 8-K Atom | User-Agent with contact | 10 req/s | Seconds after acceptance | CIK → ticker map | 2005+ | **USE** |
| FinBERT (local CPU) | none | Free, local | ms per headline | n/a | n/a | **USE** (baseline) |
| HF Inference Providers | HF token | **$0 for free accounts** since 15 Oct 2026; PRO $2/mo | – | – | – | **SKIP** |
| FinGPT | none | Needs a GPU (7B+) | – | – | – | **SKIP** |
| Claude API (Haiku 5.5 → Sonnet 5.5) | key | Paid from Claude credits | ~1 s | Extracts them | – | **USE** |

## Source notes

**Alpaca News.** REST `GET https://data.alpaca.markets/v1beta1/news?symbols=AAPL,TSLA&limit=50`. Stream `wss://stream.data.alpaca.markets/v1beta1/news`; after auth, send `{"action":"subscribe","news":["*"]}`. Every item carries `symbols`, `headline`, `summary` and `created_at`, and the source is Benzinga. History goes back to 2015. It launched free at 200 calls/min (as a "limited-time beta"), and current docs don't say it is paid. Confirm with a free key on day 1. *Verdict: USE as the primary tagged stream; measure lag against wire RSS.*

**Finnhub.** `https://finnhub.io/api/v1/company-news?symbol=AAPL&from=2026-11-09&to=2026-11-10&token=KEY` and `/api/v1/news?category=general|merger`. The free tier gives about 60 calls/min for personal use, and `news-sentiment` is premium (unverified). *USE as a second tagged feed.*

**Alpha Vantage** (`/query?function=NEWS_SENTIMENT&tickers=AAPL&apikey=KEY`): 25 requests a day is too few intraday. Use it as a daily calibration label. **Marketaux** (`api.marketaux.com/v1/news/all?symbols=TSLA&filter_entities=true&api_token=KEY`): about 300 articles a day, and the free page doesn't confirm entity sentiment. **NewsAPI, NewsData, GNews**: delayed 12–24 h, and NewsAPI is dev-only. *SKIP.*

**GDELT DOC 2.0.** `https://api.gdeltproject.org/api/v2/doc/doc?query=(sanctions OR tariff) sourcelang:english&mode=artlist&maxrecords=250&timespan=15min&format=json`. `mode=timelinetone` gives average tone over time. The smallest timespan is 15 min and maxrecords is capped at 250. *USE for geopolitical and macro "external factor" detection (the loser-hunter thesis), not for single stocks.*

**Google News RSS** (`news.google.com/rss/search?q=%22Nvidia%22+when:1h&hl=en-US&gl=US&ceid=US:en`): undocumented, capped at 100 items, links are redirects. **Yahoo RSS** (`feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL,MSFT&region=US&lang=en-US`): works, returns up to 20 items, and the general feeds have returned 404 since 6 Oct 2026. *MAYBE: both are unofficial fallbacks.*

**Press-release wires.** PR Newswire: `https://www.prnewswire.com/rss/news-releases-list.rss` and `…/financial-services-latest-news/financial-services-latest-news-list.rss`. GlobeNewswire publishes its feed list at `globenewswire.com/Rss/List`. Business Wire's official RSS is unreliable. Wires are where earnings and M&A news starts, so they are the earliest free signal. Get tickers with the regex `\((NYSE|NASDAQ|Nasdaq|NYSE American):\s*([A-Z.]+)\)`. *USE PRN and GNW.*

**SEC 8-K.** `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=100&output=atom`. Send a User-Agent with a contact email and stay at or below 10 req/s. EDGAR accepts filings 06:00–22:00 ET, and anything filed after 17:30 is disseminated the next business day. Useful item codes: 1.01, 1.05 (cyber), 2.01, 2.02 (results), 4.02 (restatement), 5.02 (executive departure), 7.01, 8.01. *USE: authoritative, and the item code is a free event label.*

**Hugging Face.** Since 15 Oct 2026 free accounts get **no** included Inference Providers credits (PRO gets $2/mo). So run models locally:
- `ProsusAI/finbert` (~110M params, BERT-base, Financial PhraseBank; **CC BY-NC-SA 4.0, non-commercial**, which is fine for paper trading but must be revisited before going live)
- `yiyanghkust/finbert-tone` (trained on analyst-report sentences; licence not stated)

Both run on CPU with `transformers`, and 2,000 headlines a day is trivial for a CPU (estimate). FinGPT needs a GPU, so *SKIP*. FinBERT scores polarity, not events, so Claude handles catalyst classification.

## Deduplication (one wire story, many outlets)

1. **Canonicalise URLs**: resolve redirects and strip `utm_*` and AMP suffixes.
2. **Normalise headlines**: lowercase and strip the " - Outlet" suffix and punctuation. Catch exact duplicates with a SHA-1 hash.
3. **Find near-duplicates**: MinHash (or SimHash) on character 5-gram shingles, with Jaccard ≥ 0.8 within a 24 h window and the same ticker set. For paraphrases, compare `all-MiniLM-L6-v2` embeddings (CPU) and treat cosine ≥ 0.9 as a match.
4. **Build story clusters**: the canonical item is the **earliest** timestamp, preferring the primary source (8-K, then wire, then Benzinga, then aggregators). Keep `first_seen`, `n_outlets` and `spread_velocity` as features, because a story that spreads fast matters more.
5. Send each cluster to Claude once, which cuts LLM volume by an estimated 40–60%.

## Proposed free stack

- **Ingest (tier 1, real-time):** the Alpaca news WebSocket, plus pollers on SEC 8-K Atom (every 10–15 s), PRN and GNW RSS (every 15–30 s), and Finnhub company-news on a watchlist rotation (≤ 60/min).
- **Context (tier 2):** GDELT macro tone every 15 min, with Google News and Yahoo RSS as fallbacks.
- **Scoring:** dedup, then local FinBERT on every item, then Haiku 5.5 triage per cluster (JSON: tickers, event type, direction, magnitude 0–5, novelty, confidence). Clusters with magnitude ≥ 4 (the top 1–2%) go to Sonnet 5.5 with the article and price context, and its output feeds the agent "room".
- **Labels:** the nightly Batch API (50% off) back-labels history so we can evaluate the signal.

## Claude cost estimate (official pricing, 10 Nov 2026)

Haiku 5.5 (≤100K-token prompts) costs $0.10 per MTok input, $0.125 per MTok for a 5-minute cache write, $0.01 per MTok for a cache hit and $0.50 per MTok output. Sonnet 5.5 costs $2 per MTok input and $10 per MTok output. Batch is 50% off and can be combined with caching.

**Triage, per headline:** a 1,500-token cached system prompt costs $0.000015, 100 fresh input tokens cost $0.00001 and 40 output tokens cost $0.00002, for **≈ $0.045 per 1,000 headlines** ($0.18 without caching). 2,000 headlines a day is about $0.09, plus about $0.03 of cache rewrites, so **≈ $0.12/day, or about $3.6/month**. Pad the prompt to the model's minimum cacheable length; see the prompt-caching docs.

**Escalation:** 20–40 a day × (4,000 input + 600 output tokens) on Sonnet comes to about $0.014 per event, so **$0.28–0.56/day, or about $6–12/month**. The same calls on Opus 5.5 would cost about $0.028 per event.

**Total: about $10–16/month**, mostly Sonnet. Real-time triage cannot use Batch.

## Sources

- Alpaca: https://docs.alpaca.markets/us/docs/streaming-real-time-news.md ; https://alpaca.markets/docs/news-api ; https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/ ; https://forum.alpaca.markets/t/ticker-limits-news-streaming/14727
- Finnhub (secondary): https://docs.nexla.com/user-guides/connectors/finnhub_api ; https://qveris.ai/guides/best-free-financial-news-api/
- Alpha Vantage: https://www.alphavantage.co/support/
- Marketaux: https://marketaux.com/pricing
- NewsAPI: https://newsapi.org/pricing
- GNews: https://gnews.io
- NewsData: https://newsdata.io/blog/best-news-api/
- Tiingo: https://www.tiingo.com/products/news-api
- GDELT DOC 2.0: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
- Google News RSS: https://newscatcherapi.com/blog/google-news-rss-search-parameters-the-missing-documentaiton
- Yahoo RSS: https://feeder.co/knowledge-base/rss-content/yahoo-news-rss-feeds/
- PR Newswire RSS: https://prnewswire.com/rss/
- GlobeNewswire: https://www.globenewswire.com/newswire-press-release-content
- SEC EDGAR access: https://www.sec.gov/os/accessing-edgar-data
- 8-K feed and item codes: https://dev.to/nexgendata/sec-8-k-filings-api-build-a-material-events-tracker-2026-guide-2ll3
- HF pricing: https://huggingface.co/docs/inference-providers/pricing ; change log: https://www.usagepricing.com/blueprint/activity/hugging-face-2026-10-15-free-inference-credits-removed
- FinBERT models: https://huggingface.co/ProsusAI/finbert ; https://huggingface.co/yiyanghkust/finbert-tone
- Claude pricing: https://platform.claude.com/docs/en/about-claude/pricing
