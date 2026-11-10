# v2 Round 2: Quant / Backend

## (a) Corrections
- **PDT: I was wrong.** The rule was retired on 4 Jun 2026 and paper never enforced it.
- **Wrong:** Alpaca News is free from 2015. Volume uses SIP (≥ 15 min old), never IEX.
- **Wrong:** 50 trades only justifies *killing* a strategy. A claim needs ~900 trades, t > 3.
- **Slippage:** adopt research 06's model:
  - fills at bid/ask, plus 0.25 × spread + 0.5σ√(q/V);
  - 500 ms latency; limits fill only on a trade-through.
- **Confirmed:** history to 2016; 40–50% losing days.

## (b) MVP strategies (ET, flat by 15:55)
1. **No-news dip reversal:**
   - **Universe:** EDGAR quality score ≥ 70, ADV > $50M, spread < 5 bp.
   - **Drop:** down ≥ 1.5σ since the open, with sector-beta residual ≤ 1.5σ.
   - **Turnover:** SIP volume / 20-day same-time average ≤ 1.5.
   - **No news:** no story, 8-K, rating change or earnings in 24 h; the room must call the cause macro, sector or noise.
   - **Trade:** enter on a 5-min higher low. Stop at 1.5 × ATR₅ₘ. Exit at VWAP, 1.5R or time.
   - **Off:** in CRISIS, or SPY < VWAP with first 30 min < −1%. Skip FOMC/CPI ±30 min.
2. **SPY intraday momentum (Balanced and up):** if the return from the prior close to 10:00 is above 0.5σ, buy at 15:30 and exit MOC. Long only, no LLM.
3. **Shadow only:**
   - ORB on stocks in play: about zero net, and live relative volume isn't free.
   - News continuation: the LLM edge dies at 20 bp.

## (c) Data
| Feature | Source |
|---|---|
| Price, spread, ATR, VWAP | Alpaca IEX |
| Volume, β, σ, ADV | Alpaca SIP (≥ 15 min old) |
| Quality, filings | EDGAR |
| News veto | Alpaca News, Finnhub, PRN/GNW → FinBERT → Haiku |
| Earnings | Finnhub calendar |
| Regime | Cboe VIX/VIX9D, sector ETFs |
| Event gate | FRED, BLS, Fed RSS |

**Logged only** until a 3-month ablation: Polymarket/Kalshi, GDELT, Tradestie/ApeWisdom. The exception is a `pump_risk` long-block.

## (d) Room budget
- **Per session** (3 calls × 6k in / 600 out):
  - Sonnet 5.5: $0.054, or about $0.032 cached.
  - Haiku 5.5: $0.0027.
- **60 sessions a day, 21 days:** Sonnet about $41–68/month; Haiku about $3.4.
- **Research's $10–16** is triage plus escalation; the room replaces escalation, so add $3.6 for triage.
- **Total:** $45–72/month, under $120.
- **Daily cap:** worst-case reservation is about $0.067 a session, so $3/day allows 45 sessions, then rules-only vetoes.
