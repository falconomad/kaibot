# Alpha Vantage earnings backfill (free tier, research only; #797, decision 20)

Research data only: the engine never reads it. Free tier only (stakeholder, 26 Nov 2026): 25 requests a day and
5 a minute per key, shared with the owner's other uses of the key. The backfill spends at most **20 a UTC day**
(`--budget`, never above 23), **13 s apart**, and stops at the first rate-limit answer. 500 symbols: about 25 days.

## Owner setup (once, about 2 minutes)

1. Add one line to **`~/Projects/kaibot-secrets/data.env`** (create the file with mode 600 if it does not exist
   yet; it is the data-only key file of #712, see [secrets.md](secrets.md)):

   ```bash
   ALPHAVANTAGE_API_KEY=<your key>
   ```

2. Check it (prints "configured (not shown)", never the key; sends no request):

   ```bash
   cd ~/Projects/kaibot && git pull && ./scripts/fetch_av_earnings.sh --check-key
   ```

3. Schedule the nightly batch at 02:00 local time (launchd, far from the 15:30-16:10 ET quiet window):

   ```bash
   ./scripts/fetch_av_earnings.sh --install-daily
   ```

The research runner also has the script on its allow-list (`ops/runner/allowed.yaml`, heavy so the quiet window
defers it) and a first queued batch (`av-earnings-2026-11-27`, results on #797). The runner and launchd count
against the same daily total (`data/earnings/alphavantage/state.json`), so together they never pass 20 a day.

## What it does

`scripts/fetch_av_earnings.py` asks `function=EARNINGS` for each symbol of the engine universe (the 500 current
S&P 500 members), alphabetically, skipping symbols already cached. Only when every symbol is cached are cached
symbols older than 100 days re-read, oldest first.

| Answer | What happens |
|---|---|
| quarterly rows | stored as received in `data/earnings/alphavantage/raw/<SYMBOL>.json` |
| `Information` or `Note` (rate limit) | not stored; no more requests until the next UTC day; exit 0 |
| `Error Message` about the key | stops; exit 2: check the key |
| `{}` or another `Error Message` | recorded as "no data" in `state.json`, asked again after 100 days |
| network/HTTP error | the request counts; the run stops; exit 1 |
| no key anywhere | `WARNING`, no request, exit 0 |

The key is read (that name only) from `data.env`, else from the `--env` file, else the environment. It goes out as
the `apikey` query value, which the HTTP layer redacts in every log line. `./scripts/fetch_av_earnings.sh --status`
shows progress (cached n/500, requests used today) without sending anything.

## Reading it

`kaibot.data.av_earnings.AVEarnings(...).known_as_of(symbol, date)`: only reports usable at that date's open, by
the conservative rule (from the open of the first session after `reportedDate`). EPS values are split-adjusted;
`surprise_pct` is not affected by splits. Details: [decision 20](../decisions/20_earnings_surprise_drift.md).

**Licence:** personal, non-commercial. Flag to the stakeholder before any multi-user exposure.
