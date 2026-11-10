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

## 3. Overfitting checks (story S08)
Every replay appends to `data/trials.jsonl` and stores its daily returns in `data/trials/<trial_id>.csv`.
`scripts/trial_report.py` computes the deflated Sharpe of each trial (deflated by the number of trials and the
spread of their Sharpes) and, for each window with two or more trials, the probability of backtest overfitting
(CSCV, 16 blocks = 12,870 splits). PBO near 0.5 means picking the best variant on that window was luck.
