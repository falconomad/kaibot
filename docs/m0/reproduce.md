# Reproducing and extending the M0 replays

## 1. History (story S03)
`./scripts/fetch_history.sh` on the Mac downloads 1-minute SIP bars (09:30–16:00 ET only), Benzinga headlines and
SEC fundamentals for the 300 symbols in `config/universe_history.yaml`, from Jan 2016 to now. It is resumable:
Ctrl-C and run it again. `./scripts/fetch_history.sh --status` shows progress. It uses 135 of Alpaca's 200
requests/min (60 while the engine runs). Expect roughly 12–18 hours and about 5 GB under `data/`.

Survivorship bias: the universe is today's large caps applied back to 2016. Companies delisted before Nov 2026
are missing, so long replays on it are optimistic. M1 replaces it with a point-in-time universe.

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

### Rerun with feed and fill options (issue #75)
`docs/m0/replay_004.md` holds the fill assumptions and the one command that reruns the five windows above on SIP
bars (and on IEX bars once an IEX-feed download exists) with the default and the stricter fills, and writes the
side-by-side table. `scripts/run_replay.py` takes the same options one replay at a time: `--feed sip|iex`,
`--bars-data DIR`, `--fills realistic`, `--slippage-bps N`. Without them a replay is unchanged.

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
