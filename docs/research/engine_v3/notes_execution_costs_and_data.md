# Execution costs, Alpaca order handling and free data for honest multi-day backtests (Kaibot engine v3)

Research date: 12 November 2026. Scope: US stocks and ETFs, long-only retail account of $5k to $100k on Alpaca, orders of $2k to $20k in names with more than $50M average daily dollar volume (ADV).

Reading conventions used below:
- "bp" = basis point of traded notional. "Full spread" = ask minus bid; one marketable order pays about half of it; a round trip (one buy, one sell) pays about one full effective spread.
- Anything under "Inferences" is my own arithmetic or reasoning from the cited facts and is not itself a sourced figure.
- Several source pages were read through an automated page reader; where a number could not be confirmed on the page it is listed under Gaps instead.

---

## 1. What a small marketable order in a liquid US stock really costs (spreads, price improvement, Rule 605, Alpaca routing)

### Takeaway
The best public evidence puts the full effective spread for retail-size marketable orders handled by wholesalers at about 4.6 bp in S&P 500 stocks (so about 2.3 bp per side, 4.6 bp per round trip) and about 16 bp in the next-largest size tercile, with wholesalers filling at 36% to 56% of the quoted spread. Alpaca sends all of its equity flow to four wholesalers and is paid 12% of the quoted spread on marketable orders; no Alpaca-specific execution-quality statistic was found.

### Cited Findings

Rule 605 evidence by stock group (Dyhrberg, Shkilko and Werner, "The Retail Execution Quality Landscape", 4 July 2023; Rule 605 reports January 2019 to March 2022; liquidity-demanding orders of 100 shares or more; about 8,165 non-ETF stocks). Wholesaler figure first, exchange figure second — [NBER conference paper](https://conference.nber.org/conf_papers/f187847/f187847.pdf):

| Group | Quoted spread (bp) | Effective spread (bp) | Effective / quoted | Price impact (bp) | Realized spread (bp) | Shares price-improved (%) |
|---|---|---|---|---|---|---|
| S&P 500 | 8.28 vs 5.53 | 4.62 vs 5.22 | 0.56 vs 0.94 | 3.41 vs 6.45 | 1.21 vs -1.23 | 75.07 vs 12.28 |
| Tercile 1 (largest non-S&P) | 24.16 vs 15.53 | 15.96 vs 14.70 | 0.66 vs 0.95 | 11.77 vs 17.81 | 4.19 vs -3.10 | 68.96 vs 11.71 |
| Tercile 2 | 58.72 vs 40.65 | 42.84 vs 38.72 | 0.73 vs 0.95 | 28.72 vs 41.08 | 14.12 vs -2.36 | 63.94 vs 9.06 |
| Tercile 3 (smallest) | 118.46 vs 95.17 | 93.63 vs 92.75 | 0.79 vs 0.97 | 41.08 vs 88.10 | 33.09 vs 4.65 | 62.68 vs 7.26 |

- In S&P 500 names, wholesalers handed back 44% of the quoted spread as price improvement against 6% on exchanges; across the full sample the wholesaler effective/quoted ratio was 0.76 and 92.98% of wholesaler shares executed at or inside the NBBO — [NBER conference paper](https://conference.nber.org/conf_papers/f187847/f187847.pdf)
- Order-size buckets are not reported in that paper; odd lots (under 100 shares) were excluded, and only a footnote says industry data suggest odd-lot quality resembles the 100 to 499 share bin — [NBER conference paper](https://conference.nber.org/conf_papers/f187847/f187847.pdf)
- June 2025 Rule 605 filings, as tabulated by Global Trading: the six biggest retail wholesalers executed 68.1 billion shares and reported $395.3M of price improvement (May 2025: $357.7M). Median share-weighted effective/quoted ratios were Citadel 0.385, Virtu 0.455, Susquehanna 0.365, Hudson River 0.355, Two Sigma 0.535, Jane Street 0.515 — [Global Trading](https://www.globaltrading.net/?p=39430)

Experimental evidence (Schwarz, Barber, Huang, Jorion and Odean, "The 'Actual Retail Price' of Equity Trades", published in the Journal of Finance vol. 80 no. 5, 2025, pp. 2507-2541 per [RePEc](https://ideas.repec.org/a/bla/jfinan/v80y2025i5p2507-2541.html)):
- Design: about 85,000 simultaneous market orders worth roughly $16M, placed from 21 December 2021 to 9 June 2022 in six accounts at five brokers; main order size about $100, with $1,000 and $5,000 robustness runs giving similar results; 128 stocks stratified by price, size, volatility and liquidity plus mega caps and "retail favourites"; every buy was sold about 30 minutes later; all trades fell between 9:40 and 15:50 ET — [CQA slides by Xing Huang](https://cqa.org/wp-content/uploads/2024/04/Huang_Actual-Retail-Price_CQA.pdf)
- The sample is much less liquid than Kaibot's universe: mean quoted spread 0.64% of price, median 0.28%, 10th percentile 0.03% — [CQA slides](https://cqa.org/wp-content/uploads/2024/04/Huang_Actual-Retail-Price_CQA.pdf)
- Round-trip cost and share of the NBBO spread returned as price improvement, by broker — [CQA slides](https://cqa.org/wp-content/uploads/2024/04/Huang_Actual-Retail-Price_CQA.pdf):

| Broker | Price improvement (% of NBBO spread; 50% = midpoint) | Round-trip cost | PFOF (cents/share) |
|---|---|---|---|
| TD Ameritrade | 47.2% | -7.2 bp | 0.099 |
| E*Trade | 36.1% | -19.7 bp | 0.180 |
| Fidelity | 35.8% | -23.4 bp | 0.000 |
| Robinhood | 26.8% | -31.4 bp | 0.215 |
| IBKR Lite | 19.5% | -44.4 bp | n.a. |
| IBKR Pro | 18.8% | -46.2 bp | 0.000 |
| Always at NBBO (worst case) | 0% | -61.9 bp | n.a. |

- Broker identity, not venue, drove the gaps: TD beat Robinhood by 22.5 percentage points of spread even when both orders went to the same market center, and PFOF size explained little — [CQA slides](https://cqa.org/wp-content/uploads/2024/04/Huang_Actual-Retail-Price_CQA.pdf)
- Alpaca was not one of the tested brokers (TD Ameritrade, Fidelity, E*Trade, Robinhood, two Interactive Brokers tiers) — [Olin Business School news](https://olin.washu.edu/about/news-and-media/news/2022/10/study-finds-astonishing-differences-in-brokers-pricing-of-stock-trades.php)

Rule 605 amendments:
- Adopted 6 March 2024 (Release 34-99679): reporting extends beyond market centers to larger customer-facing broker-dealers; new size categories capture fractional, odd-lot and larger orders; timestamps and time-to-execution move to milliseconds; realized spreads are computed at several horizons; every reporter must publish a summary report — [SEC](https://sec.gov/rules-regulations/2025/09/disclosure-order-execution-information)
- Compliance was pushed from 14 December 2025 to 1 August 2026 by an order of 30 September 2025 (Release 34-104147) — [SEC](https://sec.gov/rules-regulations/2025/09/disclosure-order-execution-information)
- Robinhood's chief economic advisor wrote on 28 September 2026 that the amended regime started on 1 August 2026, and argued the new broker-level averages cannot rank brokers because customer mix, security mix and non-covered (for example fractional) flow differ — [Robinhood policy blog](https://robinhood.com/us/en/policy/blog/rule-605-and-the-limits-of-efficacy/)
- Separately, the half-penny tick and lower access-fee cap (Rules 612 and 610(c)) are still not in force: an SEC order of 11 June 2026 (Release 34-105656) moved compliance to the first business day of November 2027 — [SEC order](https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf)

Alpaca routing and payment for order flow (Rule 606 report, April to June 2026):
- All held non-directed equity orders went to four wholesalers. S&P 500 flow in June 2026: Citadel 45.7%, Virtu 21.7%, Jane Street 17.1%, GTS 15.5% — [Alpaca 606 report Q2 2026](https://files.alpaca.markets/disclosures/library/SEC+606a1+-+2026Q2.pdf)
- Alpaca customers mostly use market orders: S&P 500 order mix in June 2026 was 56.1% market, 14.7% marketable limit, 24.8% non-marketable limit, 4.4% other — [Alpaca 606 report Q2 2026](https://files.alpaca.markets/disclosures/library/SEC+606a1+-+2026Q2.pdf)
- Payment terms disclosed for all four venues: for marketable orders filled in the regular session Alpaca is paid 12% of the spread per share, capped at 5 cents a share; resting orders priced at $1 or more earn 20 mils a share; extended-hours fills earn nothing — [Alpaca 606 report Q2 2026](https://files.alpaca.markets/disclosures/library/SEC+606a1+-+2026Q2.pdf)
- Realized payment on S&P 500 market orders ran from about 73 to 167 cents per hundred shares across venues and months (April to June 2026), 55 to 113 cents on marketable limits and about 25 cents on non-marketable limits; non-S&P 500 market orders paid about 12 to 22 cents per hundred shares — [Alpaca 606 report Q2 2026](https://files.alpaca.markets/disclosures/library/SEC+606a1+-+2026Q2.pdf)
- Auction fills: Virtu neither pays nor charges for opening/closing auction executions; Citadel charges 12 mils a share on closing-auction fills — [Alpaca 606 report Q2 2026](https://files.alpaca.markets/disclosures/library/SEC+606a1+-+2026Q2.pdf)

Time of day and market-cap context:
- A 2017 study of 744 US ETFs across 78 five-minute intervals found spreads widest after the open and elevated until about 10:00, tightest in late afternoon, with spread inversely related to volume (univariate R-squared 59.1%); its advice was to stay away from the open, and it saw no problem with trading at the close. No bp-by-interval table is given — [Alpha Architect summary of Rowley, Thomas and O'Hanlon](https://alphaarchitect.com/best-times-etf-investors-trade/)
- Nasdaq's economist reported small-cap average spreads drifting from about 36 bp to over 50 bp, and that a doubling of the VIX goes with roughly 50% wider spreads (October 2021) — [Nasdaq](https://www.nasdaq.com/articles/have-spreads-changed-over-time-2021-10-14)

### Inferences
- Rule 605 spreads are full spreads, so the S&P 500 wholesaler figure of 4.62 bp implies about 2.3 bp per marketable order and about 4.6 bp per round trip. Kaibot's 2.2 bp per round trip for spread therefore looks roughly half the S&P 500 average for 2019 to early 2022; it fits only the tightest mega caps (1 to 2 bp quoted). For names just above the $50M ADV floor, the tercile-1 figure (about 16 bp per round trip) is the better guide. (Assumes the standard Rule 605 definition of effective spread as twice the distance from the midpoint; the fetched page did not restate the definition.)
- The "price impact" column in Rule 605 (3.41 bp in S&P 500 stocks) is a component of the effective spread (effective = realized + impact), not an extra cost on top of it. Kaibot's 3.1 bp impact term is numerically close to this figure; if it was calibrated from such data it double counts.
- Alpaca's per-share PFOF on S&P 500 market orders in Q2 2026 (roughly 0.7 to 1.7 cents) is several times the 0.1 to 0.2 cents the Schwarz et al. brokers received in 2021-22. Schwarz et al. found PFOF explained little at those small levels, but a broker keeping 12% of the spread leaves the wholesaler less to return as price improvement, so I would not assume Alpaca fills near the midpoint. A prudent assumption until Alpaca data are checked: fills at 60% to 100% of the quoted half-spread (Robinhood/IBKR-like), not TD-like.
- The Schwarz et al. round-trip costs (7 to 46 bp) should not be transplanted to Kaibot: their median stock had a 28 bp quoted spread. The transferable result is the share of spread paid (about 53% to 81% of the quoted half-spread).
- All direct evidence is for regular-session trading between roughly 9:40 and 15:50; none of it covers the first ten minutes.

### Gaps
- No Alpaca-specific effective/quoted ratio or price-improvement statistic was found. Alpaca's own amended Rule 605 broker report (due under the August 2026 regime) and its execution-quality disclosure page were not located.
- No 2023-2026 table of quoted/effective spreads by market-cap bucket was retrieved; the newest granular breakdown is January 2019 to March 2022.
- No source gave basis-point spreads by time of day (open vs midday vs close) for large-cap stocks; only the qualitative ETF pattern was found.
- Battalio and Jennings' work on wholesaler execution quality was not retrieved.
- Odd-lot results (most $2k to $20k orders in high-priced stocks are under 100 shares) are missing from the pre-2026 Rule 605 data.

---

## 2. Market-impact models: is about 3 bp of impact plausible for a $14,000 order?

### Takeaway
No. A $14,000 order is 0.028% of volume at the $50M ADV floor and 0.003% in a $500M ADV name, which is 9 to 90 times smaller than the smallest order in the classic Almgren calibration and inside the regime where measured impact is linear and below the square-root curve. Standard models give roughly 0.5 to 1.5 bp at the floor and 0.1 to 0.5 bp for typical large caps; the cost that matters at this size is the half-spread, not impact.

### Cited Findings
- Almgren, Thum, Hauptmann and Li (2005): permanent impact I = gamma x sigma x (X/V) x (shares outstanding/V)^(1/4); realized cost against the pre-trade midpoint J = I/2 + eta x sigma x |X/(V x T)|^(3/5), with sigma daily volatility, X/V order size over daily volume and T the execution time in days. Fitted gamma = 0.314 (s.e. 0.041), eta = 0.142 (s.e. 0.0062), exponent 0.600 (s.e. 0.038); a pure square-root exponent was rejected at 95% — [Almgren et al., "Direct Estimation of Equity Market Impact"](https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf)
- Calibration sample: 29,509 Citigroup orders in S&P 500 stocks, December 2001 to June 2003, after dropping orders under 1,000 shares or under 0.25% of ADV, VWAP orders and market-on-open/close orders; median order 0.62% of ADV; the authors call the model reliable up to about 10% of ADV; regression R-squared was under 1% — [Almgren et al.](https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf)
- Their worked example for a 10%-of-ADV purchase: IBM permanent impact 20 bp and realized cost 18 to 32 bp depending on duration (half a day down to a tenth of a day); Darden 22 bp and 23 to 43 bp — [Almgren et al.](https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf)
- Bucci, Benzaquen, Lillo and Bouchaud studied about 8 million institutional metaorders (ANcerno, January 2007 to June 2010): impact grows linearly with size when the order is below roughly one thousandth of daily volume and follows a square root between about 0.1% and 10%; the square-root prefactor is about 0.4, i.e. impact is about 0.4 x sigma x sqrt(Q/V) — [Bucci et al., arXiv 1811.05230](https://arxiv.org/pdf/1811.05230)
- Goyal, Jegadeesh and Wu (JFQA; December 2025 version; NYSE and Nasdaq common stocks, 2012 to 2021) fit impact = a + lambda x sqrt(order imbalance / 10-day ADV). At 1% of ADV in large stocks they report 9.1 bp (NYSE) and 11.8 bp (Nasdaq) in continuous trading and 4.5 bp / 10.2 bp in the closing auction; a 0.5%-of-ADV large NYSE trade costs 6.4 bp intraday vs 3.2 bp at the close; a linear model understates impact for orders under about 5% of ADV — [Goyal, Jegadeesh and Wu](https://jfqa.org/wp-content/uploads/2026/01/25551_Price_Impact.pdf)
- Frazzini, Israel and Moskowitz, using about $1.7 trillion of one institution's live trades (1998 to 2016, 21 developed markets), measured average market impact just under 9 bp for trades finished within a day, about 85% of it permanent (only about 1.26 bp reverses in 24 hours) — [Alpha Architect summary](https://alphaarchitect.com/the-best-research-paper-ever-written-on-trading-costs/)
- A study of small aggressive orders in futures found the mid-price response is close to linear in order size normalized by the average size at the best quote (R-squared 0.94 or higher), with a simple estimate of half a tick times that normalized volume — [Market Impact of Small Orders, arXiv 2201.02983](https://ar5iv.arxiv.org/html/2201.02983)
- In the retail Rule 605 data the adverse-selection ("price impact") component of S&P 500 wholesaler fills was 3.41 bp, against 6.45 bp for exchange orders — [NBER conference paper](https://conference.nber.org/conf_papers/f187847/f187847.pdf)

### Inferences
Order size as a share of daily volume (Q/V) for Kaibot:

| Order | ADV $50M | ADV $500M | ADV $5B |
|---|---|---|---|
| $2,000 | 0.004% | 0.0004% | 0.00004% |
| $14,000 | 0.028% | 0.0028% | 0.00028% |
| $20,000 | 0.040% | 0.0040% | 0.00040% |

Square-root law applied naively, 0.4 x sigma x sqrt(Q/V) (an upper bound here, because measured impact is linear and lower below Q/V of about 0.1%):

| Case | sigma 1.5%/day | sigma 2.5%/day |
|---|---|---|
| $14k, ADV $50M | 1.0 bp | 1.7 bp |
| $14k, ADV $500M | 0.3 bp | 0.5 bp |
| $14k, ADV $5B | 0.1 bp | 0.2 bp |
| $20k, ADV $50M | 1.2 bp | 2.0 bp |
| $2k, ADV $50M | 0.4 bp | 0.6 bp |

- Extending the linear regime down from the crossover (impact at Q/V = 0.1% is about 1.9 bp for sigma 1.5%, then proportional to size) gives about 0.5 bp for $14k at $50M ADV and about 0.05 bp at $500M ADV.
- Scaling the Goyal et al. continuous-market figure for large stocks by the square root (ignoring the intercept) gives about 1.5 to 2.0 bp at 0.028% of ADV and 0.5 to 0.6 bp at 0.0028%. Their model is fitted to aggregate order imbalance, not single orders, so this is also an upper bound for one retail order.
- Almgren's permanent term for Q/V = 0.028% and a float-to-volume ratio of 200 is about 0.05 bp at sigma 1.5%: negligible.
- Almgren's realized cost is measured from the pre-trade midpoint, so for an order small enough to fill inside the displayed quote the "temporary impact" is the half-spread itself. Charging a spread term and a temporary-impact term on the same retail-size order double counts.
- Verdict on the Kaibot model: 3.1 bp of impact on a $14,000 order is too high by a factor of about 2 to 3 at the $50M ADV floor and by 10 or more for names trading over $500M a day. A defensible replacement is 0.5 to 1 bp per round trip near the floor and 0.1 to 0.3 bp for the liquid core, or simply zero with a wider spread term.
- The total of about 6 bp per round trip may still be about right for an average S&P 500 name, because the spread term (2.2 bp) looks too low by a similar amount (see section 1: about 4.6 bp). The decomposition is wrong more than the sum.
- Impact stops being negligible if account size grows: at $100k per order in a $50M ADV stock, Q/V is 0.2% and the square-root figure is about 2.7 bp (sigma 1.5%).

### Gaps
- Kissell's I-Star model parameters were not retrieved.
- No study was found that measures post-trade price movement caused by individual $2k to $20k retail orders; the conclusion rests on extrapolating institutional models downward and on Rule 605 aggregates.
- The Frazzini et al. cost-by-size curve (smallest buckets) was not available in the summary read.

---

## 3. Opening and closing auctions: mechanics, cut-offs, volume share and cost

### Takeaway
The closing auction is the cheap, deep venue (about 7% to 10% of daily volume, impact about half of intraday for large NYSE stocks) and Alpaca accepts close orders until 15:50 ET; the opening auction is thin (about 1% to 2% of volume) and measured as the most expensive place to trade, with Alpaca's cut-off at 9:28 ET. Auction orders pay no explicit spread, but closing prices usually print at the pre-close bid or ask, so "spread-free" is not guaranteed.

### Cited Findings
Cut-offs and mechanics:
- Alpaca `opg` orders (market- or limit-on-open) trade only in the opening auction; anything sent between 9:28 and 19:00 ET is rejected, and orders sent after 19:00 wait for the next day's open. Alpaca `cls` orders trade only in the closing auction; submissions between 15:50 and 19:00 ET are rejected and later ones roll to the next day's close. Unfilled auction orders are cancelled — [Alpaca orders documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca.md)
- Alpaca notes that on Nasdaq, orders entered from 9:28:01 to 9:29:59 do not get the Nasdaq Official Opening Price — [Alpaca orders documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca.md)
- Nasdaq Closing Cross: imbalance data start at 15:50 (every 10 seconds, then every second from 15:55) and on-close orders can no longer be cancelled or changed from 15:50; market-on-close entry ends at 15:55; limit-on-close entry runs to 15:58 with repricing against the 15:50/15:55 reference prices; imbalance-only orders are accepted until 16:00. The cross prints one price that maximizes matched shares — [Nasdaq Closing Cross FAQ](https://www.nasdaqtrader.com/content/productsservices/Trading/ClosingCrossfaq.pdf)
- NYSE close: MOC/LOC entry, change and cancellation end at 15:50; afterwards only orders offsetting a published significant imbalance are accepted; imbalance data update every second to the close. NYSE open: order entry from 6:30, imbalance data from 8:00, entry and cancellation allowed until the designated market maker opens the stock from 9:30 — [NYSE auctions fact sheet](https://www.nyse.com/publicdocs/nyse/markets/nyse/NYSE_Opening_and_Closing_Auctions_Fact_Sheet.pdf)

Volume share:
- Closing auctions: about 9% of US daily volume on a typical 2024 day (about $50 billion notional), about 20% on rebalance and options-expiry days, over 34% on the 28 June 2024 Russell reconstitution — [BMLL](https://www.bmlltech.com/news/market-insight/into-the-close-unpacking-u-s-closing-auction-dynamics-and-the-impact-of-the-russell-reconstitution)
- Earlier history: 3.1% of volume in 2010 rising to 7.5% in 2018 — [Bogousslavsky and Muravyev, Journal of Financial Markets 2023](https://ideas.repec.org/a/eee/finmar/v66y2023ics1386418123000502.html)
- 2012-2021 averages per stock: closing auction 7.10% of ADV (median 5.34%); opening auction 1.58% (median 0.92%), under 1.5% since 2015 — [Goyal, Jegadeesh and Wu](https://jfqa.org/wp-content/uploads/2026/01/25551_Price_Impact.pdf)
- Nasdaq's own FAQ puts its closing cross at almost 10% of Nasdaq average daily volume — [Nasdaq Closing Cross FAQ](https://www.nasdaqtrader.com/content/productsservices/Trading/ClosingCrossfaq.pdf)

Cost evidence:
- Closing prices usually coincide with the pre-close bid or ask, deviations from pre-close levels revert quickly and almost fully, and price impact at the close is lower than in continuous trading — [Bogousslavsky and Muravyev](https://ideas.repec.org/a/eee/finmar/v66y2023ics1386418123000502.html)
- Impact at 1% of ADV (bp), 2012-2021 — [Goyal, Jegadeesh and Wu](https://jfqa.org/wp-content/uploads/2026/01/25551_Price_Impact.pdf):

| Size group | Close NYSE | Close Nasdaq | Continuous NYSE | Continuous Nasdaq | Open NYSE (derived) | Open Nasdaq (derived) |
|---|---|---|---|---|---|---|
| Large | 4.5 | 10.2 | 9.1 | 11.8 | 13.4 | 66 |
| Small | 7.4 | 12.5 | 15.1 | 16.0 | 17.0 | 114 |
| Micro | 18.2 | 44.7 | 21.3 | 22.1 | 33.4 | 159 |

  (Opening figures were computed by the page reader from the paper's Table 5 coefficients rather than quoted; the paper itself calls opening auctions illiquid and bases them on the 32.8% of stocks with a 9:28 pre-market price.)
- The same paper's "strategic execution" benchmark (close for everything except Nasdaq micro caps) lowers annual trading cost of a momentum long/short portfolio from 2.58% (continuous) to 1.78%; closing-only costs 2.05%, or 1.13% without micro caps — [Goyal, Jegadeesh and Wu](https://jfqa.org/wp-content/uploads/2026/01/25551_Price_Impact.pdf)
- Spreads in ETFs stay elevated from the open until about 10:00 — [Alpha Architect](https://alphaarchitect.com/best-times-etf-investors-trade/)
- Wholesaler terms for Alpaca flow: auction fills earn Alpaca no payment at Virtu and cost 12 mils a share at Citadel for the close — [Alpaca 606 report Q2 2026](https://files.alpaca.markets/disclosures/library/SEC+606a1+-+2026Q2.pdf)

### Inferences
- A $14k order is a vanishing fraction of a large-cap closing auction (7% of a $50M ADV is $3.5M), so its own impact on the closing price is effectively nil. The cost of using the close is the imbalance-driven tilt: when the auction prints at the pre-close ask on a buy-imbalance day, a buyer pays the half-spread; on a sell-imbalance day the buyer gains it. For a strategy whose buys are uncorrelated with index-rebalance flow the expected cost is between zero and one half-spread. Momentum-style buys may correlate with closing buy imbalances, so half a spread per side is the conservative backtest setting.
- Using the close requires the decision by 15:50 ET at Alpaca. A backtest that computes the signal from the official close and also fills at that close has look-ahead; honest versions compute the signal from the 15:45-15:49 price and fill at the official close, or fill next day.
- The open is the wrong default: thin auction, widest spreads, and no direct evidence of spread-free fills. If a strategy must act on overnight information, a marketable limit placed after about 9:45-10:00 is better supported than a market-on-open order.
- Minute bars from the free IEX feed cannot contain the primary-exchange auction prints, because IEX is one venue; the SIP bar at 9:30 and the daily SIP close are the right proxies for auction prices. Alpaca's own example shows IEX carried under 2% of AAPL's consolidated volume on one day (923,134 vs 51,861,083 shares) — figures from the [Alpaca market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).
- Auction days to treat separately in backtests: index rebalances, quarterly expiries and the June Russell reconstitution, when the close takes 20% to 34% of volume and closing dislocations are larger.

### Gaps
- No study was found that measures, in basis points, the cost of a retail market-on-open order versus a marketable order at 9:35, 9:45 or 10:00 in large caps for 2023-2026.
- The Nasdaq opening-cross quick guide was unavailable (retired page), so Nasdaq opening cut-offs come only from Alpaca's note.
- The basis-point size of closing-price deviations from the pre-close midpoint (Bogousslavsky and Muravyev) was not on the abstract page.
- An Alpaca forum thread titled "Recently Seeing Partial Fills on Market OPG order (S&P500 stocks)" exists but was not read; partial fills on live `opg` orders are a possible issue to check — [Alpaca forum](https://forum.alpaca.markets/t/recently-seeing-partial-fills-on-market-opg-order-s-p500-stocks/13906)
- Whether Citadel's 12-mil closing-auction charge is ever passed to the customer is not stated.

---

## 4. Alpaca specifics as of 2026: order types, stops, fractional limits, PDT, fees, rate limits, paper simulator, data plans

### Takeaway
Alpaca supports everything a multi-day long-only engine needs (GTC stops that last 90 days, auction orders, brackets), but fractional orders are day-only, bracket and trailing stops ignore extended hours, and a gapped stop becomes a market order at the open. The pattern-day-trader rule ended on 4 June 2026. Explicit costs are about 0.2 to 0.4 bp per round trip. The paper simulator fills at the NBBO with unlimited size, no queue, no price improvement, no fees and no dividends.

### Cited Findings
Orders — all from the [Alpaca orders documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca.md):
- Types: market, limit, stop, stop-limit, trailing stop; order classes bracket, OCO and OTO.
- Time in force: `day`, `gtc`, `opg`, `cls`, `ioc`, `fok`.
- GTC orders are cancelled automatically 90 days after creation (job runs 16:15 ET on the `expires_at` date).
- A sell stop turns into a market order once the stop price trades; stops and trailing stops are triggered by consolidated-tape prints that are not outside the NBBO. A stop-limit that is gapped through stays live as a limit order until it can fill or is cancelled.
- Trailing stops (dollar or percent) follow the high-water mark, accept only `day` or `gtc`, never trigger outside regular hours, and cannot be legs of bracket/OCO orders.
- Brackets need `day` or `gtc`, are not eligible for extended hours, and only arm the exit legs after the entry is completely filled; the stop leg shrinks when the take-profit partially fills.
- Extended sessions: pre-market 4:00-9:30, after-hours 16:00-20:00, overnight 20:00-4:00 ET (Sunday to Friday). Only limit orders with `day` or `gtc` and `extended_hours=true` are accepted there.
- Fractional and notional orders are limited to `day` time in force (market, limit, stop, stop-limit); `gtc`, `opg`, `cls`, `ioc` and `fok` are not available for them, and notional orders cannot be modified in place.
- Prices at or above $1 may have at most two decimals.
- Corporate actions on resting orders: prices of non-marketable GTC limit orders may be adjusted; bracket child legs carry do-not-reduce/do-not-cancel flags and are left alone; trailing stops may be cancelled or adjusted around splits at Alpaca's discretion.
- The page describes no price collar or market-to-limit conversion for market orders and warns that market orders can slip.

Pattern-day-trader rule:
- The SEC approved FINRA's removal of the PDT designation and the $25,000 minimum on 14 April 2026; the change took effect 4 June 2026, brokers have until 20 October 2027 to implement, and eligible margin accounts must still hold more than $2,000 — [Schwab](https://international.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum)
- Alpaca implemented it on 4 June 2026 (FINRA Regulatory Notice 26-10): day-trade counting and PDT restrictions are gone, real-time pre-trade checks reject orders that would create an intraday margin deficit, the equity needed for 4x intraday buying power fell from $25,000 to $2,000, and the old API fields (`pattern_day_trader`, `daytrade_count`, `daytrading_buying_power` and related) are removed by 6 July 2026 in favour of `buying_power` — [Alpaca blog](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/)
- Alpaca's FAQ confirms the four-trade limit no longer exists but says firms may keep house requirements — [Alpaca intraday margin FAQ](https://docs.alpaca.markets/docs/understanding-the-new-intraday-margin-rule)

Fees:
- SEC Section 31 fee: $20.60 per million dollars of sales from 4 April 2026 (it was $0.00 before that date); it stays until 60 days after the fiscal 2027 appropriation is enacted — [SEC fee rate advisory](https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2)
- FINRA trading activity fee on equity sales: $0.000195 a share, capped at $9.79 a trade, for 2026 (2024-2025: $0.000166 / $8.30; from 1 January 2027: $0.000232 / $11.61) — [FINRA fee adjustment schedule](https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule)
- Alpaca passes through the SEC fee and TAF on sells only and a CAT fee on both buys and sells, rounds fees up to the next cent, and debits them at the end of the trading day — [Alpaca regulatory fees documentation](https://docs.alpaca.markets/us/docs/regulatory-fees); [Alpaca support](https://alpaca.markets/support/regulatory-fees)

Rate limits and data plans:
- Trading API: 200 requests a minute per account, HTTP 429 beyond that, not user-adjustable — [Alpaca support](https://alpaca.markets/support/usage-limit-api-calls)
- Market data plans — [Alpaca market data documentation](https://docs.alpaca.markets/us/docs/about-market-data-api.md):

| | Basic (free) | Algo Trader Plus |
|---|---|---|
| Price | $0 | $99 a month |
| Real-time equities | IEX only | all US exchanges (SIP) |
| WebSocket symbols | 30 | unlimited |
| Historical queries | must end at least 15 minutes ago | no restriction |
| Historical API calls | 200 a minute | 10,000 a minute |
| History start | 2016 | 2016 |

Paper trading:
- Paper orders never reach a venue; they are matched against the real-time NBBO, only once marketable; order size is not compared with quoted size; a random partial fill happens about 10% of the time; the starting balance is $100,000 — [Alpaca paper trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)
- Not modelled, per Alpaca's own list: market impact, information leakage, latency slippage, queue position of resting limits, price improvement, regulatory fees and dividends; borrow fees are also absent — [Alpaca paper trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)
- An Alpaca employee stated in December 2025 that paper and live fills are both driven by full SIP quotes (buys against the ask, sells against the bid, quotes not trades), that the data subscription does not change paper fills, and that the simulator ignores volatility and liquidity — [Alpaca forum](https://forum.alpaca.markets/t/paper-trading-fill-delays-of-50-260-seconds-limit-orders-filled-minutes-after-price-crossed/18223)
- Alpaca's support page adds only that paper fills are simulated from real-time quotes and can differ from live in active markets — [Alpaca support](https://alpaca.markets/support/difference-paper-live-trading)

### Inferences
- Explicit cost of a $14,000 sale in 2026: SEC fee $0.29 (0.21 bp) plus TAF of about $0.02 for roughly 90 shares, plus CAT cents on each side: about 0.23 to 0.25 bp per round trip. Kaibot's 0.3 bp is slightly conservative. For $2,000 orders, cent rounding lifts it to roughly 0.4 bp. For backtest years before April 2026 the SEC fee differed (it was zero immediately before), so a flat 0.3 bp is a simplification.
- Overnight stops: a GTC sell stop persists for up to 90 days but only triggers in the regular session, so after a gap down it fires on the first qualifying print and sells at the market near the open. Backtests must fill gapped stops at the open price, never at the stop price. A stop-limit avoids the bad fill but can leave the position unprotected.
- Fractional positions cannot carry GTC stops or use auction orders. For a multi-day engine, either trade whole shares only or re-submit day stops every morning.
- Bracket legs are not adjusted for dividends or splits; engine-managed stops (or cancel/replace around ex-dates) are safer for multi-week holds.
- Paper fills marketable orders at the far side of the NBBO, i.e. full quoted half-spread with no price improvement. For liquid names that is probably slightly pessimistic midday relative to live wholesaler fills, but paper understates everything else: resting limits fill the moment the quote touches, size is unlimited, there are no fees, and multi-day P&L omits dividends. Paper results for limit-order strategies are therefore optimistic; for market-order strategies they are roughly fair on price but miss fees and dividends.
- With no PDT rule, the remaining account constraints for long-only unlevered trading are buying power and settlement; same-day exits (for example a stop on entry day) no longer risk a lock-out.
- The free data plan is workable for daily-decision strategies: SIP history delayed 15 minutes is enough for backtests and end-of-day signals, while live quotes for order pricing would come from IEX only (thin) unless the $99 plan is bought.

### Gaps
- Alpaca's brokerage fee schedule PDF (exact CAT fee per share, any other pass-throughs, confirmation of zero commission on US equities) was not retrieved; the documentation pages only point to it.
- Whether fee rounding is per execution, per order or per day is not clear from the pages read.
- The paper documentation does not say how `opg`/`cls` orders, stop triggers at a gap, or halts are simulated.
- How the intraday margin rule treats accounts under $2,000 and cash accounts at Alpaca was not stated on the pages read.
- No Alpaca statement on market-order price collars or limit-up/limit-down handling was found.

---

## 5. Recommended execution practice and conservative cost modelling for small daily-decision accounts

### Takeaway
The evidence supports trading in the regular session after the opening period or in the closing auction, using marketable limits rather than naked market orders, and modelling cost per side as a half effective spread scaled by liquidity bucket plus a small buffer, then stress-testing at multiples. I found no authoritative practitioner source that prescribes specific numbers, so the recommendations here are inferences from sections 1 to 4.

### Cited Findings
- Closing auctions cost about half as much as intraday trading for large NYSE stocks and opening auctions are the most expensive mechanism; a mixed policy (close for all but Nasdaq micro caps) is cheaper than either pure approach — [Goyal, Jegadeesh and Wu](https://jfqa.org/wp-content/uploads/2026/01/25551_Price_Impact.pdf)
- Turnover dominates: estimated annual trading cost is tens of basis points for slow factors but 1.1% to 2.6% for momentum and 3.4% to 6.8% for short-term reversal, which is largely unprofitable after costs — [Goyal, Jegadeesh and Wu](https://jfqa.org/wp-content/uploads/2026/01/25551_Price_Impact.pdf)
- Spreads are widest early in the session and settle by about 10:00; the study's authors advise against trading near the open and found the close acceptable — [Alpha Architect](https://alphaarchitect.com/best-times-etf-investors-trade/)
- Retail market orders sent between 9:40 and 15:50 received between 19% and 47% of the quoted spread back as price improvement depending on the broker, with similar results at $100, $1,000 and $5,000 — [CQA slides](https://cqa.org/wp-content/uploads/2024/04/Huang_Actual-Retail-Price_CQA.pdf)
- Alpaca documents no protective collar on market orders and notes they can slip in thin or fast markets — [Alpaca orders documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca.md)
- Alpaca's wholesalers pay it on marketable flow in the regular session only; extended-hours fills earn nothing and are limit-only — [Alpaca 606 report Q2 2026](https://files.alpaca.markets/disclosures/library/SEC+606a1+-+2026Q2.pdf); [Alpaca orders documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca.md)
- Alpaca lists the reasons simulated results differ from live ones: fill prices, fill and liquidity assumptions, and data sources — [Alpaca paper trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)

### Inferences
Execution:
- Entry/exit default: marketable limit, day time in force, priced a few basis points through the far quote (for example ask plus 5 to 10 bp for buys), sent after about 9:45-10:00 ET. This still routes to a wholesaler and can be price-improved, and it caps the damage from a stale or wide quote, which a market order does not.
- When the signal is computed near the end of the day, use `cls` (market-on-close) submitted by about 15:45-15:49 ET, in whole shares.
- Avoid `opg` and the first 10 to 15 minutes unless the strategy's edge depends on the opening price; if it does, model that fill pessimistically (see below).
- Protective stops: GTC stop (market) in whole shares for certainty of exit, accepting gap risk; cancel and re-place around splits and consider ex-dividend dates for tight stops.
- Skip auction use on known rebalance/expiry closes unless intended.

Cost model for backtests (per side, of notional), replacing "2.2 spread + 3.1 impact":

| Bucket (by ADV) | Half effective spread | Impact | Fees (sell side) | Per round trip |
|---|---|---|---|---|
| Mega/large, ADV over $500M | 1 to 2.5 bp | 0 to 0.3 bp | 0.25 bp | about 3 to 6 bp |
| Large/mid, ADV $100M to $500M | 2.5 to 5 bp | 0.3 to 0.5 bp | 0.25 bp | about 6 to 11 bp |
| Mid, ADV $50M to $100M | 5 to 8 bp | 0.5 to 1 bp | 0.25 bp | about 11 to 18 bp |

  These ranges are my interpolation between the S&P 500 (4.62 bp full effective spread) and tercile-1 (15.96 bp) wholesaler figures in section 1 and the impact arithmetic in section 2; they are not published figures.
- Better than buckets: estimate each stock's quoted spread from data (for example median quoted spread from Alpaca quotes, or a high-low/close-based estimator from bars) and charge 60% to 100% of the half-spread per side.
- Open fills: charge at least 2x the midday half-spread for fills in the first 15 minutes or at the opening auction.
- Stops: fill at the worse of stop price and next tradable price (the open after a gap), plus the half-spread.
- Always report results at 1x, 2x and 3x the base cost and the break-even cost per round trip; a multi-day strategy that needs costs under about 10 bp per round trip in mid caps is fragile.
- Because holding periods are days to weeks, a 6 to 12 bp round-trip cost is small against typical per-trade moves; the larger honesty risks are survivorship, look-ahead in close-based signals and gap fills, not the impact term.

### Gaps
- No practitioner or academic source was retrieved that directly compares marketable limit orders with market orders for retail flow at wholesalers (the Schwarz et al. slides show marketable-limit results but the series could not be read reliably).
- No published convention for "half-spread plus fixed slippage" defaults (for example platform default slippage models) was retrieved; the model above is my synthesis.
- No evidence was found on whether small limit orders priced at the midpoint get filled by Alpaca's wholesalers.

---

## 6. Survivorship-free universe construction with free data

### Takeaway
Free point-in-time S&P 500 membership is obtainable (Wikipedia change table back to about 2000, a maintained GitHub list from 1996, iShares holdings files from about 2006), and Alpaca staff say delisted symbols stay in its database, but Alpaca has no historical-assets endpoint, does not serve OTC history to ordinary users, and has at least one reported ticker-reuse contamination, so coverage must be tested symbol by symbol. Published examples show survivorship inflating momentum results severely (Nasdaq 100 momentum CAGR 46% down to 16.4%).

### Cited Findings
Constituent history:
- Wikipedia method: scrape the current constituents table and the changes table, then walk backwards month by month undoing additions and removals. The author's reconstruction covers 1990 onward, matched a paid dataset for the current list, and is judged fairly accurate and complete back to about 2000 and weaker before — [Robot Wealth](https://robotwealth.com/how-to-get-historical-spx-constituents-data-for-free/)
- A maintained community dataset, "Historical Lists of S&P 500 components since 1996" (GitHub fja05680/sp500), is pointed to in the comments of that article — [Robot Wealth](https://robotwealth.com/how-to-get-historical-spx-constituents-data-for-free/)
- iShares holdings downloads can be requested for past dates (files begin 31 October 2006) and carry name, CUSIP, ISIN and SEDOL; the Wikipedia page's revision history is a second point-in-time source; the SPDR holdings file has no history. Tickers are not standardized across sources (for example FB/META), so CIK is the safer join key, available on Wikipedia only from 2014 — [Riaz Arbi](https://riazarbi.github.io/quant/backtesting-sp500-constituent-history)

Alpaca and delisted names:
- An Alpaca employee wrote in March 2022 that trades and quotes are stored unadjusted, renames are not followed in that raw data, and delisted symbols remain in the database; another forum user in the same thread reported cases where delisted stocks returned nothing — [Alpaca forum](https://forum.alpaca.markets/t/are-alpaca-historical-tick-data-point-in-time/8493)
- In December 2025 Alpaca staff confirmed there is no endpoint for historical assets (one with an as-of date is planned) and explained that when a stock is delisted its symbol in the asset table is replaced by a number or given a suffix — [Alpaca forum](https://forum.alpaca.markets/t/delisted-tickers/18227)
- The bars endpoint has an `asof` date parameter (default today) that maps renamed tickers to the same entity; `asof=-` switches mapping off, and a past `asof` date lets an old ticker be queried. Mapping only works from the day after a rename — [Alpaca bars reference](https://docs.alpaca.markets/us/reference/stockbars); [Alpaca market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- OTC history needs `feed=otc` and a subscription currently limited to broker partners, so the post-delisting OTC life of a failed company is not available to a normal account — [Alpaca market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq); [Alpaca forum](https://forum.alpaca.markets/t/get-historical-data-for-inactive-stocks/10097)
- A forum thread titled "Fixing Data: Historical bars of symbol 'Q' are comprised of two different stocks" indicates reused tickers can be spliced together (title only, not read) — [Alpaca forum](https://forum.alpaca.markets/t/fixing-data-historical-bars-of-symbol-q-are-comprised-of-two-different-stocks/18184)
- Alpaca history starts in 2016 on both plans — [Alpaca market data documentation](https://docs.alpaca.markets/us/docs/about-market-data-api.md)

Alternatives:
- EODHD advertises daily history for delisted US tickers mostly from January 2000 (the page says 26,000+ in the body and 11,000+ in its metadata), listable through the exchange symbol list with `delisted=1`, and says delisted data are included in any package; free-plan limits are not stated on that page — [EODHD](https://eodhd.com/financial-academy/financial-faq/historical-stock-prices-for-delisted-companies)

Size of the bias:
- Two experiments attributed to Michael Harris: a Dow 30 trend-following rule earned 7.7% a year on survivors only and 5.6% once delisted members were included; a Nasdaq 100 momentum rule fell from 46% CAGR to 16.4% and its maximum drawdown rose from 41% to 83% — [Quantified Strategies via Pulse, 29 May 2026](https://www.pulse.bot/stock-trading/news/survivorship-free-momentum-and-trend-following-strategies-3e520ccc-26a2-4c5c-bdee-52299ea573f0/)

### Inferences
- Kaibot's universe ("300 of today's large caps", 2016 to 2026) is close to the worst case for momentum or trend rules: today's large caps are by construction the decade's winners, so any long rule that buys strength is flattered. The Nasdaq 100 example shows the effect can be most of the return.
- Practical free build: (1) membership by date from the Wikipedia change table cross-checked against the GitHub list and month-end iShares IVV (S&P 500) or IWB (Russell 1000) holdings files; (2) for each historical member, request Alpaca bars with `asof` set to a date inside its membership; (3) log every symbol/date range that returns nothing; (4) fill holes with daily data from another vendor or, failing that, book a conservative terminal return for names that vanish; (5) apply the liquidity screen (top N by trailing dollar volume) using only data available at each date.
- Most S&P 500 and Russell 1000 removals since 2016 were acquisitions or demotions rather than bankruptcies, so even daily-only data for the missing names removes most of the bias; minute bars for them matter less for multi-day rules.
- Quantify the bias directly: run each candidate rule on (a) today's 300 and (b) the point-in-time list, and report the gap in CAGR, Sharpe and drawdown. A rule that only works on (a) should be rejected.
- Watch for reused tickers: validate each series for price discontinuities at the membership boundaries and compare shares-times-price against known market cap where possible.

### Gaps
- GitHub could not be opened in this session, so the fja05680/sp500 file format, update date and licence are unverified.
- Free Russell 1000 membership history was not verified; iShares IWB date-specific holdings are the likely route but were not tested.
- Tiingo, Stooq, Nasdaq Data Link and the SEC company-tickers file were not checked for delisted coverage, free-tier limits or licences.
- The completeness of Alpaca's bars for delisted 2016-2026 index members is unknown and conflicting in forum reports; it needs an empirical test.
- Whether `GET /v2/assets?status=inactive` returns usable delisted symbols was not confirmed from documentation; staff describe renamed/suffixed symbols in the asset table.
- The survivorship examples come from a trading-education site reporting another author's tests, without periods or data sources; treat the magnitudes as illustrative.

---

## 7. Free historical earnings-announcement dates and surprises with point-in-time reliability

### Takeaway
Free APIs are adequate for forward-looking calendars but shallow on history (Finnhub's free tier: one month of calendar, four quarters of surprises), and vendor calendars carry look-ahead risk because they store the latest record rather than what was known at the time. The filing time of the earnings 8-K (Item 2.02) on EDGAR is the most defensible free point-in-time timestamp, but I could not verify EDGAR access details in this session.

### Cited Findings
- Finnhub free plan: 60 API calls a minute, US coverage only, earnings calendar limited to one month, EPS surprises limited to four quarters, company news one year; deeper history is a paid feature — [NexusFi data-provider profile](https://nexusfi.com/d/data-providers/finnhub/)
- Alpha Vantage `EARNINGS_CALENDAR` returns upcoming scheduled reports only, over a 3-, 6- or 12-month horizon, as CSV with symbol, name, report date, fiscal period end, EPS estimate and currency; its separate `EARNINGS` function returns past quarterly and annual EPS with analyst estimates and surprise amounts — [Macroption](https://www.macroption.com/alpha-vantage-earnings-calendar/)
- Pitfalls flagged in a May 2026 guide: free tiers differ in quota, history, coverage and usage rights; feeds that keep only the latest record create look-ahead in backtests; "before market"/"after market" flags are session labels rather than timestamps; and definitions of adjusted EPS and surprise differ between vendors — [QVeris guide](https://qveris.ai/guides/free-earnings-calendar-api/)
- Company practice (30 firms from a Silicon Valley 150 sample, four quarters): earnings dates were announced on average about 19 to 22 days ahead (most often three weeks, minimum 6 to 10 days), and every company furnished its Item 2.02 Form 8-K shortly after the market close on the day of the release — [JD Supra](https://production.jdsupra.com/legalnews/snapshot-earnings-date-announcements-3291029)
- A free lookup tool for earnings 8-Ks on EDGAR exists (title only, not read) — [edgar.tools](https://app.edgar.tools/tools/earnings-8k)

### Inferences
- For backtests from 2016, build the earnings-date history from EDGAR: for each company (by CIK), list 8-K filings containing Item 2.02 and use the filing acceptance timestamp. Filed before 9:30 ET means the same session reacts; filed after 16:00 ET means the next session is the first tradable reaction. This avoids vendor revisions because the filing time is fixed at submission.
- Rule for honest multi-day tests: define "announcement session" as the first regular session that opens after the timestamp, and forbid any entry decision that uses that session's close before it has occurred. When only a date is known (no time flag), assume the worst case for the strategy.
- "Avoid holding through earnings" rules need the scheduled date known in advance, which a filing timestamp does not provide. With roughly three weeks' typical notice, live trading can use Finnhub or Alpha Vantage calendars; for history, a simplification is to assume the date was known 10 trading days ahead and test sensitivity.
- Start archiving daily snapshots of the free calendars now; that creates a true point-in-time record going forward at zero cost.
- Surprise history for free is limited (Finnhub four quarters; Alpha Vantage's EPS history is deeper but its estimate fields are not guaranteed point-in-time), so strategies that depend on historical surprise magnitude are hard to test honestly on free data; strategies that only need the date are feasible.

### Gaps
- EDGAR API specifics (submissions JSON, full-text search, rate limits, item tagging by year) were not fetched; the approach is standard but unverified here.
- Finnhub's own pricing page could not be read; the free-tier limits come from a third-party profile.
- Alpha Vantage's free request limit and whether `EARNINGS` returns report time of day were not confirmed.
- Nasdaq's earnings calendar and Alpaca's news API were not examined for history depth or timestamps.
- No source quantified how often vendor earnings dates are revised or wrong.

---

## 8. Corporate actions: Alpaca bar adjustment and adjusted-versus-raw pitfalls

### Takeaway
Alpaca bars default to raw prices and can be adjusted on request for splits, dividends and spin-offs; live and latest-price endpoints are always raw. Multi-day backtests should compute returns on fully adjusted data but size orders, set stops and screen liquidity on raw prices, and resting stops need manual care around splits and ex-dividend dates.

### Cited Findings
- The historical bars endpoint takes an `adjustment` parameter with values `raw` (the default), `split`, `dividend`, `spin-off` and `all`, combinable as a comma-separated list; default feed is `sip`; up to 10,000 data points per page across all requested symbols — [Alpaca bars reference](https://docs.alpaca.markets/us/reference/stockbars)
- Latest-bar and snapshot endpoints have no adjustment option and return data as originally received; streaming clients must re-subscribe to the new symbol after a rename — [Alpaca market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- Historical trades and quotes are not adjusted for corporate actions — [Alpaca forum](https://forum.alpaca.markets/t/are-alpaca-historical-tick-data-point-in-time/8493)
- Bars are built from trades using condition-code rules (for example odd-lot trades count toward volume but not open/high/low/close), and a minute with no qualifying trade produces no bar — [Alpaca market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- Resting orders: non-marketable GTC limit prices may be adjusted for corporate actions, bracket legs are flagged do-not-reduce/do-not-cancel and are not adjusted for dividends or other actions, and trailing stops may be cancelled or re-priced around splits at Alpaca's discretion — [Alpaca orders documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca.md)
- Paper accounts do not simulate dividends — [Alpaca paper trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)

### Inferences
- Use `adjustment=all` for anything that compares prices across days (returns, moving averages, breakouts, ATR). With raw bars, a split looks like a crash and an ex-dividend drop looks like a small loss, both of which create false signals and false stop-outs over multi-week holds.
- Use raw prices for anything tied to the live market: share counts, limit and stop levels, dollar-volume screens and price filters. A stop level derived from back-adjusted prices is offset from the live price by the cumulative adjustment factor and must be converted before it is sent.
- Adjusted history is restated every time a new dividend or split occurs, so a cached adjusted dataset silently goes stale and will not match a fresh download. Either store raw bars plus the corporate-action factors and adjust in code, or re-download adjusted data before each research run and record the download date.
- Dividend adjustment credits the dividend to the price path; a live account receives it as cash later, and the paper account not at all. Expect paper multi-day P&L to lag an adjusted-price backtest by the dividend yield (roughly 1% to 2% a year for large caps, my estimate).
- Mixed granularity is a trap: if minute bars are raw and daily bars adjusted (or downloaded on different dates), intraday-versus-daily comparisons break on every ex-date.
- Minutes without bars mean "no trade", not "missing data"; forward-fill prices for signals but do not assume a fill was possible in that minute.
- Live stops: for bracket legs (never adjusted) and plain GTC stops (documentation only promises possible adjustment for non-marketable limits), cancel and re-create protective orders on the morning of a split, and widen or re-anchor tight stops on ex-dividend dates for high-yield names.

### Gaps
- Whether Alpaca also rescales volume when `adjustment=split` is requested was not confirmed.
- Alpaca's corporate-actions announcements endpoint (coverage, history, lead time) was not examined.
- No documentation was found stating how plain GTC stop orders (as opposed to limits and trailing stops) are treated on a split.
- Handling of spin-offs, mergers with cash/stock elections and symbol changes in open positions was not researched.
