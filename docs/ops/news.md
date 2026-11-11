# News: dedup, triage, evidence (story S33) and pump_risk (story S37)

## Pipeline (`src/kaibot/news/`)
```
AlpacaData.news(symbols, start, end)        Benzinga via Alpaca, 50/page, shared `alpaca` budget (sources.yaml)
  -> normalise        untrusted text cleaned (control/bidi chars removed, capped), tickers validated, http(s) URLs only;
                      source = the press-release wire when Benzinga republishes one (globenewswire, prnewswire,
                      accesswire, businesswire, newsfile), else the provider source (benzinga)
  -> dedup            skip (headline_sha1, source) already stored (unique index news_item_sha_source)
  -> cluster          same ticker AND published within 24 h of the cluster's first story AND
                      (same canonical-headline SHA-1 OR MinHash Jaccard >= 0.8, word 3-shingles, 64 permutations)
  -> news_item row    available_ts = Kaibot's ingest time; cluster n_outlets = distinct sources, last_seen
  -> triage           exactly one Haiku call per NEW cluster (models.triage, limits.triage in config/llm.yaml)
```
- **Canonical headline:** lowercased; wire tags stripped (`UPDATE 2-`, `BRIEF-`, `(Reuters)`, `- Bloomberg`, `[PRN]`...);
  punctuation and whitespace collapsed. `headline_sha1` = SHA-1 of that.
- **Cost of dedup:** candidates come from an in-memory window bucketed by ticker (pruned after 48 h), so a new item
  is compared only with recent clusters that share a ticker. `NewsDesk.load_window()` rebuilds it after a restart;
  clusters loaded that way are never triaged again.
- **Triage** (`news/triage.py`): forced tool `triage_cluster` {cause, materiality 0-1, direction, summary <= 200},
  validated by `agents.contracts.TriageScore` (extra fields rejected; no price/size/stop/exit fields exist). The
  headlines go in as JSON inside `<news_data>`; the system prompt says the block is data, never instructions.
  Budget refused, timeout or invalid output -> `triage` stays null and is not retried (the rules-only veto applies).

## Point-in-time reads
`clusters_for(symbol, as_of, hours=24)` and `evidence_pack(...)` use only clusters with `first_seen <= as_of` and
items with `available_ts <= as_of`; tickers and `n_outlets` are recomputed from those items, and a triage whose
`triage_ts > as_of` is hidden.

## `evidence_pack` (for the agent room)
```python
from kaibot.news import NewsDesk, configure, evidence_pack
desk = NewsDesk(store, source=alpaca_data, gateway=gateway); desk.load_window(); configure(desk)
desk.poll(symbols)                                   # each news cycle; never raises
pack = evidence_pack(symbol, sector_etf, as_of, hours=24)    # == desk.evidence_pack(...)
```
Returns
```
{"symbol", "sector_etf", "as_of" (ISO UTC), "hours",
 "items": [{"id": "n<cluster_id>", "kind": "news", "scope": "ticker" | "sector", "source", "published_at",
            "headline", "url", "n_outlets", "triage": {cause, materiality, direction, summary} | None}],
 "n_clusters", "truncated", "complete"}
```
At most 20 items, newest first (by the cluster's latest story); `source`/`headline`/`url`/`published_at` are the
cluster's first story. `complete: false` means a store read failed or no desk is configured: treat it as no
evidence. Headlines are cleaned but still untrusted: keep them inside a data block in any prompt. Each item's
`id`, `source` and `published_at` map onto `agents.contracts.EvidenceRef`.

## pump_risk and the extra rules-only veto (`src/kaibot/features/pump.py`)
`pump_risk(symbol, as_of, bars, news, *, market_cap=None, has_8k=None) -> {"flag", "score", "reasons"}` scores
SEC/FINRA pump-and-dump red flags (low price / microcap / thin dollar volume; abnormal volume; big gap or spike;
only promotional wire releases with no independent news or 8-K). It flags only with score >= 0.5 and at least one
quality flag and one spike flag, so liquid large caps do not flag. The weights and sources are in the module
docstring.

`rules_veto_extra(symbol, as_of, *, bars=None, news=None, side="buy", market_cap=None, has_8k=None) -> str | None`
returns `"pump risk"` for a flagged long (or `"pump risk check failed"` if the check itself errors), else None.
It never blocks sells/exits, reads no alt data and does no I/O. `bars` must be point-in-time daily bars (today's
row = the session so far); evidence-pack items can be passed as `news`.
