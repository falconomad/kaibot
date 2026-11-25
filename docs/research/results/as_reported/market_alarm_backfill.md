# Market-event alarm: backfill and the eight comparisons (2026-11-23)

- Pre-registration `docs/prereg/market_alarm_study.md`, sha256 `73563ce46061e57af013803912c6488b2011254d34a746ca4a354318f86e0d40`; code `0743ef000fe87e34658be82f7416d7e8139c4302`.
- Sessions 2016-01-04 to 2024-12-31: 2264. Missing inputs: none.

## Share of sessions flagged

| Flag | Sessions | Share | Unknown |
|---|---|---|---|
| `fed_day` | 71 | 3.1% | 0 |
| `cpi_day` | 106 | 4.7% | 0 |
| `megacap_report` | 250 | 11.0% | 61 |
| `index_reshuffle` | 45 | 2.0% | 0 |
| `mega_listing` | 0 | 0.0% | 0 |

| Tier | Sessions | Share |
|---|---|---|
| major | 84 | 3.7% |
| notice | 346 | 15.3% |
| none | 1834 | 81.0% |

Tightening of `major` (pre-registered, before any comparison): megacaps >= 3: 3.7%. Used: **3**.

Check 2026-11-19: tier **major**, flags mega_listing; mega listing ['ANT'].

## The eight comparisons

| Flag | Measure | Sessions | Flagged | Mean flagged | Mean other | Difference | NW t | Clear (abs t >= 3) | Trial row |
|---|---|---|---|---|---|---|---|---|---|
| `fed_day` | M1 | 2263 | 71 | 0.00818 | 0.00728 | 0.00090 | 0.94 | no | already logged: not logged again |
| `fed_day` | M2 | 1931 | 61 | -0.00041 | 0.00041 | -0.00082 | -0.59 | no | logged |
| `cpi_day` | M1 | 2263 | 106 | 0.00778 | 0.00729 | 0.00049 | 0.57 | no | already logged: not logged again |
| `cpi_day` | M2 | 1931 | 91 | 0.00063 | 0.00037 | 0.00026 | 0.29 | no | logged |
| `megacap_report` | M1 | not run: megacap_report unknown on 60 sessions | | | | | | | |
| `megacap_report` | M2 | 1931 | 217 | -0.00082 | 0.00053 | -0.00135 | -1.96 | no | logged |
| `index_reshuffle` | M1 | 2263 | 45 | 0.01008 | 0.00726 | 0.00283 | 2.10 | no | already logged: not logged again |
| `index_reshuffle` | M2 | 1931 | 39 | -0.00191 | 0.00043 | -0.00234 | -1.82 | no | logged |

M1 = absolute SPY close-to-close return. M2 = daily return of `reports/engine_balanced_trend_momentum_pit_20261123T130025/daily_trend_momentum_base_1x_cap100_hold.csv`.

## Mega listings (not tested)

| Session | What | SPY close-to-close |
|---|---|---|
| - | none | |
