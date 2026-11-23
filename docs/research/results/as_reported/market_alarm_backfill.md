# Market-event alarm: backfill and the eight comparisons (2026-11-21)

- Pre-registration `docs/prereg/market_alarm_study.md`, sha256 `73563ce46061e57af013803912c6488b2011254d34a746ca4a354318f86e0d40`; code `8b7afd083250b301e64549604ba5c7e3342bdb63`.
- Sessions 2016-01-04 to 2026-11-20: 2738. Missing inputs: none.

## Share of sessions flagged

| Flag | Sessions | Share | Unknown |
|---|---|---|---|
| `fed_day` | 86 | 3.1% | 0 |
| `cpi_day` | 128 | 4.7% | 0 |
| `megacap_report` | 303 | 11.1% | 61 |
| `index_reshuffle` | 54 | 2.0% | 0 |
| `mega_listing` | 2 | 0.1% | 0 |

| Tier | Sessions | Share |
|---|---|---|
| major | 103 | 3.8% |
| notice | 428 | 15.6% |
| none | 2207 | 80.6% |

Tightening of `major` (pre-registered, before any comparison): megacaps >= 3: 3.8%. Used: **3**.

Check 2026-11-19: tier **major**, flags mega_listing; mega listing ['ANT'].

## The eight comparisons

| Flag | Measure | Sessions | Flagged | Mean flagged | Mean other | Difference | NW t | Clear (abs t >= 3) | Trial row |
|---|---|---|---|---|---|---|---|---|---|
| `fed_day` | M1 | 2737 | 86 | 0.00768 | 0.00727 | 0.00041 | 0.49 | no | logged |
| `fed_day` | M2 | not run: no engine + SimBroker pit run | | | | | | | |
| `cpi_day` | M1 | 2737 | 128 | 0.00777 | 0.00726 | 0.00051 | 0.66 | no | logged |
| `cpi_day` | M2 | not run: no engine + SimBroker pit run | | | | | | | |
| `megacap_report` | M1 | not run: megacap_report unknown on 60 sessions | | | | | | | |
| `megacap_report` | M2 | not run: no engine + SimBroker pit run | | | | | | | |
| `index_reshuffle` | M1 | 2737 | 54 | 0.00941 | 0.00724 | 0.00217 | 1.86 | no | logged |
| `index_reshuffle` | M2 | not run: no engine + SimBroker pit run | | | | | | | |

M1 = absolute SPY close-to-close return. M2 = daily return of `-`.

## Mega listings (not tested)

| Session | What | SPY close-to-close |
|---|---|---|
| 2026-06-12 | SPCX | 0.5% |
| 2026-11-19 | ANT | -2.0% |
