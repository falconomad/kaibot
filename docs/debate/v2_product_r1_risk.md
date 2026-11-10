# v2 Round 1: Risk & Compliance memo (autonomous paper day-trading)

## 1. No human in the loop
The approval click was the last line of defence. Now the deterministic gate is the only one, so it must get stricter. "No user control" covers trade selection; the user can still always stop the engine.

Automatic and non-negotiable, even in paper:
- **Python gate on every order;** size comes from stop distance and the per-trade risk cap. No LLM number reaches an order.
- **Daily loss stop:** flatten and halt for the day; plus a drawdown halt for the period.
- **Circuit breakers:** pause after N straight losses; caps on trades per hour; cooldown before re-entering a symbol.
- **Staleness halt:** an old quote or a dead feed blocks new entries; exits still run.
- **LLM-budget stop:** fall back to rules-only or halt.
- **Kill switch:** one click flattens and halts. Reconcile after every fill; any drift halts trading.
- **Aggressiveness levels** are presets inside fixed hard ceilings.

Why paper discipline matters: the paper record is the evidence for going live. Looser limits, optimistic fills or after-the-fact tuning make that evidence false. Paper must run the live code path and limits.

## 2. Model risk
- **Hallucinated signals:** the agent "room" can agree on invented catalysts, a misread headline, or one wire story reposted ten times. Agents choose only among candidates Python produced, each with a source and timestamp.
- **Backtest illusions:** look-ahead and survivorship bias, tuning to one period. "Loser hunter" is mean reversion: it works until the market keeps falling. Require walk-forward and out-of-sample tests, and log every variant tried.
- **Optimistic paper fills:** simulated fills typically skip slippage, partial fills and market impact. Haircut every fill by a modelled cost and report P&L before and after.
- **"Only more money":** rewards winning small and often while hiding the rare large loss, through martingale sizing, averaging down, skipping stops, over-trading. Ban adding to losers and upsizing after losses in code. Show max drawdown and worst day next to P&L.

## 3. Data and terms
- **X:** scraping breaches its terms and the free API is very limited. **Reddit:** API terms restrict automated and commercial use. **Yahoo:** unofficial, unlicensed endpoints that break without warning. Treat all three as off-limits unless the terms allow the use.
- **Finnhub and other free tiers:** rate limits and attribution; budget calls per minute. **Prediction markets:** being able to read their data doesn't mean you're eligible to trade on them; check each one.
- **Outages:** every source gets a health check, a timeout and a fallback. A dead feed halts trading; its signal must not drop out silently.
-
## 4. Future-live (flag only)
- **US pattern day trader rules:** minimum-equity requirements for frequent day trading in margin accounts, with FINRA changes proposed. Build a day-trade counter now. **Cash accounts:** settlement violations.
- **Taxes:** EU residency, the W-8BEN form, FX gains, how frequent trading is taxed; wash-sale rules apply mainly to US taxpayers. Get local tax advice and keep a trade ledger in € and $.
- **Broker terms:** eligibility for non-US residents, rules on automation and API use, market-data licensing.
- **Live keys** never in the repo; switching to live is a manual, audited step.

## 5. Hills I'll fight on
1. **The kill switch and daily loss stop are automatic, whatever "no control" means.**
2. **LLMs pick among candidates; Python sizes, prices and submits.** This is the bloodbath lesson, and it matters more with no approval step.
3. **Going live needs a bar set in advance:** a minimum number of trades, out-of-sample results, cost-haircut fills, drawdown under the limit, beating buy-and-hold. "Paper made money" isn't that bar.
