# v2 Round 2: Risk & Compliance

## Concede
- **PDT:** research 06 is right and my round 1 point is out of date. The rewrite took effect on 4 Jun 2026, so I drop the day-trade counter. For live trading the risk is now intraday margin calls; we avoid it by capping buying power at the ledger amount, with no leverage.
- **PM:** LLM lookahead in replays is a real risk; replays use only point-in-time inputs.
- **Architect:** fail closed, reserve LLM cost before each call, keep managing exits at the budget stop. Agreed.

## Push back
- **Research 06, shorts on Aggressive:** no in v1. Losses are unbounded, borrow isn't modelled, and fractional shares can't be shorted.
- **Research 06, 4% open risk on Aggressive:** that exceeds the 3% daily stop, so one correlated gap blows through it.
- **PM, 40 days to go live:** too few. Use research 06's bar: at least 900 trades over at least 3 months, deflated Sharpe, beating random entry. **Quant:** 50 trades can kill a strategy, not prove one.
- **Advocate, Flatten all as a separate button:** if the engine keeps running, flattening just leads to re-entries.

## Final numbers (Conservative / Balanced / Aggressive)
- **Risk per trade:** 0.25 / 0.5 / 1%
- **Daily stop, flatten and halt:** −0.75 / −1.5 / −3%. That is 3R at every level, which is why I take research 06 over the PM's −1/−2/−3.
- **Open risk:** at most the room left before the daily stop.
- **Max position / concurrent positions:** 10% / 3, 15% / 5, 20% / 8
- **New entries per day:** at most 10 / 20 / 30
- **Shorts, leverage, overnight holds:** none at any level

## Automatic halts
- **3 straight losses:** 60-minute pause.
- **Daily stop hit 3 times in 10 days:** halt pending review.
- **Drawdown of 4 / 8 / 12% from peak:** the run ends.
- **Quotes stale more than 5 s:** no entries.
- **Every entry is a bracket order held at the broker,** so stops survive a crash. A missed watchdog heartbeat means flatten.
- **LLM budget:** at $190, no new entries.
- **Reconciliation drift:** halt.

## Flatten all
It belongs to the user, as **Flatten-and-stop**: one confirmation, close everything, halt. Pause stays separate. Manual interventions are tagged in the stats.
