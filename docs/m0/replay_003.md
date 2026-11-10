# Replay 003: Variant B — quality ≥ 70 + deeper dip (pre-registered 10 Nov 2026)

**Verdict: B is killed. Filtering to high-quality names and dips of ≤ −2σ leaves too few trades (25 and 5) and
those trades lose: −0.78% on the design window (t −2.07, worse than 94% of random entries). Variant A stays the
baseline.**

Change versus A: entries only. Same exits, risk gate, costs, universe and windows as replay 002.
- Dip threshold z ≤ −2.0σ since the open (A: −1.5σ).
- Point-in-time quality score ≥ 70/100: equal-weight percentile ranks of ROA, net margin, revenue growth and cash
  quality (operating cash flow / net income), from each company's latest **original** 10-K (SEC companyfacts) filed
  **before** the trading date. Restatements are ignored. XOM has no usable tags and is never eligible; NEE and DUK
  score on two components only (no revenue tag). About 9 of 58 names score ≥ 70 on a typical day.

Both windows were run once; trials are logged in `data/trials.jsonl`.

| Metric | Design (Feb 2025 – Jun 2026) | Hold-out (Jul – Nov 2026) | A design / hold-out |
|---|---|---|---|
| Candidates after filters | 163 | 54 | — |
| Trades | 25 | 5 | 750 / 187 |
| Net return (on $100k) | **−0.78%** | **−0.11%** | +0.08% / +0.70% |
| t-stat per trade | −2.07 | −0.42 | 0.03 / 0.53 |
| Hit rate | 28% | 60% | 44.8% / 49.7% |
| Profit factor | 0.40 | 0.61 | 1.00 / 1.11 |
| Max drawdown | 0.9% | 0.2% | 2.4% / 0.9% |
| Percentile vs random (200 runs) | **5.5th** | 32.5th | 100th / 95th |
| SPY over the same window | +24.9% | −1.4% | — |
| Exits (stop / trail / time / target) | 17 / 4 / 2 / 2 | 2 / 1 / 2 / 0 | — |

**Reading it:**
- **Sample size:** 30 trades in 21 months. Even a true edge could not be proven at this rate (the bar is ≥ 900
  trades); a filter this tight can only be killed, which is what the design window does.
- **Direction:** the research hypothesis was that quality names rebound better. Here the opposite showed up: 17 of
  25 design trades hit their stop. Deep intraday dips in top-quality large caps were more often the start of a
  real move than noise. This is weak evidence (25 trades), but nothing supports B.
- **Not tuned:** no other thresholds (60, 80, −1.75σ) were tried; doing so after seeing this would be curve-fitting
  and each try would count as another trial in the deflated Sharpe.

**Next:**
- Keep A as the baseline. Its only positive signal is entry selection beating random in both windows.
- The honest next step is **M1 forward paper trading of A**, which collects out-of-sample trades at ~2/day; any
  further replay variants should be pre-registered and added to the trial count.
