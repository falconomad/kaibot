# Replay 002: Variant A exits (pre-registered 10 Nov 2026)

**Verdict: Variant A turns −4% into roughly break-even (+0.1% on the design window, +0.7% on the hold-out).
It is still no evidence of an edge (t = 0.03 and 0.53, against a bar of 3). Entry selection beats random
in both windows, so A becomes the new baseline. It does not justify going live.**

Change versus v1: exits only. Fixed 2R target with no VWAP cap. Once a bar closes ≥ +1R, the stop moves to breakeven and then trails 1.5 ATR below the highest close. Time exit at 15:55. Entries, risk gate, costs and universe are unchanged. Both windows were run once, and the trial is logged.

| Metric | Design (Feb 2025 – Jun 2026) | Hold-out (Jul – Nov 2026) |
|---|---|---|
| Trades | 750 | 187 |
| Net return (on $100k) | **+0.08%** | **+0.70%** |
| t-stat per trade | 0.03 | 0.53 |
| Hit rate | 44.8% | 49.7% |
| Profit factor | 1.00 | 1.11 |
| Daily Sharpe (annualised) | 0.04 | 0.75 |
| Max drawdown | 2.4% | 0.9% |
| SPY over the same window | +24.9% (Sharpe 1.00) | −1.4% (Sharpe −0.23) |
| Random entries (mean, 200 runs) | −$7,946 | −$1,038 |
| Percentile vs random | **100th** | **95th** |
| Exits (stop / trail / time / target) | 371 / 140 / 130 / 109 | 76 / 40 / 51 / 20 |

**Reading it:**
- **Exits:** v1's VWAP-capped target was the main problem; letting winners run fixes the asymmetry.
- **Selection:** consistently better than random (100th and 95th percentile), which is the one real signal so far.
- **Against SPY:** a flat strategy loses badly to buy-and-hold in a +25% market, but beat SPY in the hold-out's −1.4%. That is a single short period, not evidence.
- **Against the go-live bar:** far from it. t is about 0.5 versus 3, and a positive deflated Sharpe needs much more.

**Next:**
- **Variant B:** the point-in-time quality filter plus a deeper dip (≤ −2σ). It needs `SEC_USER_AGENT`.
- **M1:** forward paper trading of Variant A, building the real evidence clock.
