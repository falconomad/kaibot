# Alt-data ablation for admission (story S39)

PRD v2: alt data (FINRA short volume, Form 4 insider buying, GDELT tone; later Polymarket, Kalshi, ApeWisdom,
Tradestie, Wikipedia) is **logged** from M1 and **traded only after a 3-month ablation**. The one exception is
`pump_risk`, which already blocks longs and reads no alt data. This page is the procedure for that ablation.

**Passing never turns anything on.** An `admit` verdict only PROPOSES the feature for `rules_extra`. Enabling it
takes a separate pull request that adds the filter to the rules, ships with its own replay that logs a `trial`
row (CLAUDE.md), and is reviewed like any other strategy change. Nothing in the engine reads `data/ablation/`,
and `kaibot.features.alt` stays unreachable from trading code (`tests/unit/test_alt_isolation.py`).

## What is tested
`config/ablation.yaml` holds pre-registered rules. Each one is a keep filter on one point-in-time feature from
`kaibot.features.alt.alt_features` (dotted path), e.g. `short_volume.svr_rel <= 1.5`: keep the trade unless
off-exchange short volume spiked. Write the rule and its hypothesis **before** looking at results. The file's
sha256 goes into every result and trial row, so a rule edited after seeing data shows up as a new trial.

For each rule, `src/kaibot/evaluation/ablation.py` (pure, injectable feature lookup):

1. reads the feature for each trade **as of its decision** (`available_ts <= decision_ts`; the decision is set
   to entry minus `--decision-lag-min`, 1 minute by default);
2. keeps the trades that pass, **plus every trade whose value is unknown** (missing data never removes a trade);
   coverage = share of trades with a known value;
3. computes kept mean R, t-stat and total R (R = pnl / risk);
4. draws 1,000 random subsets of the same size from all trades (numpy, seeded from `seed` in the config,
   vectorised, deterministic). percentile = `random_entry_percentile(kept total R, random totals)`.

| Verdict | When |
|---|---|
| `insufficient` | fewer than **63 distinct trading days** with a known feature value, or fewer than **100 trades** with a known value |
| `admit` | percentile **>= 95** AND kept mean R > all-trades mean R |
| `reject` | otherwise |

The thresholds are constants in the module; the config cannot loosen them. If the feature carries no information,
the kept set is just a random subset, so one rule is falsely admitted about 5% of the time (a test checks this
over 200 seeds). With several rules, a false admit *somewhere* is likely. So each row also reports `p_value` and
an informational `bonferroni_pass`, and the admission PR's own replay trial is the real check.

## Running it
```
python scripts/run_ablation.py --trades data/reports/trades_balanced_<tag>.csv           # a replay's trades
python scripts/run_ablation.py --trades data/reports/shadow_trades_balanced_<tag>.csv    # a shadow replay
python scripts/run_ablation.py --supabase --env ~/Projects/kaibot-secrets/.env [--run-id N] [--strategy dip_reversal]
```
- `--supabase` reads closed rows of `kaibot.trade` with read-only selects via `kaibot.engine.store.Store`
  (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY` or `SUPABASE_SERVICE_ROLE_KEY`; the key is never printed).
- Alt data is read from `--alt-root` (default `--data`, i.e. `data/alt/<source>/...`, see `alt_data.md`). Each
  source is read once per process and then filtered point in time per trade.
- Output: `data/ablation/<UTC date>.json` (thresholds, config sha256, git sha, per-rule table) and one row in
  `data/trials.jsonl` with `kind: "ablation"`. Its `n_trials` is the number of rules actually tested (not
  `insufficient`), so tested filters count against the deflated Sharpe in `scripts/trial_report.py`.
- With no alt history yet, every rule reports `insufficient`. That is expected until about 3 months after the
  collectors started.

## Admission checklist (a human does this)
1. The ablation JSON shows `admit` for the rule on >= 63 trading days and >= 100 known trades.
2. Open a PR that adds the filter to `rules_extra`. Link the ablation JSON and its config sha256.
3. The PR runs a replay with the filter and logs a trial. Check the DSR / PBO in `scripts/trial_report.py`.
4. Review and merge outside 09:30-16:00 ET. Until that PR merges, trading is unchanged.
