# Kaibot v2: PM position memo, round 1

## 1. Product and success
An autonomous paper day-trading engine for liquid US stocks: fund a simulated account, pick a level, and it trades intraday, ends flat by the close, and reports results.

"More money each day" can't be the bar: luck makes green days, and 1% a day is about 1,100% a year. **Success means a real edge after costs over N = 40 trading days of paper trading:**
- **Daily P&L:** mean net return (after a slippage haircut) > 0 with the bootstrap 95% CI above 0; ≥ 55% green days; no day breaches the loss halt.
- **vs SPY:** a higher Sharpe than holding SPY over the same days.
- **vs random entry:** above the 95th percentile of 1,000 random-entry runs with the same universe, sizing and hold times.
- **vs rules-only:** the agent room beats the same strategy without LLM calls, or the room is cut.
- **Drawdown:** within the cap for the chosen level.

No live discussion until this gate passes.

## 2. Scope
**MVP**
- One strategy, the **loser hunter**: quality names (nightly EDGAR filter) that drop with their sector or the market, with Finnhub news confirming the cause is external. Long only, intraday exit.
- Universe of about 300 liquid names, 1-minute bars from Alpaca IEX.
- **Agent room:** a pre-market regime brief, plus one LLM question per candidate: "is this drop external?" Python sizes and exits.
- A deterministic risk engine. Alpaca paper fills, plus our own ledger with a slippage haircut.
- A replay harness, required before paper trading starts.
- One dashboard: equity, today's P&L, trades with reasons, baselines, status.

**Later:** Polymarket, GDELT and Reddit signals, each admitted only after proving lift in an ablation test; a full debate room; shorts; more strategies; Android push; live broker. X is out because it has no usable free API.

## 3. Onboarding and settings
- **Amount:** $1k to $100k. € input is converted once; both shown.
- **Aggressiveness** (Conservative / Balanced / Aggressive):
  - risk per trade 0.25/0.5/1%
  - max positions 3/5/8
  - max position size 10/15/20%
  - daily loss halt 1/2/3%
  - signal threshold high/medium/lower

  No level unlocks leverage, shorts or overnight holds.
- **Hours:** regular session only. No entries in the first 5 or last 15 minutes; flat by 15:55 ET.
- **User sees:** live equity, positions, each trade's rationale and transcript, a daily report.
- **User can:** Pause (no new entries), Flatten-and-stop (the kill switch), or start a New run, which archives the old one. Changing the level requires a new run, keeping stats honest.
- **The kill switch survives.** "No control" means no control over individual trades, not no off switch. The engine also self-halts on the loss limit, stale data or budget stop.

## 4. Top 5 risks
1. **No edge.** Free data plus a crowded dip-buying trade may just track beta; baselines expose this.
2. **LLM lookahead.** Claude may already know how past events played out, which flatters replays.
3. **Flattering fills.** IEX covers a few percent of volume and paper fills are optimistic.
4. **Latency and limits.** Finnhub free is about 60 calls/min and news lags minutes, so drops may be priced in.
5. **Premature go-live** after a lucky week.

## 5. Questions for engineering
1. Can we get one to two years of free, point-in-time minute bars and timestamped news? How do we stop LLM lookahead in replays?
2. Realistic event-to-order latency, and how many tickers fit the free rate limits?
3. What does the agent room cost per trading day? Does it fit under the $190 stop, and what runs when that stop hits?
