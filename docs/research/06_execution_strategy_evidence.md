# 06 — Paper execution options and strategy evidence

*Quant research, 10 Nov 2026.*

## TL;DR
- **Execution:** use **Alpaca paper** behind our own `BrokerAdapter`, and re-price every fill with a **conservative shadow cost model**. The dashboard shows the conservative P&L. Going live later means changing keys and base URL, not code.
- **PDT:** the $25k rule ended 4 Jun 2026. It never applied to paper.
- **Strategies:** the edges are real but small and mostly eaten by costs. Buying losers works only for **no-news** drops; **news** drops keep going. Expect a few bp a day at best.

## A) Free paper-execution options

| Option | Access | Fill realism | Verdict |
|---|---|---|---|
| **Alpaca paper** | Free email-only account, worldwide, no funding, $100k simulated [1] | Real-time NBBO fills. Limit orders fill when marketable. ~10% random partial fills. Order size **not** checked against quoted size. **No** slippage, latency, market impact, queue position, fees, dividends or borrow fees. Paper-only accounts get IEX data [1]. Fractional shares and extended/overnight hours supported, but fractional orders can't be shorted [2] | **Use** |
| Tradier sandbox | Needs a Tradier brokerage account [3] | Data 15 min delayed, no streaming [3] | No |
| IBKR paper | Free trial has delayed data; the full paper account with real-time data comes with a real account [4] | Real-time fills; API needs a persistent TWS/Gateway session [5] | Too heavy |
| Webull, Robinhood, tastytrade, Public | No paper mode through their APIs [5] | — | No |
| Own simulator on free quotes | Free | As good as our model | **Use as overlay** |

**Shadow cost model (applied to every Alpaca fill and every backtest):**
- **Spread:** buys fill at the ask and sells at the bid, using at least max(IEX spread, 1 bp, $0.01). IEX quotes can be wider or staler than the NBBO.
- **Slippage and impact:** add `0.25·spread + 0.5·σ_1min·sqrt(q/V_1min)`, then calibrate against real fills.
- **Latency:** price against the quote 500 ms after the decision.
- **Partial fills:** at most 5% of 1-minute volume.
- **Limit orders:** fill only when the market trades **through** the limit, which crudely models queue position.
- **Fees and shorts:** SEC/TAF fees on sells; names that aren't easy to borrow are blocked.

**Live switch:** the engine only calls `BrokerAdapter` (submit, cancel, positions, account, stream_fills). Alpaca paper and live share the same API. The shadow model keeps running after the switch, and modelled-versus-real slippage becomes the calibration metric. Alpaca paper balances can't be edited [1], so Kaibot keeps its own ledger at the user's onboarding amount (converted from € once) and caps buying power at it.

**PDT status (Nov 2026):**
- The SEC approved FINRA's rewrite on 14 Apr 2026, effective **4 Jun 2026**. The $25k minimum and day-trade counting are gone. Margin accounts above $2,000 get risk-based intraday buying power. Brokers have until 20 Oct 2027 to implement it [6][7].
- Alpaca has already lifted PDT: 4x intraday buying power from $2,000, intraday margin calls, and pre-trade rejection of orders that would create a deficit. Repeatedly missing margin calls can bring a 90-day restriction [8].
- **Why it matters later, not now:** a $5k live margin account can now day-trade freely, but the engine must respect margin calls. Cash accounts are still limited by settled funds. Paper never enforced any of this.

## B) Evidence on strategies

**1. Reversal ("loser hunter").**
- Moves **without** news reverse and moves **with** news drift further, for both headline news [9] and analyst-report events [10].
- Low-turnover losers reverse; high-turnover losers keep falling [11]. The biggest intraday losers usually have abnormal volume, so they fall in the wrong bucket.
- After costs, weekly reversal earns about 30–50 bp/week, but only in large caps with turnover-aware construction [12].
- **Rule:** buy liquid large- or mid-cap stocks whose drop is explained by market beta, with no stock-specific news and normal volume. Use fundamentals as a value-trap filter, not as a timing signal.

**2. Intraday momentum, ORB, VWAP.**
- **First half-hour → last half-hour:** SPY's first-half-hour return predicts its last half-hour, most strongly on volatile, high-volume and macro-news days [13]. First-half-hour changes in StockTwits sentiment predict it too [14]. This is a market-timing signal for one trade a day, not a stock picker.
- **Opening-range breakout:** a 5-minute ORB on "Stocks in Play" reports a net Sharpe ratio of 2.81 (2016–2023) [15]. An independent replication of the index version found the gross edge but **~zero net** once spread and slippage were charged [16]. Its numbers are an upper bound.
- **VWAP:** the published evidence is trend-following on QQQ, net of commissions only [17]. VWAP mean reversion for single stocks is **unproven**.

**3. PEAD and news.**
- Classic post-earnings drift has been absent in large caps since 2006 [18].
- What remains is news continuation. An earnings or news shock is a reason **not** to buy the dip.

**4. LLM and sentiment.**
- **GPT-4 headline scores predict returns** [19]:
  - The strategy earns 34 bp/day before costs, and becomes **unprofitable at 20 bp round-trip**.
  - Its Sharpe ratio fell from 6.5 (Q4 2021) to 1.2 (early 2024) as LLM adoption spread.
  - The edge sits mostly in the short leg and in small stocks.
  - Returns 1–15 minutes after release are insignificant; the drift runs from +15 minutes to the close.
- **Use Claude** to classify drops as news-driven or noise-driven, and to score each deduplicated story once. Don't race headlines.

**5. What kills edges, and validation.**
- **Retail outcomes:**
  - Taiwan: more than 80% of day traders lose after costs [20]; about 3% are predictably profitable [21].
  - Brazil: 97% lost money and 0.4% out-earned a bank teller [22].
- **The killers:**
  - Spread and slippage, often as large as the edge [16][19].
  - Adverse selection on limit fills.
  - Stale quotes.
  - Multiple testing: a backtest needs t > 3 to be credible [23].
- **Validation, required before a strategy goes live on the dashboard:**
  1. Walk-forward test: train on a rolling window, freeze the rules, test on the next month.
  2. Purged, embargoed k-fold cross-validation for any ML model.
  3. Report the deflated Sharpe ratio [24] and the probability of backtest overfitting [25]. Log every variant tried, because the trial count feeds both.
  4. Minimum sample `n ≥ (3σ/μ)²` trades: with a 10 bp mean and 100 bp standard deviation per trade, that is ≥ 900 trades over at least 3 months.
  5. Beat the 95th percentile of 1,000 random-entry runs.

## Honest expectations and risks
- **Targets:**
  - Plausible after costs: a net Sharpe ratio of 0.5–1.5, about +2 to +10 bp a day, with 40–50% losing days.
  - Most likely first-quarter result: around zero.
  - "+0.5–2% every day" compounds to 3.5x–150x a year, and no evidence supports it. Benchmark against SPY.
- **Main risks:**
  1. Paper fills that are too optimistic.
  2. Overfitting the agent debate.
  3. LLM-signal crowding [19].
  4. News-gap losses.
  5. Free-data outages.
  6. Behavioural shock when going live.

## Risk settings by aggressiveness level

| Setting | Conservative | Balanced | Aggressive |
|---|---|---|---|
| Risk per trade | 0.25% | 0.5% | 1.0% |
| Stop (ATR on 5-min bars) | 1.5× | 1.5× | 1.0× |
| Max position / concurrent | 10% / 3 | 15% / 5 | 25% / 8 |
| Aggregate open risk | 1% | 2% | 4% |
| Daily loss stop (flatten and halt) | −0.75% | −1.5% | −3% |
| Universe | S&P 500, ADV > $50M, spread < 5 bp | + mid caps, ADV > $20M | ADV > $10M, spread < 15 bp |
| Shorting | No | No | Easy-to-borrow whole shares |
| Strategies | No-news dip reversal | + last-30-minute momentum | + Stocks-in-Play ORB |

**Position size:**
- Rule: `qty = min(risk$/stop_distance, maxpos$/price, 5% × 1-min volume)`. Fractional shares are allowed for longs.
- Halve the size when VIX is above 25 or the stock's spread is above its 20-day median.
- All positions are flat by the close.

**Regime filters (all levels):**
- No entries in the first 5 minutes or after 15:45, except the momentum trade.
- Skip ±30 minutes around FOMC and CPI releases.
- No dip-buys in stocks with earnings, guidance or a rating change that day.
- No dip-buys when SPY is below its VWAP **and** its first-30-minute return is below −1% [13].
- Halt trading if quotes are more than 5 seconds stale.

## Evaluation metrics (daily, weekly, cumulative)
1. Net P&L, both shadow and broker-reported, against SPY buy-and-hold and against the random-entry baseline.
2. Hit rate, profit factor (target > 1.2 net), win/loss ratio, and expectancy in R and in bp.
3. Max intraday drawdown, max multi-day drawdown and the longest losing streak.
4. Sharpe ratio, deflated Sharpe ratio, probability of backtest overfitting, and trade count against the minimum sample.
5. Cost attribution: modelled versus actual slippage, and the share of gross edge lost to costs.
6. Per-strategy and per-agent attribution; agents that don't beat random are retired.

## Sources
1. Alpaca – Paper Trading docs: https://docs.alpaca.markets/docs/paper-trading
2. Alpaca – Fractional Trading docs: https://docs.alpaca.markets/docs/fractional-trading
3. Tradier – FAQ (sandbox): https://docs.tradier.com/docs/faq
4. Interactive Brokers – Free Trial: https://www.interactivebrokers.co.uk/en/trading/free-trial.php
5. TradersPost – Broker paper trading comparison: https://blog.traderspost.io/article/broker-paper-trading-comparison
6. Schwab – SEC approves scrapping $25,000 day trader minimum: https://www.schwab.com/learn/story/sec-approves-scrapping-25000-day-trader-minimum
7. Federal Register, 17 Apr 2026, FINRA filing: https://www.federalregister.gov/documents/2026/04/17/2026-07485/self-regulatory-organizations-financial-industry-regulatory-authority-inc-notice-of-filing-of
8. Alpaca – FINRA retires the PDT rule: https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/
9. Chan (2003), JFE, "Stock price reaction to news and no-news": https://econpapers.repec.org/RePEc:eee:jfinec:v:70:y:2003:i:2:p:223-260
10. Savor (2012), JFE, "Stock returns after major price shocks": https://econpapers.repec.org/RePEc:eee:jfinec:v:106:y:2012:i:3:p:635-659
11. Medhat & Schmeling (2022), RFS, "Short-term momentum": https://econpapers.repec.org/RePEc:oup:rfinst:v:35:y:2022:i:3:p:1480-1526.
12. de Groot, Huij & Zhou (2012), JBF, "Another look at trading costs and short-term reversal profits": https://econpapers.repec.org/RePEc:eee:jbfina:v:36:y:2012:i:2:p:371-382
13. Gao, Han, Li & Zhou (2018), JFE, "Market intraday momentum": https://econpapers.repec.org/RePEc:eee:jfinec:v:129:y:2018:i:2:p:394-414
14. Renault (2017), JBF, "Intraday online investor sentiment": https://ideas.repec.org/a/eee/jbfina/v84y2017icp25-40.html
15. Zarattini, Barbon & Aziz (2024), "A profitable day trading strategy for the U.S. equity market": https://ideas.repec.org/p/chf/rpseri/rp2498.html
16. Independent ORB replication (practitioner blog, lower reliability): https://www.mql5.com/en/blogs/post/776235
17. Zarattini & Aziz, "VWAP: the holy grail for day trading systems": https://concretumgroup.com/volume-weighted-average-price-vwap-the-holy-grail-for-day-trading-systems/
18. Martineau (2022), CFR, "Rest in peace post-earnings announcement drift": https://ideas.repec.org/a/now/jnlcfr/104.00000122.html
19. Lopez-Lira & Tang, "Can ChatGPT forecast stock price movements?" (v6, Oct 2025): https://arxiv.org/abs/2304.07619
20. Barber, Lee, Liu & Odean, "Do individual day traders make money?" (via CXO): https://www.cxoadvisory.com/individual-investing/do-day-traders-make-money/
21. Barber, Lee, Liu & Odean (2020), RAPS, "Learning, fast or slow": https://econpapers.repec.org/RePEc:oup:rasset:v:10:y:2020:i:1:p:61-93.
22. Chague, De-Losso & Giovannetti (2019), "Day trading for a living?": https://ideas.repec.org/p/spa/wpaper/2019wpecon47.html
23. Harvey, Liu & Zhu (2016), RFS, "…and the cross-section of expected returns": https://nber.org/papers/w20592
24. Bailey & López de Prado (2014), "The deflated Sharpe ratio": https://papers.ssrn.com/abstract=2460551
25. Bailey, Borwein, López de Prado & Zhu, "The probability of backtest overfitting": https://papers.ssrn.com/abstract=2326253
