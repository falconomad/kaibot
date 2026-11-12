# Reproducing and extending the M0 replays

## 1. History (story S03)
`./scripts/fetch_history.sh` on the Mac downloads 1-minute SIP bars (09:30–16:00 ET only), Benzinga headlines and
SEC fundamentals for the 300 symbols in `config/universe_history.yaml`, from Jan 2016 to now. It is resumable:
Ctrl-C and run it again. `./scripts/fetch_history.sh --status` shows progress. It uses 135 of Alpaca's 200
requests/min (60 while the engine runs). Expect roughly 12–18 hours and about 12 GB under `data/` (measured: 12.2 GB for 300 symbols, Nov 2026).

Survivorship bias: the universe is today's large caps applied back to 2016. Companies delisted before Nov 2026
are missing, so long replays on it are optimistic. M1 replaces it with a point-in-time universe.

### Symbols outside the universe (test group S66, custom tickers)
`fetch_history.sh` only covers the universe file. For anything else, run the downloader directly on the Mac, from
the repo root (the `.venv` is the one `fetch_history.sh` or `run_engine.sh` created):

```
# the high-beta test group, after `scripts/screen_test_group.py --write` has put the passing names in the seed
KAIBOT_ENV_FILE=~/Projects/kaibot-secrets/.env .venv/bin/python scripts/fetch_history.py \
    --test-group high_beta_growth --start 2024-01-01 --rth-only --per-min 45

# any hand-picked list (custom tickers, or the group's names before --write)
KAIBOT_ENV_FILE=~/Projects/kaibot-secrets/.env .venv/bin/python scripts/fetch_history.py \
    --symbols NBIS,CRWV,FSLY --start 2024-01-01 --rth-only --per-min 45
```

- **What is downloaded:** SPY (the replay's market reference) plus exactly the names given, each once; both options
  together download the union. Bars land in `data/bars_1m/symbol=XYZ/YYYY-MM.parquet`, the layout the replay reads.
  Add `--news` for Benzinga headlines. For history back to 2016 use `--start 2016-01-01`.
- **Resumable:** finished months are skipped (`.empty` marks a month before a listing), so Ctrl-C and the same
  command again continues; only the running month is refreshed. Running it twice changes nothing else.
- **Data rules:** Alpaca SIP history only, never newer than 20 minutes (the free plan allows >= 15 minutes old).
  Requests wait for the shared `alpaca` budget in `config/sources.yaml` (150/min) and the tighter `--per-min`;
  45 keeps the engine's 150 plus this script under Alpaca's 200/min, so it is safe while the engine runs.
- **Symbols:** upper-cased; anything that is not a plain ticker (`NBIS`, `BRK.B`) stops the run before any request.
  An unknown test group stops it too and lists the groups that exist. A ticker Alpaca does not know, or one without
  a single bar since `--start`, is reported at the end and the exit code is 1; the other symbols still download.
- **The live engine is not affected:** nothing in `config/` is written, and the engine reads neither
  `data/bars_1m` nor the `test_groups` block.
- **Time (estimated, not measured):** about 2-3 requests per symbol-month, so 25 names from 2024 take roughly an
  hour at 45 requests/min.

## 2. Re-run replays 001–003 (story S05 check)
The cloud workspace that ran them is gone, so the first job on the new data is to re-run them exactly as they
were: the 71-symbol seed universe, with data from 1 Jan 2025 (that sets the warm-up, e.g. when the 60-day beta
becomes defined). Each run writes `input_sha256` and `events_sha256`. Running the same command twice must give the
same hashes; the trade counts and P&L should match `docs/m0/*.json` unless Alpaca has revised bars since.

```
E=~/Projects/kaibot-secrets/.env; R=".venv/bin/python scripts/run_replay.py --env $E --data-from 2025-01-01"
$R --variant v1 --start 2025-02-01 --end 2026-11-06 --label full          # replay 001: 642 trades, -4.0%
$R --variant A --start 2025-02-01 --end 2026-06-30 --label design         # 002: 750 trades, +0.08%
$R --variant A --start 2026-07-01 --end 2026-11-06 --label holdout        # 002: 187 trades, +0.70%
$R --variant B --start 2025-02-01 --end 2026-06-30 --label design         # 003: 25 trades
$R --variant B --start 2026-07-01 --end 2026-11-06 --label holdout        # 003: 5 trades
.venv/bin/python scripts/trial_report.py                                  # DSR per trial, PBO per window
```
(`.venv/bin/pip install scipy` first: the replay's statistics need it.)

## 3. Overfitting checks (story S08)
Every replay appends to `data/trials.jsonl` and stores its daily returns in `data/trials/<trial_id>.csv`.
`scripts/trial_report.py` computes the deflated Sharpe of each trial (deflated by the number of trials and the
spread of their Sharpes) and, for each window with two or more trials, the probability of backtest overfitting
(CSCV, 16 blocks = 12,870 splits). PBO near 0.5 means picking the best variant on that window was luck.

## 4. Walk-forward (story S08)
Once `./scripts/fetch_history.sh --status` shows the 2016+ download complete:

```
E=~/Projects/kaibot-secrets/.env
.venv/bin/python scripts/walk_forward.py --env $E --universe config/universe_history.yaml \
    --start 2016-01-01 --end 2026-11-06 --variant A --level balanced --grid default
.venv/bin/python scripts/trial_report.py
```

- **Folds:** train on 12 whole months, test on the next month, roll monthly. `--start` is the first date with bars;
  the first train month starts after the 120-day feature warm-up (2016-05), so the first test month is 2017-05
  (115 folds to Nov 2026; the last test month is partial).
- **Grid (`default`, 27 configs, pre-declared):** `z_max` (dip threshold) in {-1.5, -1.75, -2.0},
  `atr_mult` (stop in 5-minute ATRs = 1R) in {1.0, 1.5, 2.0}, `target_r` (target in R) in {1.5, 2.0, 3.0}.
  The pre-registered default is z -1.5, 1.5 ATR, 2R (variant A as replayed in 002).
- **Selection:** highest train daily Sharpe after shadow costs among configs with >= 100 train trades
  (`--min-train-trades`); ties go to the default; if none qualifies the default is used (reported as a fallback).
  The choice for month M is made before M is simulated. The default also runs on every test month (no-tuning
  baseline).
- **Simulation:** every month is simulated for every config from flat $100k, so the run-drawdown stop resets
  monthly. Bars are loaded `--block-months` (3) months at a time plus the warm-up; features are computed once per
  block and shared by the grid.
- **Output:** `data/reports/walkforward_<level>_wf_A_<ts>.{json,md}`: OOS (stitched test months) trades, t-stat per
  trade, daily Sharpe, hit rate, trades/month, max drawdown for tuned and default; SPY over the same sessions;
  PBO across the grid (CSCV over the per-config OOS daily returns); deflated Sharpe with
  n_trials = 27 x folds + trials already logged; the share of folds where the tuned choice beat the default.
  Two trial rows (tuned, default) are appended to `data/trials.jsonl`; the tuned row carries `n_trials` so
  `trial_report.py` deflates later trials by the whole search.
- **Cost (estimated from a synthetic 94-symbol run, not yet measured on the real data):** peak memory ~1.4 GB
  for 300 symbols with 3-month blocks (~1 GB with `--block-months 1`) and roughly 30 minutes, most of it reading
  Parquet (each month is read ~5 times because of the warm-up overlap). The report records `runtime_s` and
  `peak_rss_mb`. Running the same command twice must give the same
  `input_sha256` and `events_sha256_*`.
