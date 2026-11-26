# Secrets: which process reads which key (audit 2 7.2, #712)

Every secret lives in a file under `~/Projects/kaibot-secrets/` on the Mac (never in the repository, GitHub or a
chat), except the dead-man's copy in Supabase Vault. Each file is mode 600. This page lists every process and what
it reads. Keep it in step when a process or a key changes.

## The files

| File | Holds | Read by |
|---|---|---|
| `.env` | the **trading pair** `ALPACA_API_KEY` / `ALPACA_SECRET` of the real paper account; `SUPABASE_SERVICE_KEY`; `FRED_API_KEY`, `FINNHUB_API_KEY`, `ANTHROPIC_API_KEY`, `SEC_USER_AGENT`, `HEALTHCHECKS_PING_URL`, the other data keys of `.env.example`; the `KAIBOT_*` settings | the engine, the watchdog, the alarm pinger, the nightly job and the hand-run scripts below |
| `helper.env` | the helper sign-in: `SUPABASE_PUBLISHABLE_KEY` (not secret), `SUPABASE_HELPER_EMAIL`, `SUPABASE_HELPER_PASSWORD`; optional `SUPABASE_URL`. Until `data.env` works it may still hold the trading pair (the setup before #712: only the price relay reads it, and only while there is no `data.env`); the owner deletes those lines once `data.env` works (setup step 7). A service key is refused | the price relay, the notice job, `shadow_results.py`, `run_ablation.py --supabase`, `helper_check.py` ([helper_role.md](helper_role.md)) |
| `data.env` (#712) | the **data pair** `ALPACA_DATA_KEY` / `ALPACA_DATA_SECRET` of a **second, empty Alpaca paper account**; `ALPHAVANTAGE_API_KEY` (#797, free tier, research only). A trading pair or a service key in it is refused | the price relay, the ticker-check worker, `data_key_check.py`; `fetch_av_earnings.py` (its one name only, [alphavantage.md](alphavantage.md)) |
| `runner_gh_token` (#711) | the research runner's fine-grained GitHub token | the research runner only ([research_runner.md](research_runner.md)) |
| Supabase Vault | `kaibot_alpaca_paper_key_id`, `kaibot_alpaca_paper_secret`: the trading pair again | the database dead-man (pg_cron), which must cancel and sell on the real paper account when the Mac is gone ([failsafe.md](failsafe.md)) |
| `staging.env` (optional) | a staging account's own keys, same names as `.env` | a staging engine only ([always_on.md](always_on.md)) |

**Why a second Alpaca account.** Alpaca paper keys can't be scoped. A key pair that can read market data or
positions can also place orders on its account, and there is no read-only or data-only key. So the helpers that need
market data only get the key pair of a **different** paper account, which holds nothing and is never traded. That
pair can place orders only on that empty account: it can't see, buy or sell anything of the real paper run. The
ticket's test, "the relay's key cannot place an order", holds because the account is a different one, not because
the key is weaker. `scripts/data_key_check.py` checks exactly that (below). Each account also has its own single IEX
stream connection, so the relay no longer competes with anything on the trading account's connection.

## Every process and the secret it reads

| Process | How it runs | Reads | Never reads |
|---|---|---|---|
| Engine (`python -m kaibot.engine`) | `run_engine.sh`, under the always-on service (`com.kaibot.engine`) | `.env`: the trading pair, the service key, FRED, Finnhub, Anthropic (agent room), SEC user agent | `helper.env`, `data.env`, the runner token |
| Watchdog (`kaibot.engine.watchdog`) | started by `run_engine.sh` beside the engine | `.env`: the trading pair (cancels entries, keeps stops), the service key | the others |
| Alarm pinger (`scripts/healthcheck.py`) | beside the engine (`service_run.sh`) and in the nightly job | `.env`: `HEALTHCHECKS_PING_URL` only | everything else |
| **Ticker-check worker** (`scripts/run_ticker_checks.py`) | beside the engine (`service_run.sh`) | **With `data.env`:** `data.env`: the **data pair** (asset record, daily bars); `.env`: only `SUPABASE_URL`, the service key (it answers `kaibot.ticker_check` rows: no helper function for them yet) and `FINNHUB_API_KEY`. The trading pair's values are never sliced out of their lines or kept (`common/secret_files.read_names`); the file's text passes through memory once while it is read. **Without `data.env`** (until the owner's setup): the `.env` and its trading pair, as before #712, with a `WARNING` at each start in `ticker_checks.log` | with `data.env`: **the trading pair** (#712); always: `helper.env`, the runner token |
| **Price relay** (`scripts/price_stream.py`) | beside the engine when `KAIBOT_PRICE_STREAM=on` | **With `data.env`:** `helper.env` (signs in as the helper; held stocks through `kb_helper_held_symbols`, migration 0053; heartbeat and broadcast through 0038) and `data.env` (the **data pair**: the clock and the IEX stream); a trading pair still in `helper.env` is skipped; no `helper.env`, or a bad `data.env`, stops it (no fall-back). Until 0053 is applied it streams nothing (said once). **Without `data.env`** (until the owner's setup): as before #712, `helper.env` with its paper trading pair (held stocks by `GET /v2/positions`), else the `.env` with the service key, with a `WARNING` at each start in `price_stream.log` | with `data.env`: **the `.env`** and any trading pair (#712; in Docker the box gets only `helper.env` and `data.env`); always: the runner token |
| Nightly job (`service_run.sh daily`) | `com.kaibot.daily` | `.env`: `fetch_daily.sh` reads the trading pair for SIP daily bars; the notice job (`publish_notices.py`) reads `helper.env` (else the service key) | |
| Research runner (`scripts/research_runner.py`) | `com.kaibot.research-runner`, every 15 min | `runner_gh_token`, for GitHub only (#711). It never opens `.env`: a job marked `env` gets `--env <path>` and opens it itself | `.env`, `helper.env`, `data.env` |
| Runner jobs (`ops/runner/allowed.yaml`) | the runner, one at a time | `shadow_results.py`, `run_ablation.py`: `helper.env`. Scripts marked `env` (the SEC and fundamentals fetchers): the one or two lines of `.env` they need | the runner token (never in a job's environment) |
| Dead-man (`kaibot.deadman_tick`) | pg_cron in Supabase | Vault: the trading pair | everything on the Mac |
| Dashboard (`apps/web/`) | Vercel and the owner's browser | the publishable key and the owner's sign-in; Vercel's own environment for its API routes | everything on the Mac |
| Hand-run scripts | by the owner | `.env`: `go_live_paper.sh`, `alpaca_no_shorting.sh`, `alpaca_fee_activities.sh`, `smoke_test_keys.py`, `probe_alpaca_idempotency.py` (trading pair); `trial_registry.py`, `run_config_replays.py`, `eval_room.py --live`, `outage_drill.sh` (service key); the research fetchers (data keys of `.env`). `data_key_check.py` reads `data.env`, `helper.env`'s names and, to compare the two accounts, the trading pair | |
| Backups (`backup_local.sh`) | nightly | no secret: the secrets folder is never in a backup ([backup_restore.md](backup_restore.md)) | all of them |

Still holding the trading pair outside the engine and the watchdog: the dead-man's Vault copy (it must be able to
trade the real account, by design; whether it stays is the owner's call in the audit), the nightly `fetch_daily.sh`
and the hand-run research fetchers (`collect_alt.py`, `fetch_daily.py`, and others). Those read market data only
and are the next candidates for `data.env`; they were not moved in #712.

## The data key (one-time setup, owner, about 10 minutes, outside market hours)

1. **A second paper account.** Alpaca: sign up a second free account (a second email), open its paper trading.
   Don't fund it or trade it. Paper > API keys > Generate. (If your Alpaca login lets you add a second paper account,
   that works too: any paper account other than the one the engine trades.)
2. **Migration 0053** (`migrations/0053_helper_held_symbols.sql`): the relay reads the held stocks through the
   helper. The stakeholder applies it, any time ([migrations_pending.md](migrations_pending.md), step 40).
3. **The data file** on the Mac, a new file (never in `.env`, never `helper.env`):
   ```bash
   cd ~/Projects/kaibot-secrets
   ( umask 077; printf 'ALPACA_DATA_KEY=%s\nALPACA_DATA_SECRET=%s\n' '<the second account key>' '<its secret>' > data.env )
   chmod 600 data.env
   ```
4. **Check it** (prints no key; only GETs, no order):
   ```bash
   cd ~/Projects/kaibot && PYTHONPATH=src python3 scripts/data_key_check.py
   ```
   Every line but one should be `OK`: data.env holds the data pair only; the data pair signs in; **the data account is
   not the trading account**; it holds no position; it reads market data. The line "helper.env holds no trading pair"
   stays `FAIL` until step 7.
5. **Restart the service** outside 09:30-16:00 ET: `./scripts/service.sh restart`. From this start the two helpers
   use the data key: `logs/ticker_checks.log` says "market-data key of the second paper account (no trading key
   here)", and `logs/price_stream.log` says "signed in as the helper with the market-data key" (and, until 0053 is
   applied, "migration 0053 is not applied ... streams nothing").
6. **Watch one session**: ticker checks answer on the Strategy page, and (with 0053) the dashboard's prices stream.
7. **Then take the trading pair out of `helper.env`**: delete its `ALPACA_API_KEY` and `ALPACA_SECRET` lines (they
   are no longer read once `data.env` exists; nothing deletes them for you). Run `data_key_check.py` again: every
   line `OK`.

**Before the setup nothing stops (Product, 26 Nov 2026).** Without `data.env` the ticker-check worker and the price
relay run exactly as before #712, on the paper trading key, and each start writes a `WARNING: no market-data key
file ...` line in its log. The moment `data.env` exists, neither reads the trading key again: there is no fall-back
from a `data.env` that is there but wrong (it stops the helper with a message), and with `data.env` but without
0053 the relay streams nothing rather than read positions with the trading key (the dashboard keeps its 10 s
prices). To go back deliberately: remove `data.env` and restart.
