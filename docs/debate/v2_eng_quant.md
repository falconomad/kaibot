# v2 feasibility: Quant / Backend input

Real-time data is IEX only (1-minute bars and quotes, no NBBO, no depth), and free news lags by minutes. **Any edge must come from holding minutes to hours and from selectivity, not speed.**

## 1. Strategy families

| Strategy | Signal | Hold | Why | Fails when |
|---|---|---|---|---|
| **Regime filter** | SPY vs VWAP and 20-day MA; 30-min realised volatility; breadth | n/a | Chooses reversal (calm tape) or breakout (trending tape) | Turns; it lags |
| **Loser hunter** | Price > $5, ADV > $20M, EDGAR quality. Down ≥ 2 ATR, mostly explained by SPY/sector beta, no company news in 24 h. Enter on a 5-min higher low or VWAP reclaim; stop below the day's low. | 15 min to close | Non-fundamental selling overshoots | Trend-down days, news we missed, pre-earnings |
| **Opening-range breakout** | Break of the 15-min range, IEX relative volume ≥ 2, regime up. Stop at mid-range; target 2R or close. | 30 min to close | Informed flow in stocks in play | Chop, false breaks, noisy IEX volume |
| **News continuation** | Material 8-K or wire item, price above VWAP, relative volume ≥ 3. Enter on the first pullback that holds VWAP. | To close | Post-news drift | Already priced in; LLM misclassifies |

Reddit, X, GDELT and Polymarket start as logged features only.

## 2. Pipeline (one cycle per 1-minute bar)
1. **Python features** (< 2 s).
2. **Deterministic 0–1 score per strategy**, then the top N candidates.
3. **Agent room** (async, event-triggered, cached). It outputs categories with sources: the cause of a drop (fundamental, news, macro or noise), how material news is, and a veto with a reason. **It never sets entry, stop, size or exit; it can only veto or downgrade.**
4. **Risk gate** (< 100 ms), then a bracket order (< 0.5 s).

**Budget:** a decision within 10 s of bar close. $200/month ≈ $9/day, so small models, capped at about 300 calls a day.

**Aggressiveness:** maps to risk per trade (0.25 / 0.5 / 1%), maximum concurrent positions and a daily stop (1 / 2 / 3%).

## 3. Paper realism
Alpaca paper fills at the quote with no queue or impact, so it is optimistic. Use it for plumbing; our simulator is the shadow ledger:
- Market orders fill at the next bar's open, plus ½ IEX spread + 0.1 × the 1-minute σ.
- Limit orders fill only when price trades *through* them by a tick.
- Sells pay regulatory fees.

**Report the worse P&L.**

**PDT:** under $25k, a US margin account allows 3 day trades per 5 days, and Alpaca paper enforces it. A $5,000 day-trader may not run live as briefed. Verify: FINRA was revising the rule.

## 4. Validation
- **Free history:** Alpaca 1-minute bars back to about 2016; EDGAR with acceptance timestamps; GDELT back to 2015. Finnhub free news covers only about 1 year, so the news strategy has a thin test. Survivorship bias applies.
- **Walk-forward:** train 12 months, test 3, roll. At most 3 parameters per strategy, every trial counted, Sharpe deflated.
- **Forward paper:** 4–8 weeks on frozen code, and **at least 50 trades per strategy** before any claim.
- **Kill criteria:**
  - Expectancy after costs ≤ 0 over 50+ trades: kill.
  - Drawdown > 2× the backtest maximum: suspend.
  - Slippage > 2× the model: recalibrate.
  - Daily stop hit 3 times in 10 days: halt.
  - The LLM veto shows no lift against a shadow run without it: remove it.

## 5. Honest expectations
- "Up every day" is impossible: honest strategies lose on 40–50% of days. A steady 0.5–2% a day compounds to roughly 250–14,000% a year.
- Most retail intraday strategies lose money after costs.
- The likely first result is break-even to slightly negative. Calling anything positive takes months of forward evidence.
- The agent room helps with explanation and vetoes, not with prediction.
- We can promise hard risk limits, an audit trail and fast discovery of what fails.
