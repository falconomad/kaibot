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

## 5. Cost split and the variant A experiments (issue #109)
One command on the Mac, from the repo root (about an hour: five walk-forwards of about 12 minutes or less each):

```
./scripts/run_experiments.sh
```

- **What it runs:** `scripts/walk_forward.py --experiment <name>` for `control` (variant A, the 27-config grid of
  section 4), `mornings` (no entry after 10:00 / 10:30 / 11:00), `wide_stop` (stop 2.5 / 3 / 4 ATR, shares cut so
  the dollar risk is that of the 1.5 ATR stop), `high_vix` (entries only when the previous day's VIX close is
  above 18 / 22 / 26) and `mornings_wide_stop` (3 x 3). Same folds and selection as section 4; everything not
  named stays at A's defaults. The grids and fixed defaults are declared in
  `kaibot.replay.walkforward.EXPERIMENTS`; nothing is tuned on a test month.
- **Resumable:** an experiment with a finished report for the same dates and universe is skipped. Logs are in
  `logs/experiment_<name>.log`.
- **Output:** one table (trades, trades/month, net return, mean bp gross and net, t-stat, hit rate, Sharpe, max
  drawdown, months positive, deflated Sharpe) printed and written to `data/reports/experiments_summary.md`. Each
  run logs two trial rows; the deflated Sharpe in the table counts every configuration in `data/trials.jsonl`.
- **Cost split:** every trades CSV now also has `gross_pnl` (bought at the fill bar's open, sold at the stop or
  target level or the exit bar's close), `cost_spread`, `cost_impact`, `cost_slippage` (a stop bar that opened
  below the stop), `cost_fees` and `vix`; `pnl == gross_pnl - the four costs`. The old columns and the net
  numbers are unchanged. The report shows the totals and the mean bp per trade of each.
- **Breakdown of any run:** `.venv/bin/python scripts/analyze_trades.py data/reports/trades_<...>.csv` prints
  gross vs net and the by-year, by-exit, by-entry-time, by-VIX and by-sector tables (no network; older files
  without the cost columns work, gross shows n/a).

## 6. Multi-day rules (issue #113, decision 14)
One command on the Mac, from the repo root. Research only: it reads `data/`, never the engine or the broker.
Under decision 15 this is the first look (candidate 0): the numbers are provisional, not a verdict, and cannot
pass Gate B.

```
./scripts/run_experiments.sh multiday
```

- **What it runs:** `scripts/walk_forward_multiday.py --rule <name>` for `trend_highs`, `rel_strength`,
  `overnight_spy` and `winners_run`, the same 115 folds and selection as section 4. Rules, grids (at most 9
  configurations) and caps are declared in `kaibot.replay.multiday_rules.EXPERIMENTS`; the simulator is
  `kaibot.replay.multiday` (daily bars, entries at the next open or at the close, stops that fill at the open when
  it gaps through them, sizing on a 3 ATR gap, caps on positions and on the total held).
- **Time (estimated, not measured on the real data):** allow about 25 minutes for the first run. Building the
  daily bars from the minute bars takes about 5 to 10 minutes (1.8 s per symbol on a cloud machine) and is cached
  in `data/multiday/daily_bars.parquet`; `winners_run` replays variant A's intraday trades once (about as long as
  the loading part of one walk-forward of section 4) and caches them in `data/multiday/` too. Both caches are
  rebuilt when a month file under `data/bars_1m` changes. With the caches a run takes a few minutes.
- **Resumable:** a rule with a finished report for the same dates and universe is skipped. Logs are in
  `logs/experiment_multiday_<name>.log`.
- **Output:** one table printed and written to `data/reports/experiments_multiday_summary.md`: per rule (tuned
  monthly, and its fixed setting) trades, trades a month, average holding, net return, CAGR, gross and net bp of
  equity per trade, t-stat, hit rate, Sharpe, max drawdown, months positive, % of time invested and deflated
  Sharpe; then SPY buy-and-hold and the universe held equal-weight (rebalanced monthly, after costs) over the same
  sessions, each rule's return and Sharpe minus both, and the cost split with the gap slippage on its own. Each
  run logs two trial rows.
- **One account per row:** equity and positions are carried across months and nothing is force-closed at a month
  end; the configuration chosen for a month applies to that month's new entries.
- **Earnings:** the history has no earnings calendar, so the date of the next report cannot be known in advance.
  The run estimates it from past results headlines (`kaibot.replay.earnings`), keeps positions out of a window
  around it, measures how well that window would have worked, and switches the filter off (and says so in the
  table's notes) when it is not reliable. `--earnings off` / `on` on `walk_forward_multiday.py` forces it.
- **Read the results against the benchmarks:** the universe is today's large caps (survivorship), prices carry no
  dividends, entries fill at the next open, and the cost model is the uncalibrated one of #111.
- **Breakdown of any run:** `.venv/bin/python scripts/analyze_trades.py data/reports/trades_balanced_md_<...>.csv`
  prints gross vs net, the stops jumped by the open, and the by-year, by-exit, by-holding-period,
  by-configuration and by-sector tables.

## 7. Momentum core (issue #127, decision 15 rules 1 and 2)
One command on the Mac, from the repo root. Research only: it reads `data/`, never the engine or the broker.
Every number is **PROVISIONAL**: today's stock list (survivorship), cost model v1 and a price-only SPY. Nothing
here can pass Gate B until S71, S83 and S84 have landed and the test is rerun.

```
./scripts/run_experiments.sh core
```

- **What it runs:** `scripts/momentum_core.py --version <name>` for `trend_momentum` (Clenow's rules: weekly,
  90-day regression slope x R-squared, 100-day and 15%-move conditions, ATR sizing at 10 bp of equity, SPY
  200-day filter, exits by rank, resize every second week), `mom_12_1_band` (monthly, blend of 6 / 9 / 12-month
  returns skipping the last month, enter in the top 10%, exit below the top 20%, at most 20 names) and
  `mom_12_1_band_nofilter` (the same without the index filter). The constructions are frozen and pre-registered
  in `docs/prereg/trend_momentum.md` and `docs/prereg/mom_12_1_band.md` (committed before the code; the run
  refuses to start without them and writes their hash into the report). The rules are in
  `kaibot.replay.multiday_core`; the simulator is the one of section 6 (`kaibot.replay.multiday`), extended with
  a cost multiplier, a sizing hook, fills at the next close and partial resizes.
- **No tuning:** each configuration is ONE continuous $100k account from 2017-05 to the end of the data, starting
  flat. The grid is a set of reports of one frozen rule: fill model (base: decided after the close of T, filled
  at the close of T+1; stress: the open of T+1 with spread and impact doubled) x costs (1x, 2x, 3x; a 0x run
  to state the break-even multiple) x exposure cap (30 / 60 / 100%) x earnings (hold through; "exit before" is an
  APPROXIMATE variant on the estimated earnings window of section 6 and is skipped, with a message in the log
  and in the table's notes, when that estimate is not reliable). Then the five pre-declared neighbours of each
  rule and the matched random entries.
- **Time (measured on synthetic bars of the real size, 2,730 sessions x 300 symbols, on a busy shared 2-CPU
  cloud machine; not measured on the real data):** `trend_momentum` took 4.5 minutes of wall time (1.1 minutes of
  CPU) with the exit-before-earnings variant and 200 control runs per cap; the two band versions trade less.
  Allow about 15 minutes for the three versions with the daily-bar cache of section 6 in place (it is reused; 5
  to 10 minutes more when it has to be built); an idle machine is faster. About 70% of it is the random control:
  200 seeded runs for each of the three exposure caps of each version.
  `KAIBOT_CORE_CONTROL_RUNS=500 ./scripts/run_experiments.sh core` runs the 500 that Gate B mark 3 asks for
  (about 35 minutes; a different number of runs is a new report, not a resume). No `.env` is needed.
- **Resumable:** a version with a finished report for the same dates, universe and number of control runs is
  skipped. Logs are in `logs/experiment_core_<version>.log`.
- **Output:** ONE table printed and written to `data/reports/experiments_core_summary.md`. Per configuration:
  total return, CAGR, annualised daily Sharpe (mark-to-market) with the 50% haircut beside it, maximum drawdown
  (mark-to-market), each calendar year's return, positive years, average and maximum exposure, average number
  of names, turnover a year, cost a year in bp of equity, trades, average holding days, the share of the profit
  that came from the five best stocks, the break-even cost multiple, and the percentile of the rule's net Sharpe
  and CAGR among the matched random entries. Benchmark rows over the same sessions: SPY buy-and-hold (price
  only), SPY scaled to each version's average exposure, the equal-weight universe, and the median matched random
  account. Under the table each Gate B mark of decision 15 is printed for the base version of each rule as
  pass, fail or "cannot be judged yet (provisional data)".
- **Files:** `data/reports/core_balanced_<version>_<ts>.json` and one trades and one daily CSV per run in the
  folder of the same name. Every run is a row in `data/trials.jsonl` with its configuration; a configuration
  counts once (`n_trials` 1 for the base fill at 1x costs, 0 for its cost and fill stress reports).
- **Read it honestly:** signals and holdings use split-adjusted prices without dividends, and so do the
  benchmarks; the close is the last regular-hours minute, not the auction; at a 100% cap a purchase can be cut
  to the cash left on the fill day; the random control mirrors dates, entry counts, sizing and holding periods
  but its average exposure can differ from the rule's (it is printed beside it).
- **Breakdown of any run:** `.venv/bin/python scripts/analyze_trades.py data/reports/core_balanced_<...>/trades_<...>.csv`.
