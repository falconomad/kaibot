# Walk-forward, ten years: variant A (dip reversal) loses money

Run by the stakeholder on 12 Nov 2026 on the full history download (#3). Code `6510fad`,
input_sha256 `d2591757556bbe3b8fbdcd2366eacc3c56fb3839d9e7da055a5176acb52a1601`. Runtime 707 s.

```
scripts/walk_forward.py --universe config/universe_history.yaml --start 2016-01-01 --end 2026-11-06 \
  --variant A --level balanced --grid default
```

115 folds: train 12 months, test the next month, roll monthly. 27 configs (z_max -1.5/-1.75/-2.0, atr_mult 1.0/1.5/2.0,
target_r 1.5/2.0/3.0). Selection: best train daily Sharpe after costs with at least 100 train trades. 300 stocks.
Out-of-sample window 2017-05-01 to 2026-11-06 (2,395 sessions).

| Out-of-sample | Tuned each month | Default settings |
|---|---|---|
| Trades | 10,124 | 17,298 |
| Trades per month | 88.0 | 150.4 |
| Net return | -58.99% | -75.69% |
| Mean per trade | -0.87 bp | -0.80 bp |
| t-stat per trade | -8.09 | -11.17 |
| Hit rate | 44.7% | 42.4% |
| Daily Sharpe (annualised) | -1.81 | -2.47 |
| Max drawdown | 59.69% | 75.84% |
| Deflated Sharpe (3,105 trials) | 0.0 | 0.0 |

- SPY over the same sessions: +208.84%, daily Sharpe 0.73.
- Probability of backtest overfitting across the grid (CSCV, 16 blocks): 0.009. Tuned beat default in 67.8% of folds.
- Most-chosen settings were the strictest dip (z = -2.0): 85 of 115 folds.

## Reading

- The result is not "no edge": it is a **statistically clear loss after costs** (t = -8), in every tuning, over ten years.
  The earlier 17-month result (+0.08%, t = 0.03) was a flat patch inside a losing strategy.
- Tuning helps a little and picks the pickiest dips, but never turns it positive.
- Survivorship bias (today's large caps applied back to 2016) flatters the result, so the truth is no better than this.
- Variant A in this form cannot reach the go-live bar. More paper trading of it adds no information.

## Caveats (from the report)

- each month is simulated from flat equity, so the run-drawdown stop resets monthly
- FOMC blackout only where `config/macro_calendar.yaml` has dates (2025-2026)
- earnings-day filter relies on Alpaca news only; the agent room is not replayed
- fills: next-bar open at modelled bid/ask plus impact, no queue model
- a divide-by-zero warning in `replay/features.py:71` (turnover) on 2022-2023 data: check it does not change entries

## Next (Backend-quant, #109)

1. Split the loss into gross signal and trading costs, by year and by VIX level.
2. Check whether dips keep falling (the signal is backwards) or simply do not bounce enough to pay the spread.
3. Only then test the research ideas: dips only when VIX is high, dips measured against SPY and the sector, the
   high-beta group (#94, #95). Each is a trial.
