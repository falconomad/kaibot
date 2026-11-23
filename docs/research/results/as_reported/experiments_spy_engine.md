# Gate B go/no-go: `spy_noise_band` through the engine with SimBroker (issue #202)

**engine + SimBroker + SimClock (decision 15): the frozen rule as an engine strategy, every order through the risk gate and SimBroker, cost model v2 for the go/no-go.** Out-of-sample 2017-05-01 to 2026-11-06 (2395 sessions), level balanced, one continuous $100,000 account per row. Pre-registration `docs/prereg/spy_noise_band.md` sha256 `054b6baf502b` (unchanged), config/gate_v2.yaml sha256 `1951c0e41b6d`. VIX previous close from data/inputs/fred_vixcls.json.

## Decision 15 Gate B on cost model v2: **FAIL**

| Mark | Result | Why |
|---|---|---|
| 1. net positive overall and in >= 6 of 10 calendar years (section 10 part-years) | FAIL | net return is not positive; 4 positive years, 6 needed |
| 2. CAGR >= SPY - 2 pts with DD <= 0.75 x SPY's, or >= SPY + 1 pt with <= 1.1 x | FAIL | neither branch holds |
| 3. net Sharpe >= 90th percentile of >= 500 random-timing runs (section 10) | pass | - |
| 4. marks 1-2 at 2x costs | FAIL | net return is not positive; 2 positive years, 6 needed; neither branch holds |
| 5. point-in-time (SPY: the run itself, marks 1-4) | FAIL | marks 1 to 4 do not all hold on the point-in-time universe |
| 6. every pre-declared neighbour passes mark 1 (v2) | FAIL | lb10: net return is not positive; 4 positive years, 6 needed; lb20: net return is not positive; 2 positive years, 6 needed; lb30: net return is not positive; 3 positive years, 6 needed; check15: net return is not positive; 3 positive years, 6 needed; exit1555: net return is not positive; 4 positive years, 6 needed |

Rule CAGR -0.08%, max drawdown 1.82%, Sharpe -0.17; benchmark spy_total_return CAGR 14.37%, max drawdown 33.78%.

**Size (read before the marks):** this is the account the risk gate allows, not the prereg's 1x account: 13.31% of equity on average when long (at most 14.98%; the level's per-position cap, at most 20% under `HARD_CEILINGS`). Marks 2 and 4 (CAGR and drawdown against SPY) judge this partly invested account, so a fail there says nothing about the rule at 1x; mark 1 per year and per-trip numbers do not depend on size beyond rounding to whole shares.

## The pre-registration's marks

As written (0.5 bp a side; mark 2 at 1 bp), from the engine's round trips:

- Mark 1 (at least +1.5 bp net per round trip with t >= 2 (0.5 bp a side)): **pass**. 2.7 bp, t 2.31, 1110 round trips.
- Mark 2 (total net return positive at 1 bp a side): **pass**. 1.85%.
- Mark 3 (at least 7 of the 10 calendar years 2017-2026 positive): **pass**. 7 of 10.
- Mark 4 (maximum drawdown under 15%): **pass**. 0.65%.
- Mark 5 (lb10, lb20, lb30 each >= +0.75 bp net per round trip and a positive total return): **pass**. lb10 2.51 bp, 3.17%; lb20 2.64 bp, 3.01%; lb30 3.19 bp, 3.18%.
- Mark 3 (amendment 1) (net Sharpe at or above the 90th percentile of >= 500 random-timing accounts): **pass**. engine run at the 100.0th percentile, its trips replayed through the control at the 100.0th (lower counts) of 500 random-timing runs at 0.5 bp a side (random median Sharpe -0.18, 90th 0.23; engine 0.78).

The same marks with cost model v2 in place of the fixed scenarios (mark 2 on v2 too):

- Mark 1 (at least +1.5 bp net per round trip with t >= 2 (0.5 bp a side)): **fail**. -0.01 bp, t -0.01, 1109 round trips.
- Mark 2 (total net return positive at 1 bp a side): **fail**. -0.72%.
- Mark 3 (at least 7 of the 10 calendar years 2017-2026 positive): **fail**. 4 of 10.
- Mark 4 (maximum drawdown under 15%): **pass**. 1.82%.
- Mark 5 (lb10, lb20, lb30 each >= +0.75 bp net per round trip and a positive total return): **fail**. lb10 -0.2 bp, -0.89%; lb20 -0.05 bp, -0.9%; lb30 0.48 bp, -0.62%.
- Mark 3 (amendment 1) (net Sharpe at or above the 90th percentile of >= 500 random-timing accounts): **pass**. engine run at the 98.2th percentile, its trips replayed through the control at the 99.4th (lower counts) of 500 random-timing runs at 1.86 bp a side (random median Sharpe -0.85, 90th -0.42; engine -0.17).

## Engine against the research harness

| | Harness 0.5 bp | Engine 0.5 bp | Harness 1 bp | Engine 1 bp | Harness model v1 | Engine v2 |
|---|---|---|---|---|---|---|
| Round trips | 1119 | 1110 | 1119 | 1110 | 1119 | 1109 |
| Net bp / trip | 2.48 | 2.7 | 1.48 | 1.7 | 0.06 | -0.01 |
| t | 2.12 | 2.31 | 1.26 | 1.46 | 0.05 | -0.01 |
| Gross bp / trip | 3.48 | 3.7 | 3.48 | 3.7 | 3.48 | 3.71 |
| Hit rate | 0.424 | 0.424 | 0.408 | 0.408 | 0.38 | 0.378 |
| Total return % | 30.87 | 3.33 | 17.01 | 1.85 | -0.19 | -0.72 |
| CAGR % | 2.87 | 0.35 | 1.67 | 0.19 | -0.02 | -0.08 |
| Sharpe | 0.71 | 0.78 | 0.43 | 0.43 | 0.02 | -0.17 |
| Max drawdown % | 5.35 | 0.65 | 7.98 | 1.01 | 11.62 | 1.82 |
| Positive years | 8 | 7 | 5 | 5 | 4 | 4 |
| Days invested % | 33.0 | 32.9 | 33.0 | 32.9 | 33.0 | 32.9 |
| Mean position % of equity | 100 (1x) | 13.21 | 100 | 13.25 | 100 | 13.31 |
| All-in cost bp a side (measured) | 0.5 | 0.5 | 1 | 1.0 | model v1 | 1.86 |

Harness source: harness report `spy_balanced_spy_noise_band_20261113T084923.json`. Per-trip numbers do not depend on size; the engine's account is the one the risk gate allows (per-position cap of the level, VIX halving, whole shares), so its CAGR and drawdown are about that share of the harness's 1x account.

Round trips at 0.5 bp, one by one against the harness's signals: 1106 in both (same entry minute; 1106 also with the same exit), 13 only in the harness (other (the pause after losses, an earlier stop or exit in the session): 6, event blackout (+-30 min of a scheduled release): 7), 4 only in the engine.

## Every engine run

| Run | Round trips | Net bp / trip | t | Gross bp / trip | Cost bp a side | Total return % | CAGR % | Sharpe | Max DD % | Positive years | Exits | Halts | Engine errors | Runtime s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spy_noise_band|v2 | 1109 | -0.01 | -0.01 | 3.71 | 1.86 | -0.72 | -0.08 | -0.17 | 1.82 | 4 of 10 | {'band_vwap': 717, 'close': 392} | - | 0 | 146.5 |
| spy_noise_band|0.5 | 1110 | 2.7 | 2.31 | 3.7 | 0.5 | 3.33 | 0.35 | 0.78 | 0.65 | 7 of 10 | {'band_vwap': 718, 'close': 392} | - | 0 | 144.1 |
| spy_noise_band|1 | 1110 | 1.7 | 1.46 | 3.7 | 1.0 | 1.85 | 0.19 | 0.43 | 1.01 | 5 of 10 | {'band_vwap': 718, 'close': 392} | - | 0 | 146.8 |
| spy_noise_band~lb10|v2 | 1125 | -0.2 | -0.18 | 3.52 | 1.86 | -0.89 | -0.09 | -0.21 | 1.89 | 4 of 10 | {'band_vwap': 726, 'close': 399} | - | 0 | 145.5 |
| spy_noise_band~lb20|v2 | 1082 | -0.05 | -0.04 | 3.67 | 1.86 | -0.9 | -0.1 | -0.22 | 1.82 | 2 of 10 | {'band_vwap': 689, 'close': 393} | - | 0 | 145.8 |
| spy_noise_band~lb30|v2 | 1053 | 0.48 | 0.34 | 4.2 | 1.86 | -0.62 | -0.06 | -0.13 | 1.7 | 3 of 10 | {'band_vwap': 662, 'close': 391} | - | 0 | 149.5 |
| spy_noise_band~check15|v2 | 1457 | -0.74 | -0.81 | 2.98 | 1.86 | -1.57 | -0.17 | -0.36 | 1.94 | 3 of 10 | {'band_vwap': 1064, 'close': 392, 'stop': 1} | - | 0 | 156.4 |
| spy_noise_band~exit1555|v2 | 1109 | 0.38 | 0.33 | 4.1 | 1.86 | -0.29 | -0.03 | -0.07 | 1.62 | 4 of 10 | {'band_vwap': 717, 'exit1555': 392} | - | 0 | 150.5 |
| spy_noise_band~lb10|0.5 | 1126 | 2.51 | 2.2 | 3.51 | 0.5 | 3.17 | 0.33 | 0.74 | 0.84 | 7 of 10 | {'band_vwap': 727, 'close': 399} | - | 0 | 150.3 |
| spy_noise_band~lb20|0.5 | 1084 | 2.64 | 2.19 | 3.64 | 0.5 | 3.01 | 0.31 | 0.73 | 0.56 | 7 of 10 | {'band_vwap': 691, 'close': 393} | - | 0 | 150.7 |
| spy_noise_band~lb30|0.5 | 1054 | 3.19 | 2.24 | 4.19 | 0.5 | 3.18 | 0.33 | 0.7 | 0.6 | 9 of 10 | {'band_vwap': 663, 'close': 391} | - | 0 | 114.2 |

Report `spy_engine_balanced_20261121T172654.json`; code `8b7afd0`; runtime 422.2 s. Trial rows: n_trials 0 (reports of configurations the harness counted).
