# Index-fund cost calibration (cost model v2, ticket 2.3)

Measured 2026-11-23T12:28:12+00:00 with `scripts/calibrate_index_costs.py` at code `a29d833`. Read-only: 260 GET requests, feed `sip`. Fills: the paper account's, last 90 days. Quote sample: the rule's check times on the last 20 sessions.

The paper venue fills at the quote with no queue and no impact. What a fill paid beyond the quote is shown and is not used for the bucket.

| Symbol | Source | Rows | Sessions | Window | Mean price | Quoted half-spread, bp: mean | median | 90th pct | max |
|---|---|---|---|---|---|---|---|---|---|
| SPY | at the account's fills | 0 | 0 | - to - | - | - | - | - | - |
| SPY | at the check times | 260 | 20 | 2026-10-26 to 2026-11-20 | 749.72 | 0.174 | 0.136 | 0.274 | 1.418 |

| Symbol | Source | First half hour | Midday | Last half hour |
|---|---|---|---|---|
| SPY | fills | - | - | - |
| SPY | check times | - | 0.174 (220) | 0.170 (40) |

Mean quoted half-spread in bp (rows).

## The bucket value

- SPY: **0.20 bp a side** (the mean quoted half-spread from quotes, rounded up to the next 0.05 bp). Cost model v2 charges 1.75 bp today.
