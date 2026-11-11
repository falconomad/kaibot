# Alt-data collectors (story S19)

Three free sources are collected every night to build point-in-time history for the M3 ablation (PRD v2:
"collected from M1; traded only after a 3-month ablation"). They only accrue going forward, so the collectors are
resumable and idempotent. **The engine never reads them**: nothing under `src/kaibot/{engine,features,strategies,
risk,broker,agents,replay}` may import `kaibot.ingest.alt` (enforced by `tests/unit/test_alt_isolation.py`).

| Source | What | Endpoint | Published | Requests / night | Budget (`config/sources.yaml`) |
|---|---|---|---|---|---|
| `finra_regsho` | Reg SHO daily short-sale volume, consolidated NMS (CNMS): off-exchange (TRF/ADF/ORF) short, short-exempt and total volume per symbol, regular hours | `https://cdn.finra.org/equity/regsho/daily/CNMSshvol<YYYYMMDD>.txt` | by 18:00 ET on the trade date; rarely re-posted later | 5 (last 5 trade dates, to catch re-posts) | 1 s between requests |
| `form4` | SEC Form 4 / 4/A insider transactions where a universe stock is the **issuer** | `data.sec.gov/submissions/CIK##########.json`, then the raw ownership XML in `www.sec.gov/Archives/edgar/data/...` | on acceptance | ~62 + 1 per new filing | `sec_edgar`: 5 req/s (SEC fair access allows 10), declared `SEC_USER_AGENT` |
| `gdelt_doc` | Average tone and article volume, 15-minute buckets, for 6 macro/geo queries (tariffs, sanctions, Fed rates, conflict, energy, recession) | `https://api.gdeltproject.org/api/v2/doc/doc?mode=timelinetone|timelinevolraw&format=json` | continuously, 15-min cycle | 24 (6 queries x 2 modes x 2 days) | 6 s between requests (GDELT: one every 5 s) |

Queries live in `src/kaibot/ingest/alt/gdelt.py` (`QUERIES`); their names are partition keys, so rename with care.

## Layout and point-in-time rules
```
data/alt/<source>/date=YYYY-MM-DD/part-<ingest time>-<id>.parquet     (append-only)
data/alt/form4_filings/...            ledger: every accession processed (ok | no_transactions | not_issuer | invalid)
data/alt/_meta/sec_company_tickers.json   SEC ticker -> CIK map, refreshed weekly
```
Every row has `available_ts` (the first time Kaibot could have known it) and `ingested_ts`. Readers must use
`kaibot.ingest.alt.store.read_pit(root, source, decision_ts, key)`, which keeps rows with
`available_ts <= decision_ts` and the latest version per key.

- **FINRA:** `available_ts = max(18:00 ET on the trade date, Last-Modified)`. A re-posted file adds only the rows
  whose numbers changed, as new versions dated by Last-Modified (or by our ingest time).
- **Form 4:** `available_ts` = EDGAR acceptance time; accepted at/after 17:30 ET -> 00:00 ET of the next weekday
  (tech design §2). `accepted_ts` and `filing_date` are kept too. Filings where a universe company is the
  reporting *owner* of another issuer (e.g. a bank's 10 % stakes) are logged as `not_issuer` and skipped.
- **GDELT:** buckets are revised as articles arrive, so `available_ts = ingested_ts`. A re-run adds only changed
  buckets as new versions.

Idempotent: re-running a day writes nothing unless the provider changed something. One collector per source at a
time (a lock file in `data/alt/<source>/.lock`); a second one exits with an error.

## Safety
- Every request goes through `kaibot.common.http`: HTTPS only, timeout, shared per-source budget, response size cap
  (FINRA 32 MB, SEC 16 MB / 4 MB per document, GDELT 4 MB), 429/5xx retried with backoff (a 429 holds the whole source).
- All payloads are untrusted: headers, field counts, types, dates and number ranges are checked; bad rows are logged
  and skipped, a mostly-bad file is rejected. No `eval`, no pickle.
- XML (EDGAR feed, Form 4) goes through `kaibot.common.safexml`: any DTD or entity declaration is refused before
  parsing (no entity expansion, no external entities); see the module docstring for why that replaces defusedxml.
- Only `SEC_USER_AGENT` is needed; it is read from the env file you pass and never logged. FINRA and GDELT need no key.

## Running it
```
python scripts/collect_alt.py --env ~/Projects/kaibot-secrets/.env               # all sources, recent days
python scripts/collect_alt.py --env ... --source finra --date 2026-11-09          # one source, one date
python scripts/collect_alt.py --env ... --data /path/to/data                      # default: ./data
```
Run it **after 18:30 ET** (FINRA posts by 18:00 ET) and outside 09:30-16:00 ET. It takes 3-4 minutes, mostly GDELT
pacing. Exit code 1 means at least one source failed; the others still ran. Without `--date` it re-checks recent
days, so a missed night is caught up by the next run (FINRA 5 trade dates, Form 4 last 3 days, GDELT 2 days;
for a longer gap run `--date` per day; GDELT only serves the last ~3 months).

It is a separate short process, not part of the engine, so it can run while the engine is stopped; its budgets are
per process, so avoid running it at the same time as `scripts/fetch_fundamentals.py` (both use the SEC budget).

### Nightly on the Mac (launchd)
Save as `~/Library/LaunchAgents/com.kaibot.collect-alt.plist` (adjust the paths), then
`launchctl load ~/Library/LaunchAgents/com.kaibot.collect-alt.plist`. launchd uses the Mac's local time: 00:45 in
Central European time is 18:45 or 19:45 ET all year. If the Mac was asleep, launchd runs the job once at wake.
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.kaibot.collect-alt</string>
  <key>ProgramArguments</key><array>
    <string>/opt/homebrew/bin/python3</string>
    <string>/Users/YOU/Projects/kaibot/scripts/collect_alt.py</string>
    <string>--env</string><string>/Users/YOU/Projects/kaibot-secrets/.env</string>
    <string>--data</string><string>/Users/YOU/Projects/kaibot/data</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>45</integer></dict>
  <key>StandardOutPath</key><string>/Users/YOU/Projects/kaibot/logs/collect_alt.log</string>
  <key>StandardErrorPath</key><string>/Users/YOU/Projects/kaibot/logs/collect_alt.log</string>
</dict></plist>
```
Check it: `tail -50 ~/Projects/kaibot/logs/collect_alt.log`; data: `ls data/alt/*/`.
On a VM later: the same command from cron (`45 23 * * *` in UTC is 18:45 ET in winter, 19:45 in summer; FINRA simply finds no file on weekends) or a
`docker compose run` of the engine image with `python scripts/collect_alt.py` (mount `scripts/` and the data volume).

## Features for the ablation (story S37)
`src/kaibot/features/alt.py` is the one audited reader of this store from the trading packages (tech design §1:
"Alt data is unreachable from features, except pump_risk"). Nothing in engine, strategies, risk, broker, agents or
replay may import it (`tests/unit/test_alt_isolation.py`); its output is for the signal log and the M3 ablation only.
All readers use `read_pit`; missing data gives None.

| Feature | Definition |
|---|---|
| `short_volume(root, symbol, decision_ts)` | latest CNMS short/total volume ratio vs the mean of the previous 20 trade dates (>= 10 needed): `{trade_date, svr, svr_mean20, svr_rel}` |
| `insider_net_buying(root, symbol, decision_ts, days=30)` | Form 4 non-derivative P minus S, transaction date in the last 30 days: `{net_shares, net_usd, n_buys, n_sells}`; None if the symbol is not in the filing ledger |
| `gdelt_tone_z(root, query_name, decision_ts)` | latest 15-min average tone vs the previous buckets of 7 days (>= 20 needed) |
| `alt_features(root, symbol, decision_ts)` | all of the above, every query |

The live `pump_risk` long-block reads no alt data; see `docs/ops/news.md`. The admission test itself (story S39,
`scripts/run_ablation.py`) is described in `docs/ops/ablation.md`.
