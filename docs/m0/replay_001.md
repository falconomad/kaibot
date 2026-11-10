# Replay 001: no-news dip reversal, rules-only (10 Nov 2026)

**Verdict: no edge. The pre-registered v1 rules lose money after costs, and gross of costs they are about flat.**
By the PRD kill criteria (expectancy <= 0 over 50+ trades), v1 as specified is killed. The selection logic
does carry information relative to random entries, so the next step is a pre-registered variant tested on
held-out data, not tuning on this data.

## Setup
- **Universe:** 59 liquid US large caps, with 11 sector ETFs for the beta residual. Today's list, so survivorship-biased (optimistic).
- **Data:** Alpaca 1-minute SIP bars and Benzinga news, 1 Feb 2025 to 6 Nov 2026 (445 sessions).
- **Rules:** frozen before the run (research 06 / quant memo), so the whole period is out-of-sample:
  - dip ≤ −1.5σ since the open, sector residual within ±1.5σ, turnover ≤ 1.5×;
  - no news in the prior 24 h, and a 5-minute higher low;
  - regime off when SPY < VWAP with its first 30 minutes below −1%, or VIX > 35; FOMC blackout;
  - VIX > 25 halves the size; minimum reward 0.75R.
- **Trade:** stop at 1.5 × ATR(5m); target at min(VWAP, 1.5R); time exit 15:55.
- **Risk:** Balanced level through the real risk gate, on $100k paper equity.
- **Costs:** fill at the next bar's open at the bid/ask (half-spread ≥ 1 bp), plus impact and SEC/TAF fees; the stop wins same-bar ties; targets fill only when the price trades through them.

## Results
| Metric | Strategy (net) | Strategy (gross, diagnostic) | Random entries, same sizing and exits | SPY buy-and-hold |
|---|---|---|---|---|
| Return over period | **−4.0%** | −0.7% | −7.1% mean (net, 200 runs) | +25.9% |
| Trades | 642 | 641 | same count per day | — |
| Avg R per trade | −0.136 | −0.018 | — | — |
| t-stat per trade | −2.38 | −0.41 | — | — |
| Hit rate | 40.8% | 43.7% | — | — |
| Percentile vs random | **97th** | 98th | — | — |
| Daily Sharpe (annualised) | −1.46 | — | — | 0.86 |
| Max drawdown | 5.2% | — | — | — |

| Exit | Trades | Avg R |
|---|---|---|
| Stop | 359 | −1.08 (slippage beyond the stop ≈ 0.08R) |
| Target | 223 | +1.30 |
| Time | 60 | +0.19 |

| Period | Trades | Avg R | Hit rate |
|---|---|---|---|
| 2025 H1 | 93 | −0.32 | 31% |
| 2025 H2 | 223 | −0.12 | 43% |
| 2026 to date | 326 | −0.09 | 42% |

## What it means
1. **Costs are about 0.12R per trade** (≈ $5 on a $14.5k position, ≈ 3.5 bp round trip). With a gross edge of about 0, that is the whole loss, as research 06 predicted.
2. **The entry filter is better than random** (97th percentile), but the bracket structure loses on its own: random entries with the same stop and target lose more. A target capped at VWAP cuts winners short, while the stops run their full size.
3. **The loss is consistent across sub-periods**, so it isn't one bad stretch.

## Next (pre-registered before looking at more data)
- **Hold-out:** sessions after 30 Jun 2026 are frozen as the test set for any variant. Variants are designed on Feb 2025 – Jun 2026 only.
- **Variant A (exits):** a trailing exit instead of the VWAP cap; target 2R; stop 1.5 ATR.
- **Variant B (selection):** add the point-in-time quality filter (needs `SEC_USER_AGENT`) and a deeper dip (≤ −2σ).
- Each variant is one logged trial; the deflated Sharpe accounts for all of them.
- **The agent room** is judged forward in paper trading only, never in replay.

Trial log: `data/trials.jsonl` (3 rows: the pipeline smoke test, this run, and the zero-cost diagnostic).
